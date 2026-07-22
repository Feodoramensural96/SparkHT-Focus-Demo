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
        self._focus_entry_lock = asyncio.Lock()
        self._animation_id: str | None = None
        self._behavior_id: str | None = None
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
            self._behavior_id = None

    async def close(self) -> None:
        self._microphone_resume.set()
        robot, self._robot = self._robot, None
        self._animation_id = None
        self._behavior_id = None
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

    async def set_light(self, color: str, *, brightness: float = 1.0) -> bool:
        """Set every robot light zone to one solid color."""
        robot = self._require_robot()
        supports = getattr(robot, "supports", None)
        if callable(supports) and not supports("light"):
            return False
        try:
            await asyncio.to_thread(
                robot.lights.set_color,
                color,
                brightness=brightness,
                zone="all",
            )
        except Exception as error:
            logger.warning("Robot light %s could not be set: %s", color, error)
            return False
        return True

    async def play_animation(self, animation_id: str, *, restart: bool = False) -> bool:
        """Play one robot-installed animation without blocking the main pipeline.

        Starting another animation in firmware preempts the previous animation
        job. Repeated identical semantic states are deduplicated here.
        """
        async with self._focus_entry_lock:
            return await self._play_animation(animation_id, restart=restart)

    async def _play_animation(
        self, animation_id: str, *, restart: bool = False
    ) -> bool:
        if not animation_id:
            raise ValueError("animation_id must not be empty")
        async with self._animation_lock:
            robot = self._require_robot()
            supports = getattr(robot, "supports", None)
            if callable(supports) and not supports("animation"):
                return False
            if self._animation_id == animation_id and not restart:
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
            self._behavior_id = None
            return True

    async def play_behavior(self, behavior_id: str) -> bool:
        """Enter one firmware Behavior state.

        Looping expressions such as ``standby`` and ``concentration`` are kept
        alive by the firmware's ``loop_until_replaced`` policy. This avoids a
        finite GIF job ending and exposing the SDK Connected screen.
        """
        async with self._focus_entry_lock:
            return await self._play_behavior(behavior_id)

    async def _play_behavior(self, behavior_id: str) -> bool:
        if not behavior_id:
            raise ValueError("behavior_id must not be empty")
        async with self._animation_lock:
            robot = self._require_robot()
            supports = getattr(robot, "supports", None)
            if callable(supports) and not supports("behavior"):
                return False
            if self._behavior_id == behavior_id:
                return True
            try:
                await asyncio.to_thread(robot.behavior.play, behavior_id, repeat=1)
            except Exception as error:
                logger.warning(
                    "Robot behavior %s could not be started: %s",
                    behavior_id,
                    error,
                )
                return False
            self._behavior_id = behavior_id
            self._animation_id = None
            return True

    async def enter_focus_mode(self) -> bool:
        """Play the focus face, perform one nod, then hold the focus loop.

        The calibrated V3.1 installation-space pose is pan=90, tilt=120.
        Lowering tilt to 105 raises the head; moving to 125 creates one nod.
        All targets stay inside the firmware's protected 100..140 tilt range.
        """
        async with self._focus_entry_lock:
            robot = self._require_robot()
            animation_started = await self._play_animation(
                "concentration", restart=True
            )
            supports = getattr(robot, "supports", None)
            motion_supported = not callable(supports) or supports("motion")
            if motion_supported:
                try:
                    await self._move_to_and_wait(
                        robot, pan_deg=90, tilt_deg=105, duration_ms=500
                    )
                    await self._move_to_and_wait(
                        robot, pan_deg=90, tilt_deg=125, duration_ms=300
                    )
                except Exception as error:
                    logger.warning("Robot focus entry motion failed: %s", error)
                finally:
                    try:
                        await self._move_to_and_wait(
                            robot, pan_deg=90, tilt_deg=120, duration_ms=400
                        )
                    except Exception as error:
                        logger.warning(
                            "Robot could not return to neutral pose: %s", error
                        )

            behavior_started = await self._play_behavior("concentration")
            if not behavior_started:
                # Firmware V3.1 supports Behavior. The animation fallback keeps
                # older compatible builds useful, although it may be finite.
                animation_started = await self._play_animation(
                    "concentration", restart=True
                )
            return behavior_started or animation_started

    @staticmethod
    async def _move_to_and_wait(
        robot: Any,
        *,
        pan_deg: int,
        tilt_deg: int,
        duration_ms: int,
    ) -> None:
        job = await asyncio.to_thread(
            robot.motion.move_to,
            pan_deg=pan_deg,
            tilt_deg=tilt_deg,
            duration_ms=duration_ms,
        )
        wait = getattr(job, "wait", None)
        if callable(wait):
            await asyncio.to_thread(wait, max(5.0, duration_ms / 1000 + 2.0))

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
