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
        robot_factory: Callable[..., Any] = WatcheRobot.connect,
    ) -> None:
        self._pairing_code = pairing_code
        self.discovery_port = discovery_port
        self.websocket_port = websocket_port
        self.host = host
        self._factory = robot_factory
        self._robot: Any | None = None
        self._connect_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._robot is not None

    async def connect(self) -> None:
        async with self._connect_lock:
            if self._robot is not None:
                return
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
        async for chunk in chunks:
            if not chunk:
                continue
            if len(chunk) % 2:
                chunk = chunk[:-1]
            if not chunk:
                continue
            playback = await asyncio.to_thread(
                robot.audio.play_pcm,
                chunk,
                sample_rate_hz=24_000,
                channels=1,
                sample_width_bytes=2,
            )
            await asyncio.to_thread(playback.wait, 10.0)

    async def stop_audio(self) -> None:
        await asyncio.to_thread(self._require_robot().audio.stop)

    def _require_robot(self) -> Any:
        if self._robot is None:
            raise RuntimeError("WatcheRobot SDK is not connected")
        return self._robot
