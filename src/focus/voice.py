from __future__ import annotations

import math
import sys
import time
from array import array
from collections.abc import AsyncIterator, Awaitable, Callable

from .intent import FocusIntent, match_focus_intent, normalize_zh_text
from .models import FocusSessionCreate, SessionState
from .ports import AsrPort, LlmPort, RobotPort, TtsPort
from .presentation import RobotPresentationState
from .service import FocusService


class EnergyVad:
    def __init__(
        self,
        *,
        threshold: int = 500,
        silence_chunks: int = 12,
        min_voice_chunks: int = 3,
        max_chunks: int = 240,
    ) -> None:
        self.threshold = threshold
        self.silence_chunks = silence_chunks
        self.min_voice_chunks = min_voice_chunks
        self.max_chunks = max_chunks
        self.on_voice_started: Callable[[], Awaitable[None]] | None = None
        self.threshold_provider: Callable[[], int] | None = None

    async def utterances(self, chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        buffered: list[bytes] = []
        voiced = 0
        silence = 0
        async for chunk in chunks:
            level = self._rms(chunk)
            threshold = (
                self.threshold_provider()
                if self.threshold_provider is not None
                else self.threshold
            )
            if level >= threshold:
                voiced += 1
                if (
                    voiced == self.min_voice_chunks
                    and self.on_voice_started is not None
                ):
                    await self.on_voice_started()
                silence = 0
                buffered.append(chunk)
            elif buffered:
                buffered.append(chunk)
                silence += 1
            if buffered and (
                silence >= self.silence_chunks or len(buffered) >= self.max_chunks
            ):
                if voiced >= self.min_voice_chunks:
                    yield b"".join(buffered)
                buffered, voiced, silence = [], 0, 0

    @staticmethod
    def _rms(chunk: bytes) -> int:
        if len(chunk) < 2:
            return 0
        samples = array("h")
        samples.frombytes(chunk[: len(chunk) - len(chunk) % 2])
        if sys.byteorder == "big":
            samples.byteswap()
        return math.isqrt(sum(sample * sample for sample in samples) // len(samples))


class VoiceController:
    def __init__(
        self,
        *,
        service: FocusService,
        robot: RobotPort,
        asr: AsrPort | None,
        llm: LlmPort | None,
        tts: TtsPort | None,
        vad: EnergyVad | None = None,
        focus_vad_threshold: int = 2_500,
    ) -> None:
        self.service = service
        self.robot = robot
        self.asr = asr
        self.llm = llm
        self.tts = tts
        self.vad = vad or EnergyVad()
        self.focus_vad_threshold = focus_vad_threshold
        self._idle_vad_threshold = (
            self.vad.threshold if isinstance(self.vad, EnergyVad) else None
        )
        if isinstance(self.vad, EnergyVad):
            self.vad.threshold_provider = self._vad_threshold_for_current_session
            self.vad.on_voice_started = lambda: self._show_voice_state(
                RobotPresentationState.LISTENING
            )
        self._stopping = False

    async def run(self) -> None:
        if self.asr is None:
            return
        self._stopping = False
        while not self._stopping:
            self._sync_vad_threshold()
            # The firmware pauses microphone upload while speaker playback is active.
            # Treat every utterance as a separate microphone lease: close it before
            # ASR/TTS, then reopen it for the next turn after playback completes.
            chunks = self.robot.microphone_chunks()
            utterances = self.vad.utterances(chunks)
            try:
                utterance = await anext(utterances)
            except StopAsyncIteration:
                return
            finally:
                await utterances.aclose()
                await chunks.aclose()
            if self._stopping:
                break
            await self.service.set_voice_busy(True)
            try:
                await self._show_voice_state(RobotPresentationState.THINKING)
                speech_ended_at = time.monotonic()
                try:
                    transcript = await self.asr.transcribe(utterance)
                except Exception as error:
                    self.service.mark_degraded("asr", self._failure_reason(error))
                else:
                    await self._handle_transcript_while_busy(
                        transcript, speech_ended_at=speech_ended_at
                    )
            finally:
                await self.service.set_voice_busy(False)
            if not self.robot.connected:
                return

    async def stop(self) -> None:
        self._stopping = True

    async def handle_transcript(self, transcript: str) -> str:
        await self.service.set_voice_busy(True)
        try:
            await self._show_voice_state(RobotPresentationState.THINKING)
            return await self._handle_transcript_while_busy(
                transcript, speech_ended_at=time.monotonic()
            )
        finally:
            await self.service.set_voice_busy(False)

    async def _handle_transcript_while_busy(
        self, transcript: str, *, speech_ended_at: float
    ) -> str:
        intent = match_focus_intent(transcript)
        active = getattr(self.service, "active_session", None)
        if active is None:
            active = getattr(self.service, "session", None)
        emit = getattr(self.service, "emit_voice_event", None)
        active_is_live = active is not None and active.state not in {
            SessionState.COMPLETED,
            SessionState.CANCELLED,
            SessionState.FAILED,
        }
        starts_new_session = intent is FocusIntent.START and not active_is_live
        if emit is not None and active is not None and not starts_new_session:
            emit("voice.turn_started", {"transcript": transcript[:80]})

        if intent is None and len(normalize_zh_text(transcript)) < 2:
            if emit is not None:
                emit(
                    "voice.turn_completed",
                    {
                        "transcript": transcript[:80],
                        "intent": None,
                        "speech_to_first_audio_ms": None,
                        "ignored_short_transcript": True,
                    },
                )
            return ""

        if intent is FocusIntent.START:
            session, reused = await self.service.create_session(FocusSessionCreate())
            self._sync_vad_threshold()
            if emit is not None and starts_new_session:
                emit("voice.turn_started", {"transcript": transcript[:80]})
            if session.state is SessionState.FAILED:
                reply = "机器人连接或相机暂时不可用，专注统计未能启动。"
            else:
                reply = (
                    "专注统计已经在进行中。"
                    if reused
                    else "好的，已开始专注统计，我会在结束时告诉你结果。"
                )
        elif intent is FocusIntent.STATUS:
            if active is None or active.state in {
                SessionState.COMPLETED,
                SessionState.CANCELLED,
            }:
                reply = "当前没有正在进行的专注统计。"
            else:
                reply = f"目前已采集 {active.captured_frames} 帧，分析 {active.stats.analyzed_frames} 帧。"
        elif intent is FocusIntent.STOP:
            if active is None:
                reply = "当前没有正在进行的专注统计。"
            else:
                await self.service.stop_session(active.session_id)
                self._sync_vad_threshold()
                report = self.service.get_report(active.session_id)
                if report.focus_proxy_score is None:
                    reply = "统计完成，但有效视觉样本不足，暂时无法评分。"
                else:
                    reply = (
                        f"统计完成：在位率 {report.presence_ratio * 100:.0f}% ，"
                        f"手机可见率 {report.phone_visible_ratio * 100:.0f}% ，"
                        f"专注趋势 {report.focus_proxy_score:.0f} 分。"
                    )
        elif intent is FocusIntent.CANCEL:
            if active is None:
                reply = "当前没有正在进行的专注统计。"
            else:
                await self.service.cancel_session(active.session_id)
                self._sync_vad_threshold()
                reply = "已取消本次专注统计，不会生成评分。"
        elif self.llm is not None:
            try:
                reply = await self.llm.reply(transcript, max_chinese_chars=60)
            except Exception as error:
                self.service.mark_degraded("ollama", self._failure_reason(error))
                reply = "我听到了，但当前对话服务暂时不可用。"
        else:
            reply = "我听到了。专注统计命令仍然可以正常使用。"

        speech_to_first_audio_ms = None
        if self.tts is not None:
            await self._show_voice_state(RobotPresentationState.SPEAKING)
            try:
                await self.robot.play_pcm(self.tts.synthesize(reply))
            except Exception as error:
                self.service.mark_degraded("tts", self._failure_reason(error))
            playback_started_at = getattr(self.robot, "last_playback_started_at", None)
            if playback_started_at is not None:
                speech_to_first_audio_ms = round(
                    (playback_started_at - speech_ended_at) * 1000
                )
        if emit is not None:
            emit(
                "voice.turn_completed",
                {
                    "transcript": transcript[:80],
                    "reply": reply[:120],
                    "intent": intent.value if intent else None,
                    "speech_to_first_audio_ms": speech_to_first_audio_ms,
                },
            )
        return reply

    def _sync_vad_threshold(self) -> None:
        """Use stricter voice triggering while a focus session is active."""
        if not isinstance(self.vad, EnergyVad) or self._idle_vad_threshold is None:
            return
        self.vad.threshold = self._vad_threshold_for_current_session()

    def _vad_threshold_for_current_session(self) -> int:
        """Resolve per chunk so HTTP-started sessions take effect immediately."""
        if self._idle_vad_threshold is None:
            return self.focus_vad_threshold
        active = getattr(self.service, "active_session", None)
        if active is None:
            active = getattr(self.service, "session", None)
        return (
            self.focus_vad_threshold
            if active is not None and active.state is SessionState.RUNNING
            else self._idle_vad_threshold
        )

    async def _show_voice_state(self, state: RobotPresentationState) -> None:
        show = getattr(self.service, "show_voice_state", None)
        if show is not None:
            await show(state)

    @staticmethod
    def _failure_reason(error: Exception) -> str:
        return f"{type(error).__name__}: {str(error)[:160]}"
