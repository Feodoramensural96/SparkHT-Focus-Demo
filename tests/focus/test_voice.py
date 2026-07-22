from types import SimpleNamespace

import pytest

from focus.intent import FocusIntent
from focus.models import FocusMode, FocusReport, FocusSession, FocusStats, SessionState
from focus.voice import VoiceController


class FakeFocusService:
    def __init__(self) -> None:
        self.busy: list[bool] = []
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
        return SimpleNamespace(focus_proxy_score=88, presence_ratio=.9, phone_visible_ratio=.1)


class FakeRobot:
    def __init__(self) -> None:
        self.spoken = b""

    async def play_pcm(self, chunks):
        async for chunk in chunks:
            self.spoken += chunk


class FakeTts:
    async def synthesize(self, text):
        yield text.encode()


@pytest.mark.asyncio
async def test_deterministic_start_status_stop_and_voice_priority() -> None:
    service = FakeFocusService()
    robot = FakeRobot()
    controller = VoiceController(service=service, robot=robot, asr=None, llm=None, tts=FakeTts())
    assert "已开始" in await controller.handle_transcript("开始专注统计")
    assert "4 帧" in await controller.handle_transcript("统计到哪了")
    assert "专注趋势 88 分" in await controller.handle_transcript("结束专注并生成总结")
    assert service.busy == [True, False, True, False, True, False]
