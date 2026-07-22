import asyncio
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
