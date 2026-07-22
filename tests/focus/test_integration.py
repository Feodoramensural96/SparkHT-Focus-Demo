import asyncio
import json
import time
from datetime import UTC, datetime

import pytest

from focus.infrastructure.session_store import FileSessionStore
from focus.models import (
    BatchAnalysis,
    CapturedFrame,
    FocusSessionCreate,
    FrameObservation,
)
from focus.service import FocusService


class FakeRobot:
    def __init__(self) -> None:
        self.connected = False
        self.last_playback_started_at = None
        self.spoken = b""

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def warmup_camera(self) -> None:
        return None

    async def capture(self, destination, frame_id, sequence) -> CapturedFrame:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"jpeg")
        return CapturedFrame(
            frame_id=frame_id,
            captured_at=datetime.now(UTC),
            path=destination,
            sequence=sequence,
            latency_ms=1,
        )

    async def play_pcm(self, chunks) -> None:
        self.last_playback_started_at = None
        async for chunk in chunks:
            if self.last_playback_started_at is None:
                self.last_playback_started_at = time.monotonic()
            self.spoken += chunk


class FakeVision:
    def __init__(self) -> None:
        self.calls = 0

    async def health(self) -> bool:
        return True

    async def analyze(self, frames) -> BatchAnalysis:
        self.calls += 1
        return BatchAnalysis(
            batch_id=f"b-{self.calls}",
            observations=[
                FrameObservation(
                    frame_id=frame.frame_id,
                    captured_at=frame.captured_at,
                    person="present",
                    phone="not_visible",
                    cup="visible",
                    cup_motion="stable",
                    confidence=0.9,
                    evidence="一人坐在桌前，杯子可见",
                )
                for frame in frames
            ],
            model_name="fake-step3",
            latency_ms=2,
            status="completed",
        )


class GatedVision(FakeVision):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def analyze(self, frames) -> BatchAnalysis:
        self.started.set()
        await self.release.wait()
        return await super().analyze(frames)


class FakeTts:
    def synthesize(self, text):
        async def chunks():
            yield text.encode("utf-8")

        return chunks()

    async def health(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_accelerated_session_captures_two_batches_and_builds_report(
    tmp_path,
) -> None:
    robot = FakeRobot()
    vision = FakeVision()
    service = FocusService(
        store=FileSessionStore(tmp_path),
        robot=robot,
        vision=vision,
        demo_capture_interval=0.01,
    )
    await service.start()
    try:
        session, _ = await service.create_session(
            FocusSessionCreate(duration_seconds=10)
        )
        await asyncio.sleep(0.11)
        await service.stop_session(session.session_id)
        report = service.get_report(session.session_id)
    finally:
        await service.close()

    assert report.captured_frames >= 8
    assert report.analyzed_frames >= 8
    assert vision.calls >= 2
    assert report.presence_ratio == 1.0
    assert report.phone_visible_ratio == 0.0
    assert report.focus_proxy_score == 100.0
    events = [
        json.loads(line)
        for line in (tmp_path / session.session_id / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    completed = [event for event in events if event["type"] == "vision.batch_completed"]
    captured = [event for event in events if event["type"] == "camera.frame_captured"]
    assert captured[0]["data"]["frame_id"] == "f-0001"
    assert session.session_id not in captured[0]["data"]["frame_id"]
    assert len(completed) >= 2
    assert len(completed[0]["data"]["observations"]) == 4
    assert completed[0]["data"]["model_name"] == "fake-step3"


@pytest.mark.asyncio
async def test_finalization_drains_pending_batch_when_tail_has_only_one_frame(
    tmp_path,
) -> None:
    vision = GatedVision()
    service = FocusService(store=FileSessionStore(tmp_path), robot=None, vision=vision)
    await service.start()
    try:
        session, _ = await service.create_session(FocusSessionCreate())
        frames = [
            CapturedFrame(
                frame_id=f"f-{index:04d}",
                captured_at=datetime.now(UTC),
                path=tmp_path / f"f-{index:04d}.jpg",
                sequence=index,
                latency_ms=1,
            )
            for index in range(1, 6)
        ]
        assert service._scheduler is not None
        service._scheduler.submit(frames[:4])
        service.batch_builder.add(frames[4])
        await asyncio.wait_for(vision.started.wait(), timeout=1)

        stopping = asyncio.create_task(service.stop_session(session.session_id))
        await asyncio.sleep(0)
        assert not stopping.done()
        vision.release.set()
        await stopping

        report = service.get_report(session.session_id)
        assert report.analyzed_frames == 4
        assert vision.calls == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_duration_timer_speaks_exactly_one_completed_summary(tmp_path) -> None:
    robot = FakeRobot()
    service = FocusService(
        store=FileSessionStore(tmp_path),
        robot=robot,
        vision=FakeVision(),
        tts=FakeTts(),
        demo_capture_interval=10,
    )
    await service.start()
    try:
        session, _ = await service.create_session(FocusSessionCreate())
        await service._auto_stop(session.session_id, 0)
    finally:
        await service.close()

    assert service.get_session(session.session_id).state.value == "completed"
    assert robot.spoken.decode("utf-8").startswith("统计完成")
    events = [
        json.loads(line)
        for line in (tmp_path / session.session_id / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    summaries = [
        event
        for event in events
        if event["type"] == "voice.turn_completed"
        and event["data"].get("intent") == "auto_summary"
    ]
    assert len(summaries) == 1
    assert summaries[0]["data"]["source"] == "session_timer"
    assert summaries[0]["data"]["reply"].startswith("统计完成")
    assert summaries[0]["data"]["speech_to_first_audio_ms"] >= 0


@pytest.mark.asyncio
async def test_capture_loop_never_retains_more_than_configured_limit(tmp_path) -> None:
    robot = FakeRobot()
    service = FocusService(
        store=FileSessionStore(tmp_path),
        robot=robot,
        vision=FakeVision(),
        demo_capture_interval=0.001,
        max_frames_per_session=3,
    )
    await service.start()
    try:
        session, _ = await service.create_session(
            FocusSessionCreate(duration_seconds=10)
        )
        await asyncio.sleep(0.03)
        assert service.get_session(session.session_id).captured_frames == 3
        assert len(list(service.store.frame_dir(session.session_id).glob("*.jpg"))) == 3
        await service.cancel_session(session.session_id)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_stale_batch_drop_is_persisted_and_visible_as_event(tmp_path) -> None:
    service = FocusService(store=FileSessionStore(tmp_path), robot=None, vision=None)
    session, _ = await service.create_session(FocusSessionCreate())
    frame = CapturedFrame(
        frame_id="stale-frame",
        captured_at=datetime.now(UTC),
        path=tmp_path / "stale.jpg",
        sequence=1,
        latency_ms=1,
    )

    await service._analysis_dropped([frame])

    assert service.get_session(session.session_id).dropped_batches == 1
    events = [
        json.loads(line)
        for line in (tmp_path / session.session_id / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert events[-1]["type"] == "vision.batch_failed"
    assert events[-1]["data"] == {
        "status": "dropped_as_stale",
        "frame_ids": ["stale-frame"],
    }


@pytest.mark.asyncio
async def test_failed_visual_batch_degrades_only_slow_system(tmp_path) -> None:
    service = FocusService(store=FileSessionStore(tmp_path), robot=None, vision=None)
    session, _ = await service.create_session(FocusSessionCreate())
    failed = BatchAnalysis(
        batch_id="failed-batch",
        observations=[],
        model_name="step3-vl-focus",
        latency_ms=30_000,
        status="analysis_failed",
        error="request timeout",
    )

    await service._analysis_completed(failed)

    saved = service.get_session(session.session_id)
    assert saved.state.value == "running"
    assert saved.failed_frames == 1
    assert saved.degraded_components["stepfun_vlm"] == "request timeout"
    events = [
        json.loads(line)
        for line in (tmp_path / session.session_id / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [event["type"] for event in events[-2:]] == [
        "vision.batch_failed",
        "service.degraded",
    ]
