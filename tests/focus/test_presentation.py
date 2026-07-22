import pytest

from focus.infrastructure.session_store import FileSessionStore
from focus.models import FocusMode, FocusSession, FocusSessionCreate, SessionState
from focus.presentation import (
    ANIMATION_ID_BY_STATE,
    V3_1_ANIMATION_IDS,
    RobotPresentationState,
)
from focus.service import FocusService


def test_every_demo_state_maps_to_a_canonical_v3_1_animation() -> None:
    assert set(ANIMATION_ID_BY_STATE) == set(RobotPresentationState)
    assert set(ANIMATION_ID_BY_STATE.values()) <= set(V3_1_ANIMATION_IDS)
    assert len(V3_1_ANIMATION_IDS) == 35
    assert len(set(V3_1_ANIMATION_IDS)) == len(V3_1_ANIMATION_IDS)


def test_core_focus_state_mapping_is_explicit() -> None:
    assert ANIMATION_ID_BY_STATE == {
        RobotPresentationState.IDLE: "standby",
        RobotPresentationState.LISTENING: "listening",
        RobotPresentationState.THINKING: "thinking",
        RobotPresentationState.SPEAKING: "speaking",
        RobotPresentationState.ANALYZING: "processing",
        RobotPresentationState.FOCUSING: "concentration",
        RobotPresentationState.COMPLETED: "happy",
        RobotPresentationState.ERROR: "error",
    }


class AnimationRobot:
    def __init__(self) -> None:
        self.connected = False
        self.animation_ids: list[str] = []

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def play_animation(self, animation_id: str) -> bool:
        self.animation_ids.append(animation_id)
        return True


class FocusEntryRobot(AnimationRobot):
    def __init__(self) -> None:
        super().__init__()
        self.focus_entry_calls = 0

    async def enter_focus_mode(self) -> bool:
        self.focus_entry_calls += 1
        self.animation_ids.append("concentration-loop")
        return True


@pytest.mark.asyncio
async def test_voice_animation_preempts_vision_and_restores_default(tmp_path) -> None:
    robot = AnimationRobot()
    service = FocusService(store=FileSessionStore(tmp_path), robot=robot, vision=None)
    await service.start()
    service._active = FocusSession(
        session_id="fs_animation",
        mode=FocusMode.DEMO,
        duration_seconds=90,
        state=SessionState.RUNNING,
    )

    await service._show_presentation(RobotPresentationState.FOCUSING)
    await service.set_voice_busy(True)
    await service._show_presentation(RobotPresentationState.ANALYZING)
    await service.show_voice_state(RobotPresentationState.THINKING)
    await service.set_voice_busy(False)

    assert robot.animation_ids == [
        "standby",
        "concentration",
        "thinking",
        "concentration",
    ]
    await service.close()


@pytest.mark.asyncio
async def test_voice_during_finalization_restores_processing_not_standby(
    tmp_path,
) -> None:
    robot = AnimationRobot()
    service = FocusService(store=FileSessionStore(tmp_path), robot=robot, vision=None)
    service._active = FocusSession(
        session_id="fs_finalizing_animation",
        mode=FocusMode.DEMO,
        duration_seconds=90,
        state=SessionState.FINALIZING,
    )

    await service.set_voice_busy(True)
    await service.show_voice_state(RobotPresentationState.SPEAKING)
    await service.set_voice_busy(False)

    assert robot.animation_ids == ["speaking", "processing"]


@pytest.mark.asyncio
async def test_session_start_runs_focus_entry_even_during_voice_override(
    tmp_path,
) -> None:
    robot = FocusEntryRobot()

    class CameraVision:
        async def analyze(self, frames):
            raise AssertionError("capture loop should not run in this assertion")

    service = FocusService(
        store=FileSessionStore(tmp_path), robot=robot, vision=CameraVision()
    )
    robot.warmup_camera = lambda: _async_none()
    service._voice_busy = True

    session, reused = await service.create_session(FocusSessionCreate())

    assert reused is False
    assert session.state is SessionState.RUNNING
    assert robot.focus_entry_calls == 1
    await service.cancel_session(session.session_id)
    await service.close()


async def _async_none() -> None:
    return None
