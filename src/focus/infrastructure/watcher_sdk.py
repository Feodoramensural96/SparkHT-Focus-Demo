from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from watcherobot import WatcheRobot

from focus.models import CapturedFrame


MAX_SDK_AUDIO_STREAM_BYTES = 4 * 1024 * 1024
logger = logging.getLogger(__name__)


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
        self._playback_lock = asyncio.Lock()
        self._animation_lock = asyncio.Lock()
        self._animation_id: str | None = None
        self._microphone_session: Any | None = None
        self._microphone_pause_requested = asyncio.Event()
        self._microphone_paused = asyncio.Event()
        self._microphone_resume = asyncio.Event()
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
            self._animation_id = None

    async def close(self) -> None:
        self._microphone_resume.set()
        robot, self._robot = self._robot, None
        self._animation_id = None
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
        robot = self._require_robot()
        microphone = await asyncio.to_thread(robot.microphone.open)
        self._microphone_session = microphone
        self._microphone_paused.clear()
        self._microphone_resume.clear()
        try:
            while self.connected:
                if self._microphone_pause_requested.is_set():
                    await asyncio.to_thread(microphone.close)
                    microphone = None
                    self._microphone_session = None
                    self._microphone_paused.set()
                    await self._microphone_resume.wait()
                    self._microphone_resume.clear()
                    self._microphone_paused.clear()
                    if not self.connected:
                        break
                    microphone = await asyncio.to_thread(robot.microphone.open)
                    self._microphone_session = microphone
                    continue
                try:
                    frame = await asyncio.to_thread(microphone.read, 1.0)
                except TimeoutError:
                    continue
                yield frame.data
        finally:
            if microphone is not None:
                await asyncio.to_thread(microphone.close)
            self._microphone_session = None
            if self._microphone_pause_requested.is_set():
                self._microphone_paused.set()

    async def play_pcm(self, chunks: AsyncIterator[bytes]) -> None:
        """Buffer one short TTS reply and play it as one continuous SDK stream.

        Protocol v1 requires total bytes and SHA before a stream starts. Starting one
        stream per HTTP chunk inserts a robot-side transition between chunks, so the
        complete reply is buffered in memory and submitted once. Voice replies are
        capped at 60 Chinese characters and fit comfortably under the SDK's 4 MiB limit.
        """
        async with self._playback_lock:
            robot = self._require_robot()
            resume_microphone = await self._pause_microphone_for_playback()
            self.last_playback_started_at = None
            try:
                buffered = bytearray()
                async for chunk in chunks:
                    if not chunk:
                        continue
                    buffered.extend(chunk)
                    if len(buffered) > MAX_SDK_AUDIO_STREAM_BYTES:
                        raise ValueError(
                            "TTS reply exceeds the 4 MiB WatcheRobot SDK stream limit"
                        )
                if buffered:
                    await self._play_block(robot, bytes(buffered))
            finally:
                if resume_microphone:
                    self._microphone_pause_requested.clear()
                    self._microphone_resume.set()

    async def stop_audio(self) -> None:
        await asyncio.to_thread(self._require_robot().audio.stop)

    async def play_animation(self, animation_id: str) -> bool:
        """Play one robot-installed animation without blocking the main pipeline.

        Starting another animation in firmware preempts the previous animation
        job. Repeated identical semantic states are deduplicated here.
        """
        if not animation_id:
            raise ValueError("animation_id must not be empty")
        async with self._animation_lock:
            robot = self._require_robot()
            supports = getattr(robot, "supports", None)
            if callable(supports) and not supports("animation"):
                return False
            if self._animation_id == animation_id:
                return True
            try:
                await asyncio.to_thread(robot.animation.play, animation_id)
            except Exception as error:
                logger.warning(
                    "Robot animation %s could not be started: %s",
                    animation_id,
                    error,
                )
                return False
            self._animation_id = animation_id
            return True

    def _require_robot(self) -> Any:
        if self._robot is None:
            raise RuntimeError("WatcheRobot SDK is not connected")
        return self._robot

    async def _pause_microphone_for_playback(self) -> bool:
        if self._microphone_session is None:
            return False
        self._microphone_resume.clear()
        self._microphone_pause_requested.set()
        try:
            await asyncio.wait_for(self._microphone_paused.wait(), timeout=2.0)
        except TimeoutError as error:
            self._microphone_pause_requested.clear()
            raise RuntimeError("microphone did not pause before playback") from error
        return True

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
        audio_duration_seconds = len(block) / (24_000 * 1 * 2)
        playback_timeout = max(10.0, audio_duration_seconds + 5.0)
        try:
            await asyncio.to_thread(playback.wait, playback_timeout)
        except Exception:
            try:
                await asyncio.to_thread(robot.audio.stop)
            except Exception:
                pass
            raise
