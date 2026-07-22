import time
from types import SimpleNamespace

import pytest

from focus.models import FocusMode, FocusSession, FocusStats, SessionState
from focus.voice import VoiceController


class FakeFocusService:
    def __init__(self) -> None:
        self.busy: list[bool] = []
        self.events: list[tuple[str, dict]] = []
        self.degraded: list[tuple[str, str]] = []
        self.session = FocusSession(
            session_id="fs_test",
            mode=FocusMode.DEMO,
            duration_seconds=90,
            state=SessionState.RUNNING,
            stats=FocusStats(analyzed_frames=4),
        )

    async def set_voice_busy(self, busy):
        self.busy.append(busy)

    async def create_session(self, request):
        return self.session, False

    async def stop_session(self, session_id):
        self.session.state = SessionState.COMPLETED
        return self.session

    async def cancel_session(self, session_id):
        self.session.state = SessionState.CANCELLED
        return self.session

    def get_report(self, session_id):
        return SimpleNamespace(
            focus_proxy_score=88, presence_ratio=0.9, phone_visible_ratio=0.1
        )

    def emit_voice_event(self, event_type, data):
        self.events.append((event_type, data))

    def mark_degraded(self, component, reason):
        self.degraded.append((component, reason))


class FakeRobot:
    def __init__(self) -> None:
        self.spoken = b""
        self.last_playback_started_at = None

    async def play_pcm(self, chunks):
        async for chunk in chunks:
            if self.last_playback_started_at is None:
                self.last_playback_started_at = time.monotonic()
            self.spoken += chunk

    async def microphone_chunks(self):
        if False:
            yield b""


class FakeTts:
    async def synthesize(self, text):
        yield text.encode()


class BrokenTts:
    async def synthesize(self, text):
        raise RuntimeError("tts offline")
        yield b""  # pragma: no cover


class BrokenAsr:
    async def transcribe(self, pcm):
        raise RuntimeError("asr offline")


class OneUtteranceVad:
    async def utterances(self, chunks):
        yield b"pcm"


@pytest.mark.asyncio
async def test_deterministic_start_status_stop_and_voice_priority() -> None:
    service = FakeFocusService()
    robot = FakeRobot()
    controller = VoiceController(
        service=service, robot=robot, asr=None, llm=None, tts=FakeTts()
    )
    assert "已开始" in await controller.handle_transcript("开始专注统计")
    assert "4 帧" in await controller.handle_transcript("统计到哪了")
    assert "专注趋势 88 分" in await controller.handle_transcript("结束专注并生成总结")
    assert service.busy == [True, False, True, False, True, False]
    completed = [data for event, data in service.events if event == "voice.turn_completed"]
    assert all(item["speech_to_first_audio_ms"] >= 0 for item in completed)


@pytest.mark.asyncio
async def test_tts_failure_returns_text_and_records_degradation() -> None:
    service = FakeFocusService()
    controller = VoiceController(
        service=service, robot=FakeRobot(), asr=None, llm=None, tts=BrokenTts()
    )

    reply = await controller.handle_transcript("统计到哪了")

    assert "4 帧" in reply
    assert service.degraded == [("tts", "RuntimeError: tts offline")]


@pytest.mark.asyncio
async def test_asr_failure_does_not_kill_voice_controller() -> None:
    service = FakeFocusService()
    controller = VoiceController(
        service=service,
        robot=FakeRobot(),
        asr=BrokenAsr(),
        llm=None,
        tts=None,
        vad=OneUtteranceVad(),
    )

    await controller.run()

    assert service.busy == [True, False]
    assert service.degraded == [("asr", "RuntimeError: asr offline")]
