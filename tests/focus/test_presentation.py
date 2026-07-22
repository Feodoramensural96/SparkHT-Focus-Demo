import pytest

from focus.infrastructure.session_store import FileSessionStore
from focus.models import FocusMode, FocusSession, SessionState
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


@pytest.mark.asyncio
async def test_voice_animation_preempts_vision_and_restores_default(tmp_path) -> None:
    robot = AnimationRobot()
    service = FocusService(
        store=FileSessionStore(tmp_path), robot=robot, vision=None
    )
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
        "standby",
    ]
    await service.close()
