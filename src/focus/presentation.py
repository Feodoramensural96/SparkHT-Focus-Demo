from __future__ import annotations

from enum import Enum


class RobotPresentationState(str, Enum):
    """Semantic Demo states mapped to canonical V3.1 animation IDs."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ANALYZING = "analyzing"
    FOCUSING = "focusing"
    COMPLETED = "completed"
    ERROR = "error"


ANIMATION_ID_BY_STATE: dict[RobotPresentationState, str] = {
    RobotPresentationState.IDLE: "standby2",
    RobotPresentationState.LISTENING: "listening",
    # The firmware's finite ``thinking`` behavior falls back to its built-in
    # ``standby`` face when it ends. Model/ASR latency can outlive that job,
    # exposing the sleeping face before TTS starts. Keep this interval on the
    # same neutral looping behavior used by idle instead.
    RobotPresentationState.THINKING: "standby2",
    RobotPresentationState.SPEAKING: "speaking",
    RobotPresentationState.ANALYZING: "processing",
    RobotPresentationState.FOCUSING: "concentration",
    RobotPresentationState.COMPLETED: "happy",
    RobotPresentationState.ERROR: "error",
}


# Canonical IDs from the firmware V3.1 animation registry. The Python SDK does
# not enumerate these at runtime; optional resources still depend on the SD-card
# bundle matching the firmware registry.
V3_1_ANIMATION_IDS: tuple[str, ...] = (
    "boot",
    "happy",
    "error",
    "bluetooth",
    "speaking",
    "listening",
    "processing",
    "standby",
    "thinking",
    "custom1",
    "custom2",
    "custom3",
    "standby1",
    "standby2",
    "standby3",
    "standby4",
    "disconnect",
    "shock",
    "sunglasses",
    "sad",
    "get",
    "smile",
    "recharge",
    "speechless",
    "concentration",
    "fondle_love",
    "fondle_anger",
    "blink",
    "upgrade",
    "standby_start",
    "standby_loop",
    "standby_end",
    "music",
    "speaking_blink",
    "speaking_eye",
)
