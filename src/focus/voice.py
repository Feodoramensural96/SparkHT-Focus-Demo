from __future__ import annotations

import math
import sys
from array import array
from collections.abc import AsyncIterator

from .intent import FocusIntent, match_focus_intent
from .models import FocusSessionCreate, SessionState
from .ports import AsrPort, LlmPort, RobotPort, TtsPort
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

    async def utterances(self, chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        buffered: list[bytes] = []
        voiced = 0
        silence = 0
        async for chunk in chunks:
            level = self._rms(chunk)
            if level >= self.threshold:
                voiced += 1
                silence = 0
                buffered.append(chunk)
            elif buffered:
                buffered.append(chunk)
                silence += 1
            if buffered and (silence >= self.silence_chunks or len(buffered) >= self.max_chunks):
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
    ) -> None:
        self.service = service
        self.robot = robot
        self.asr = asr
        self.llm = llm
        self.tts = tts
        self.vad = vad or EnergyVad()
        self._stopping = False

    async def run(self) -> None:
        if self.asr is None:
            return
        self._stopping = False
        async for utterance in self.vad.utterances(self.robot.microphone_chunks()):
            if self._stopping:
                break
            await self.service.set_voice_busy(True)
            try:
                transcript = await self.asr.transcribe(utterance)
                await self._handle_transcript_while_busy(transcript)
            finally:
                await self.service.set_voice_busy(False)

    async def stop(self) -> None:
        self._stopping = True

    async def handle_transcript(self, transcript: str) -> str:
        await self.service.set_voice_busy(True)
        try:
            return await self._handle_transcript_while_busy(transcript)
        finally:
            await self.service.set_voice_busy(False)

    async def _handle_transcript_while_busy(self, transcript: str) -> str:
        intent = match_focus_intent(transcript)
        active = getattr(self.service, "active_session", None)
        if active is None:
            active = getattr(self.service, "session", None)

        if intent is FocusIntent.START:
            _, reused = await self.service.create_session(FocusSessionCreate())
            reply = "专注统计已经在进行中。" if reused else "好的，已开始专注统计，我会在结束时告诉你结果。"
        elif intent is FocusIntent.STATUS:
            if active is None or active.state in {SessionState.COMPLETED, SessionState.CANCELLED}:
                reply = "当前没有正在进行的专注统计。"
            else:
                reply = f"目前已采集 {active.captured_frames} 帧，分析 {active.stats.analyzed_frames} 帧。"
        elif intent is FocusIntent.STOP:
            if active is None:
                reply = "当前没有正在进行的专注统计。"
            else:
                await self.service.stop_session(active.session_id)
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
                reply = "已取消本次专注统计，不会生成评分。"
        elif self.llm is not None:
            try:
                reply = await self.llm.reply(transcript, max_chinese_chars=60)
            except Exception:
                reply = "我听到了，但当前对话服务暂时不可用。"
        else:
            reply = "我听到了。专注统计命令仍然可以正常使用。"

        if self.tts is not None:
            try:
                await self.robot.play_pcm(self.tts.synthesize(reply))
            except Exception:
                pass
        return reply
