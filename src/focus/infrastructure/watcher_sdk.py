from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from watcherobot import WatcheRobot

from focus.models import CapturedFrame


class WatcheRobotAdapter:
    """Thin async adapter that owns exactly one WatcheRobot SDK connection."""

    def __init__(
        self,
        *,
        pairing_code: str,
        discovery_port: int = 37021,
        websocket_port: int = 8766,
        host: str = "auto",
        followup_audio_block_bytes: int = 24_000,
        robot_factory: Callable[..., Any] = WatcheRobot.connect,
    ) -> None:
        self._pairing_code = pairing_code
        self.discovery_port = discovery_port
        self.websocket_port = websocket_port
        self.host = host
        self.followup_audio_block_bytes = followup_audio_block_bytes
        self._factory = robot_factory
        self._robot: Any | None = None
        self._connect_lock = asyncio.Lock()
        self.last_playback_started_at: float | None = None

    @property
    def connected(self) -> bool:
        return self._robot is not None and not bool(
            getattr(self._robot, "_closed", False)
        )

    async def connect(self) -> None:
        async with self._connect_lock:
            if self.connected:
                return
            stale, self._robot = self._robot, None
            if stale is not None:
                try:
                    await asyncio.to_thread(stale.close)
                except Exception:
                    pass
            self._robot = await asyncio.to_thread(
                self._factory,
                pairing_code=self._pairing_code,
                discovery_port=self.discovery_port,
                websocket_port=self.websocket_port,
                host=self.host,
                timeout=30.0,
            )

    async def close(self) -> None:
        robot, self._robot = self._robot, None
        if robot is not None:
            await asyncio.to_thread(robot.close)

    async def warmup_camera(self) -> None:
        robot = self._require_robot()
        await asyncio.to_thread(
            robot.camera.capture,
            width=640,
            height=480,
            quality=0,
            timeout=4.0,
        )

    async def capture(
        self, destination: Path, frame_id: str, sequence: int
    ) -> CapturedFrame:
        robot = self._require_robot()
        started = time.monotonic()
        image = await asyncio.wait_for(
            asyncio.to_thread(
                robot.camera.capture,
                width=640,
                height=480,
                quality=0,
                timeout=1.5,
            ),
            timeout=2.0,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(image.data)
        os.replace(temporary, destination)
        timestamp = getattr(image, "timestamp", datetime.now(UTC).timestamp())
        return CapturedFrame(
            frame_id=frame_id,
            captured_at=datetime.fromtimestamp(timestamp, UTC),
            path=destination,
            sequence=sequence,
            latency_ms=round((time.monotonic() - started) * 1000),
        )

    async def microphone_chunks(self) -> AsyncIterator[bytes]:
        microphone = await asyncio.to_thread(self._require_robot().microphone.open)
        try:
            while self.connected:
                try:
                    frame = await asyncio.to_thread(microphone.read, 1.0)
                except TimeoutError:
                    continue
                yield frame.data
        finally:
            await asyncio.to_thread(microphone.close)

    async def play_pcm(self, chunks: AsyncIterator[bytes]) -> None:
        """Start the first SDK stream as soon as its PCM chunk arrives.

        Protocol v1 requires total bytes and SHA before each stream, so generated chunks
        are played as short sequential SDK streams instead of buffering the whole reply.
        """
        robot = self._require_robot()
        self.last_playback_started_at = None
        first = True
        buffered = bytearray()
        async for chunk in chunks:
            if not chunk:
                continue
            if first:
                await self._play_block(robot, chunk)
                first = False
                continue
            buffered.extend(chunk)
            while len(buffered) >= self.followup_audio_block_bytes:
                block = bytes(buffered[: self.followup_audio_block_bytes])
                del buffered[: self.followup_audio_block_bytes]
                await self._play_block(robot, block)
        if buffered:
            await self._play_block(robot, bytes(buffered))

    async def stop_audio(self) -> None:
        await asyncio.to_thread(self._require_robot().audio.stop)

    def _require_robot(self) -> Any:
        if self._robot is None:
            raise RuntimeError("WatcheRobot SDK is not connected")
        return self._robot

    async def _play_block(self, robot: Any, block: bytes) -> None:
        if len(block) % 2:
            block = block[:-1]
        if not block:
            return
        playback = await asyncio.to_thread(
            robot.audio.play_pcm,
            block,
            sample_rate_hz=24_000,
            channels=1,
            sample_width_bytes=2,
        )
        if self.last_playback_started_at is None:
            self.last_playback_started_at = time.monotonic()
        try:
            await asyncio.to_thread(playback.wait, 10.0)
        except Exception:
            try:
                await asyncio.to_thread(robot.audio.stop)
            except Exception:
                pass
            raise
