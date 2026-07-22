from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from .aggregator import FocusAggregator
from .events import EventHub
from .infrastructure.session_store import FileSessionStore
from .models import (
    BatchAnalysis,
    ComponentHealth,
    FocusEvent,
    FocusMode,
    FocusReport,
    FocusSession,
    FocusSessionCreate,
    HealthResponse,
    SessionState,
)
from .ports import AsrPort, LlmPort, RobotPort, TtsPort, VisionPort
from .scheduler import BatchBuilder, VisionPriorityScheduler


_TERMINAL = {SessionState.COMPLETED, SessionState.CANCELLED, SessionState.FAILED}


class FocusService:
    def __init__(
        self,
        *,
        store: FileSessionStore,
        robot: RobotPort | None,
        vision: VisionPort | None,
        asr: AsrPort | None = None,
        llm: LlmPort | None = None,
        tts: TtsPort | None = None,
        demo_capture_interval: float = 10.0,
        normal_capture_interval: float = 30.0,
        demo_duration_seconds: int = 90,
        normal_duration_seconds: int = 1_500,
        batch_size: int = 4,
        voice_idle_seconds: float = 5.0,
        max_frames_per_session: int = 100,
        events: EventHub | None = None,
    ) -> None:
        if max_frames_per_session < 1:
            raise ValueError("max_frames_per_session must be positive")
        self.store = store
        self.robot = robot
        self.vision = vision
        self.asr = asr
        self.llm = llm
        self.tts = tts
        self.demo_capture_interval = demo_capture_interval
        self.normal_capture_interval = normal_capture_interval
        self.demo_duration_seconds = demo_duration_seconds
        self.normal_duration_seconds = normal_duration_seconds
        self.max_frames_per_session = max_frames_per_session
        self.batch_builder = BatchBuilder(batch_size=batch_size)
        self.events = events or EventHub(max_events=200)
        self._lock = asyncio.Lock()
        self._active: FocusSession | None = None
        self._aggregators: dict[str, FocusAggregator] = {}
        self._capture_task: asyncio.Task[None] | None = None
        self._timer_task: asyncio.Task[None] | None = None
        self._scheduler: VisionPriorityScheduler | None = None
        if vision is not None:
            self._scheduler = VisionPriorityScheduler(
                vision.analyze,
                voice_idle_seconds=voice_idle_seconds,
                on_started=self._analysis_started,
                on_completed=self._analysis_completed,
                on_paused=self._analysis_paused,
                on_dropped=self._analysis_dropped,
            )

    @property
    def active_session(self) -> FocusSession | None:
        return self._active

    async def start(self) -> None:
        self.store.cleanup_expired()
        self.store.mark_unfinished_interrupted()
        if self._scheduler is not None:
            await self._scheduler.start()
        if self.robot is not None:
            await self.robot.connect()

    async def close(self) -> None:
        await self._cancel_background_tasks()
        if self._scheduler is not None:
            await self._scheduler.stop()
        if self.robot is not None:
            await self.robot.close()

    async def create_session(
        self, request: FocusSessionCreate
    ) -> tuple[FocusSession, bool]:
        async with self._lock:
            if self._active is not None and self._active.state not in _TERMINAL:
                return self._active, True
            self.batch_builder.clear()
            if self._scheduler is not None:
                await self._scheduler.discard()
            now = datetime.now(UTC)
            duration = request.duration_seconds
            if "duration_seconds" not in request.model_fields_set:
                duration = (
                    self.demo_duration_seconds
                    if request.mode is FocusMode.DEMO
                    else self.normal_duration_seconds
                )
            session = FocusSession(
                session_id=f"fs_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}",
                mode=request.mode,
                duration_seconds=duration,
                state=SessionState.RUNNING,
                created_at=now,
                started_at=now,
            )
            self._active = session
            self._aggregators[session.session_id] = FocusAggregator()
            self.store.save_session(session)
            self._emit(session, "session.state_changed", {"state": session.state.value})
            if self.robot is not None and self.vision is not None:
                try:
                    await self.robot.warmup_camera()
                except Exception as error:
                    session.state = SessionState.FAILED
                    session.ended_at = datetime.now(UTC)
                    session.degraded_components["watcher_sdk"] = str(error)[:200]
                    self.store.save_session(session)
                    self._emit(
                        session,
                        "service.degraded",
                        {"component": "watcher_sdk", "reason": str(error)[:200]},
                    )
                    self._emit(
                        session, "session.state_changed", {"state": session.state.value}
                    )
                    return session, False
                self._capture_task = asyncio.create_task(
                    self._capture_loop(session.session_id),
                    name=f"capture-{session.session_id}",
                )
                self._timer_task = asyncio.create_task(
                    self._auto_stop(session.session_id, session.duration_seconds),
                    name=f"timer-{session.session_id}",
                )
            return session, False

    async def stop_session(self, session_id: str) -> FocusSession:
        async with self._lock:
            session = self.get_session(session_id)
            if session.state in _TERMINAL:
                return session
            session.state = SessionState.FINALIZING
            self.store.save_session(session)
            self._emit(session, "session.state_changed", {"state": session.state.value})
            await self._stop_sampling()
            tail = self.batch_builder.flush_tail()
            if tail is not None and self._scheduler is not None:
                self._scheduler.submit(tail)
                try:
                    await self._scheduler.drain(timeout=30.0)
                except TimeoutError:
                    session.degraded_components["stepfun_vlm"] = "finalize_timeout"
            session.ended_at = datetime.now(UTC)
            session.state = SessionState.COMPLETED
            session.stats = self._aggregators[session_id].snapshot()
            report = self._build_report(session)
            self.store.save_report(report)
            self.store.save_session(session)
            self._emit(session, "session.state_changed", {"state": session.state.value})
            self.store.cleanup_expired()
            return session

    async def cancel_session(self, session_id: str) -> FocusSession:
        async with self._lock:
            session = self.get_session(session_id)
            if session.state in _TERMINAL:
                return session
            await self._stop_sampling()
            self.batch_builder.clear()
            if self._scheduler is not None:
                await self._scheduler.discard()
            session.state = SessionState.CANCELLED
            session.ended_at = datetime.now(UTC)
            self.store.save_session(session)
            self._emit(session, "session.state_changed", {"state": session.state.value})
            return session

    def get_session(self, session_id: str) -> FocusSession:
        if self._active is not None and self._active.session_id == session_id:
            return self._active
        return self.store.load_session(session_id)

    def get_report(self, session_id: str) -> FocusReport:
        return self.store.load_report(session_id)

    async def set_voice_busy(self, busy: bool) -> None:
        if self._scheduler is not None:
            await self._scheduler.set_voice_busy(busy)

    async def health(self) -> HealthResponse:
        components: dict[str, ComponentHealth] = {}
        components["watcher_sdk"] = ComponentHealth(
            status="healthy"
            if self.robot is not None and self.robot.connected
            else "unhealthy",
            reason=None
            if self.robot is not None and self.robot.connected
            else "sdk_not_connected",
        )
        checks = (
            ("asr", self.asr, "qwen_asr"),
            ("ollama", self.llm, "qwen3:0.6b"),
            ("tts", self.tts, "qwen3-tts"),
            ("stepfun_vlm", self.vision, "step3-vl-focus"),
        )
        for name, component, model in checks:
            if component is None:
                components[name] = ComponentHealth(
                    status="degraded", model=model, reason="not_configured"
                )
                continue
            started = time.monotonic()
            healthy = await component.health()
            components[name] = ComponentHealth(
                status="healthy" if healthy else "degraded",
                latency_ms=round((time.monotonic() - started) * 1000),
                model=model,
                reason=None if healthy else "health_check_failed",
            )
        overall = "healthy"
        if components["watcher_sdk"].status == "unhealthy":
            overall = "unhealthy"
        elif any(component.status != "healthy" for component in components.values()):
            overall = "degraded"
        return HealthResponse(status=overall, components=components)

    async def _capture_loop(self, session_id: str) -> None:
        sequence = 0
        session = self.get_session(session_id)
        interval = (
            self.demo_capture_interval
            if session.mode is FocusMode.DEMO
            else self.normal_capture_interval
        )
        while (
            session.state is SessionState.RUNNING
            and session.captured_frames < self.max_frames_per_session
        ):
            sequence += 1
            captured_at_ms = int(datetime.now(UTC).timestamp() * 1000)
            frame_id = f"{session_id}_f-{sequence:04d}"
            destination = (
                self.store.frame_dir(session_id)
                / f"{session_id}_{sequence:04d}_{captured_at_ms}.jpg"
            )
            try:
                frame = await self.robot.capture(destination, frame_id, sequence)  # type: ignore[union-attr]
            except Exception as error:
                session.failed_frames += 1
                self._emit(
                    session, "camera.capture_failed", {"error": str(error)[:200]}
                )
                if not self.robot.connected:  # type: ignore[union-attr]
                    session.state = SessionState.FAILED
                    session.ended_at = datetime.now(UTC)
                    session.degraded_components["watcher_sdk"] = "disconnected"
                    self.store.save_session(session)
                    self._emit(
                        session,
                        "service.degraded",
                        {"component": "watcher_sdk", "reason": "disconnected"},
                    )
                    self._emit(
                        session, "session.state_changed", {"state": session.state.value}
                    )
                    break
            else:
                session.captured_frames += 1
                self.store.save_session(session)
                self._emit(
                    session,
                    "camera.frame_captured",
                    {"frame_id": frame.frame_id, "latency_ms": frame.latency_ms},
                )
                batch = self.batch_builder.add(frame)
                if batch is not None and self._scheduler is not None:
                    self._scheduler.submit(batch)
            await asyncio.sleep(interval)

    async def _auto_stop(self, session_id: str, duration: int) -> None:
        await asyncio.sleep(duration)
        session = self.get_session(session_id)
        if session.state is SessionState.RUNNING:
            session = await self.stop_session(session_id)
            await self._announce_completed_report(session)

    async def _announce_completed_report(self, session: FocusSession) -> None:
        """Speak one deterministic summary when the duration timer ends a session."""
        if self.robot is None:
            return
        if self.tts is None:
            self.mark_degraded("tts", "auto_summary_not_configured")
            return

        report = self.get_report(session.session_id)
        if report.focus_proxy_score is None:
            reply = "统计完成，但有效视觉样本不足，暂时无法评分。"
        else:
            reply = (
                f"统计完成：在位率 {report.presence_ratio * 100:.0f}% ，"
                f"手机可见率 {report.phone_visible_ratio * 100:.0f}% ，"
                f"专注趋势 {report.focus_proxy_score:.0f} 分。"
            )
        started_at = time.monotonic()
        self._emit(
            session,
            "voice.turn_started",
            {"source": "session_timer", "intent": "auto_summary"},
        )
        try:
            await self.robot.play_pcm(self.tts.synthesize(reply))
        except Exception as error:
            reason = f"auto_summary_failed: {str(error)[:160]}"
            self.mark_degraded("tts", reason)
            return

        playback_started_at = self.robot.last_playback_started_at
        first_audio_ms = (
            round((playback_started_at - started_at) * 1000)
            if playback_started_at is not None
            else None
        )
        self._emit(
            session,
            "voice.turn_completed",
            {
                "source": "session_timer",
                "intent": "auto_summary",
                "speech_to_first_audio_ms": first_audio_ms,
            },
        )

    async def _stop_sampling(self) -> None:
        current = asyncio.current_task()
        for task in (self._capture_task, self._timer_task):
            if task is not None and task is not current and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._capture_task = None
        self._timer_task = None

    async def _cancel_background_tasks(self) -> None:
        await self._stop_sampling()

    async def _analysis_completed(self, analysis: BatchAnalysis) -> None:
        session = self._active
        if session is None or session.state not in {
            SessionState.RUNNING,
            SessionState.FINALIZING,
        }:
            return
        if analysis.status == "completed":
            self._aggregators[session.session_id].add_many(analysis.observations)
            session.stats = self._aggregators[session.session_id].snapshot()
            self._emit(
                session,
                "vision.batch_completed",
                {
                    "batch_id": analysis.batch_id,
                    "latency_ms": analysis.latency_ms,
                    "model_name": analysis.model_name,
                    "observations": [
                        observation.model_dump(mode="json")
                        for observation in analysis.observations
                    ],
                },
            )
            self._emit(session, "stats.updated", session.stats.model_dump(mode="json"))
        else:
            session.failed_frames += len(analysis.observations) or 1
            reason = analysis.error or "analysis_failed"
            session.degraded_components["stepfun_vlm"] = reason[:200]
            self._emit(
                session,
                "vision.batch_failed",
                {
                    "batch_id": analysis.batch_id,
                    "status": analysis.status,
                    "error": analysis.error,
                },
            )
            self._emit(
                session,
                "service.degraded",
                {"component": "stepfun_vlm", "reason": reason[:200]},
            )
        self.store.save_session(session)

    async def _analysis_started(self, frames: list) -> None:
        if self._active is not None:
            self._emit(
                self._active,
                "vision.batch_started",
                {"frame_ids": [frame.frame_id for frame in frames]},
            )

    async def _analysis_paused(self, frames: list) -> None:
        if self._active is not None:
            self._emit(
                self._active,
                "vision.batch_paused",
                {"frame_ids": [frame.frame_id for frame in frames]},
            )

    async def _analysis_dropped(self, frames: list) -> None:
        if self._active is not None:
            self._active.dropped_batches += 1
            self.store.save_session(self._active)
            self._emit(
                self._active,
                "vision.batch_failed",
                {
                    "status": "dropped_as_stale",
                    "frame_ids": [frame.frame_id for frame in frames],
                },
            )

    def _build_report(self, session: FocusSession) -> FocusReport:
        stats = session.stats
        duration_minutes = session.duration_seconds / 60
        if stats.focus_proxy_score is None:
            summary = "本次统计有效视觉样本不足，无法计算专注趋势。以上仅供参考。"
        else:
            summary = (
                f"本次统计 {duration_minutes:.1f} 分钟，在位率 {stats.presence_ratio * 100:.0f}% ，"
                f"手机可见率 {stats.phone_visible_ratio * 100:.0f}% ，"
                f"检测到 {stats.suspected_drink_events} 次疑似杯子移动，"
                f"专注趋势指数 {stats.focus_proxy_score:.0f} 分。"
                "以上为低分辨率视觉统计，仅供参考。"
            )
        return FocusReport(
            session_id=session.session_id,
            started_at=session.started_at or session.created_at,
            ended_at=session.ended_at or datetime.now(UTC),
            captured_frames=session.captured_frames,
            analyzed_frames=stats.analyzed_frames,
            failed_frames=session.failed_frames,
            presence_ratio=stats.presence_ratio,
            phone_visible_ratio=stats.phone_visible_ratio,
            phone_transition_count=stats.phone_transition_count,
            suspected_drink_events=stats.suspected_drink_events,
            focus_proxy_score=stats.focus_proxy_score,
            summary=summary,
        )

    def _emit(self, session: FocusSession, event_type: str, data: dict) -> None:
        event = FocusEvent(
            event_id=f"evt_{uuid.uuid4().hex}",
            session_id=session.session_id,
            type=event_type,  # type: ignore[arg-type]
            data=data,
        )
        self.events.publish(event)
        self.store.append_event(event)

    def emit_voice_event(self, event_type: str, data: dict) -> None:
        if self._active is not None:
            self._emit(self._active, event_type, data)

    def mark_degraded(self, component: str, reason: str) -> None:
        if self._active is None:
            return
        safe_reason = reason[:200]
        self._active.degraded_components[component] = safe_reason
        self.store.save_session(self._active)
        self._emit(
            self._active,
            "service.degraded",
            {"component": component, "reason": safe_reason},
        )
