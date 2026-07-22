#!/usr/bin/env python3
"""Summarize persisted camera, vision, and voice latency observations."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path


def percentile(values: list[int], ratio: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(ratio * len(ordered)) - 1)]


def distribution(values: list[int]) -> dict[str, object]:
    return {
        "samples": len(values),
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
        "observations_ms": values,
    }


def event_files(paths: list[Path]) -> list[Path]:
    files: set[Path] = set()
    for path in paths:
        if path.is_file():
            files.add(path)
        elif (path / "events.jsonl").is_file():
            files.add(path / "events.jsonl")
        elif path.is_dir():
            files.update(path.glob("*/events.jsonl"))
    return sorted(files)


def summarize(path: Path) -> dict[str, object]:
    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    counts = Counter(event["type"] for event in events)
    camera = [
        event["data"]["latency_ms"]
        for event in events
        if event["type"] == "camera.frame_captured"
        and isinstance(event["data"].get("latency_ms"), int)
    ]
    vision = [
        event["data"]["latency_ms"]
        for event in events
        if event["type"] == "vision.batch_completed"
        and isinstance(event["data"].get("latency_ms"), int)
    ]
    voice = [
        event["data"]["speech_to_first_audio_ms"]
        for event in events
        if event["type"] == "voice.turn_completed"
        and event["data"].get("source") != "session_timer"
        and isinstance(event["data"].get("speech_to_first_audio_ms"), int)
    ]
    return {
        "session_id": path.parent.name,
        "event_counts": dict(sorted(counts.items())),
        "camera": distribution(camera),
        "step3": distribution(vision),
        "speech_to_robot_first_audio": distribution(voice),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path, default=[Path("runtime/focus")])
    args = parser.parse_args()
    files = event_files(args.paths)
    if not files:
        parser.error("no events.jsonl files found")
    print(
        json.dumps(
            {"sessions": [summarize(path) for path in files]},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
