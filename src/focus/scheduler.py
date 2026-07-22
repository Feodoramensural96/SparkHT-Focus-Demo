from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar


FrameT = TypeVar("FrameT")
ResultT = TypeVar("ResultT")


class BatchBuilder(Generic[FrameT]):
    def __init__(self, *, batch_size: int = 4) -> None:
        if batch_size < 2:
            raise ValueError("batch_size must be at least two")
        self.batch_size = batch_size
        self._frames: list[FrameT] = []

    def add(self, frame: FrameT) -> list[FrameT] | None:
        self._frames.append(frame)
        if len(self._frames) < self.batch_size:
            return None
        batch, self._frames = self._frames[: self.batch_size], self._frames[self.batch_size :]
        return batch

    def flush_tail(self) -> list[FrameT] | None:
        if len(self._frames) < 2:
            return None
        frames, self._frames = self._frames, []
        return frames


class VisionPriorityScheduler(Generic[FrameT, ResultT]):
    """Single-concurrency latest-only scheduler preempted by voice activity."""

    def __init__(
        self,
        analyze: Callable[[list[FrameT]], Awaitable[ResultT]],
        *,
        voice_idle_seconds: float = 5.0,
        on_started: Callable[[list[FrameT]], Awaitable[None]] | None = None,
        on_completed: Callable[[ResultT], Awaitable[None]] | None = None,
        on_paused: Callable[[list[FrameT]], Awaitable[None]] | None = None,
        on_dropped: Callable[[list[FrameT]], Awaitable[None]] | None = None,
    ) -> None:
        self._analyze = analyze
        self.voice_idle_seconds = voice_idle_seconds
        self._on_started = on_started
        self._on_completed = on_completed
        self._on_paused = on_paused
        self._on_dropped = on_dropped
        self._voice_busy = False
        self._idle_ready = True
        self._pending: list[FrameT] | None = None
        self._pending_retry = 0
        self._current: list[FrameT] | None = None
        self._current_retry = 0
        self._active_task: asyncio.Task[ResultT] | None = None
        self._worker: asyncio.Task[None] | None = None
        self._idle_task: asyncio.Task[None] | None = None
        self._wakeup = asyncio.Event()
        self._stopping = False

    @property
    def pending_batch(self) -> list[FrameT] | None:
        return self._pending

    @property
    def active(self) -> bool:
        return self._active_task is not None and not self._active_task.done()

    async def drain(self, *, timeout: float = 30.0) -> None:
        async def wait_until_idle() -> None:
            while self.active or self._pending is not None:
                await asyncio.sleep(0.02)

        await asyncio.wait_for(wait_until_idle(), timeout=timeout)

    async def start(self) -> None:
        if self._worker is None:
            self._stopping = False
            self._worker = asyncio.create_task(self._run(), name="focus-vision-scheduler")

    def submit(self, batch: list[FrameT]) -> None:
        if not batch:
            raise ValueError("batch must not be empty")
        previous = self._pending
        self._pending = list(batch)
        self._pending_retry = 0
        if previous is not None and self._on_dropped is not None:
            asyncio.create_task(self._on_dropped(previous))
        self._wakeup.set()

    async def set_voice_busy(self, busy: bool) -> None:
        if busy == self._voice_busy:
            return
        self._voice_busy = busy
        if busy:
            self._idle_ready = False
            if self._idle_task is not None:
                self._idle_task.cancel()
                self._idle_task = None
            task = self._active_task
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        else:
            self._idle_task = asyncio.create_task(self._arm_after_idle())
        self._wakeup.set()

    async def _arm_after_idle(self) -> None:
        try:
            await asyncio.sleep(self.voice_idle_seconds)
        except asyncio.CancelledError:
            return
        if not self._voice_busy:
            self._idle_ready = True
            self._wakeup.set()

    async def _run(self) -> None:
        while not self._stopping:
            await self._wakeup.wait()
            self._wakeup.clear()
            if self._voice_busy or not self._idle_ready or self._pending is None:
                continue
            self._current, self._pending = self._pending, None
            self._current_retry, self._pending_retry = self._pending_retry, 0
            if self._on_started is not None:
                await self._on_started(self._current)
            self._active_task = asyncio.create_task(self._analyze(self._current))
            try:
                result = await self._active_task
            except asyncio.CancelledError:
                if self._stopping:
                    return
                if self._on_paused is not None:
                    await self._on_paused(self._current)
                if self._current_retry < 1 and self._pending is None:
                    self._pending = self._current
                    self._pending_retry = self._current_retry + 1
            except Exception:
                # The analyzer owns failure recording; one failed batch must not stop scheduling.
                pass
            else:
                if self._on_completed is not None:
                    await self._on_completed(result)
            finally:
                self._active_task = None
                self._current = None
            if self._pending is not None:
                self._wakeup.set()

    async def stop(self) -> None:
        self._stopping = True
        if self._idle_task is not None:
            self._idle_task.cancel()
        if self._active_task is not None and not self._active_task.done():
            self._active_task.cancel()
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        self._worker = None
