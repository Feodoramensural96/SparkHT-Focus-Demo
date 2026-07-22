import asyncio
from dataclasses import dataclass

import pytest

from focus.scheduler import BatchBuilder, VisionPriorityScheduler


@dataclass(frozen=True)
class Frame:
    frame_id: str


def test_four_frames_form_batch_and_tail_rules() -> None:
    builder = BatchBuilder(batch_size=4)
    assert all(builder.add(Frame(f"f-{i}")) is None for i in range(1, 4))
    assert [f.frame_id for f in builder.add(Frame("f-4"))] == [
        "f-1",
        "f-2",
        "f-3",
        "f-4",
    ]
    builder.add(Frame("f-5"))
    assert builder.flush_tail() is None
    builder.add(Frame("f-6"))
    assert [f.frame_id for f in builder.flush_tail()] == ["f-5", "f-6"]


@pytest.mark.asyncio
async def test_voice_cancels_active_vision_and_latest_batch_wins() -> None:
    started = asyncio.Event()
    cancelled: list[str] = []

    async def analyze(batch: list[str]) -> str:
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(batch[0])
            raise
        return batch[0]

    scheduler = VisionPriorityScheduler(analyze, voice_idle_seconds=0.01)
    await scheduler.start()
    scheduler.submit(["old"])
    await started.wait()
    await scheduler.set_voice_busy(True)
    scheduler.submit(["stale"])
    scheduler.submit(["latest"])
    assert scheduler.pending_batch == ["latest"]
    assert cancelled == ["old"]
    await scheduler.stop()


@pytest.mark.asyncio
async def test_resumes_only_after_idle_and_retries_paused_batch_once() -> None:
    calls: list[str] = []

    async def analyze(batch: list[str]) -> str:
        calls.append(batch[0])
        return batch[0]

    scheduler = VisionPriorityScheduler(analyze, voice_idle_seconds=0.01)
    await scheduler.start()
    await scheduler.set_voice_busy(True)
    scheduler.submit(["batch"])
    await asyncio.sleep(0.02)
    assert calls == []
    await scheduler.set_voice_busy(False)
    await asyncio.sleep(0.04)
    assert calls == ["batch"]
    await scheduler.stop()
