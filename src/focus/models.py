from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FocusMode(str, Enum):
    DEMO = "demo"
    NORMAL = "normal"


class SessionState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class VoiceState(str, Enum):
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SYNTHESIZING = "synthesizing"
    PLAYING = "playing"


class FocusSessionCreate(BaseModel):
    mode: FocusMode = FocusMode.DEMO
    duration_seconds: int = Field(default=90, ge=1, le=86_400)


class FrameObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frame_id: str = Field(min_length=1)
    captured_at: datetime
    person: Literal["present", "absent", "uncertain"]
    phone: Literal["visible", "not_visible", "uncertain"]
    cup: Literal["visible", "not_visible", "uncertain"]
    cup_motion: Literal["stable", "changed", "uncertain"]
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(max_length=30)


class CapturedFrame(BaseModel):
    frame_id: str
    captured_at: datetime
    path: Path
    sequence: int = Field(ge=1)
    latency_ms: int = Field(ge=0)


class BatchAnalysis(BaseModel):
    batch_id: str
    observations: list[FrameObservation]
    model_name: str
    latency_ms: int = Field(ge=0)
    status: Literal[
        "completed", "paused_by_voice", "analysis_failed", "dropped_as_stale"
    ]
    error: str | None = None


class FocusStats(BaseModel):
    analyzed_frames: int = 0
    valid_person_frames: int = 0
    valid_phone_frames: int = 0
    presence_ratio: float | None = None
    phone_visible_ratio: float | None = None
    phone_transition_count: int = 0
    suspected_drink_events: int = 0
    focus_proxy_score: float | None = None


class FocusReport(BaseModel):
    session_id: str
    started_at: datetime
    ended_at: datetime
    captured_frames: int
    analyzed_frames: int
    failed_frames: int
    presence_ratio: float | None
    phone_visible_ratio: float | None
    phone_transition_count: int
    suspected_drink_events: int
    focus_proxy_score: float | None
    summary: str


class FocusSession(BaseModel):
    session_id: str
    mode: FocusMode
    duration_seconds: int
    state: SessionState = SessionState.STARTING
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    ended_at: datetime | None = None
    captured_frames: int = 0
    failed_frames: int = 0
    dropped_batches: int = 0
    interrupted: bool = False
    degraded_components: dict[str, str] = Field(default_factory=dict)
    stats: FocusStats = Field(default_factory=FocusStats)


class FocusEvent(BaseModel):
    event_id: str
    session_id: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    type: Literal[
        "session.state_changed",
        "camera.frame_captured",
        "camera.capture_failed",
        "vision.batch_started",
        "vision.batch_paused",
        "vision.batch_completed",
        "vision.batch_failed",
        "stats.updated",
        "voice.turn_started",
        "voice.turn_completed",
        "service.degraded",
    ]
    data: dict[str, Any] = Field(default_factory=dict)


class ComponentHealth(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    latency_ms: int | None = None
    backend: str | None = None
    model: str | None = None
    reason: str | None = None


class HealthResponse(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    components: dict[str, ComponentHealth]
