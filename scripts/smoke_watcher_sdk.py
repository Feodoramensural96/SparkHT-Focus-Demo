#!/usr/bin/env python3
"""Verify mic, camera, and speaker sequentially on one SDK connection."""

from __future__ import annotations

import asyncio
import json
import math
import struct
import time
from datetime import UTC, datetime

from focus.infrastructure.watcher_sdk import WatcheRobotAdapter
from focus.settings import FocusSettings


async def main() -> None:
    settings = FocusSettings()
    pairing_code = settings.watcher_pairing_code.get_secret_value()
    if not pairing_code:
        raise SystemExit(
            "WATCHER_PAIRING_CODE is required and is never printed or persisted"
        )
    robot = WatcheRobotAdapter(
        pairing_code=pairing_code,
        discovery_port=settings.watcher_sdk_discovery_port,
        websocket_port=settings.watcher_sdk_websocket_port,
        host=settings.watcher_sdk_host,
    )
    await robot.connect()
    microphone = robot.microphone_chunks()
    try:
        await robot.warmup_camera()
        mic_started = time.monotonic()
        mic_chunk = await asyncio.wait_for(anext(microphone), timeout=5.0)
        mic_ms = round((time.monotonic() - mic_started) * 1000)

        captured_at = datetime.now(UTC)
        destination = (
            settings.focus_data_dir
            / "sdk-smoke"
            / f"capture-{captured_at.strftime('%Y%m%dT%H%M%SZ')}.jpg"
        )
        frame = await robot.capture(destination, "sdk-smoke-frame", 1)
        await microphone.aclose()

        async def tone():
            samples = (
                round(2_500 * math.sin(2 * math.pi * 440 * index / 24_000))
                for index in range(6_000)
            )
            yield b"".join(struct.pack("<h", sample) for sample in samples)

        playback_started = time.monotonic()
        await robot.play_pcm(tone())
        playback_ms = round((time.monotonic() - playback_started) * 1000)
        print(
            json.dumps(
                {
                    "status": "passed",
                    "same_connection": True,
                    "microphone_first_chunk_ms": mic_ms,
                    "microphone_chunk_bytes": len(mic_chunk),
                    "capture_latency_ms": frame.latency_ms,
                    "capture_path": str(frame.path),
                    "playback_total_ms": playback_ms,
                },
                ensure_ascii=False,
            )
        )
    finally:
        await microphone.aclose()
        await robot.close()


if __name__ == "__main__":
    asyncio.run(main())
