#!/usr/bin/env python3
"""Run repeatable Step3 single/multi-image protocol and latency checks."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from focus.infrastructure.stepfun_vlm import StepFunVlmClient
from focus.models import CapturedFrame


def percentile(values: list[int], ratio: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(ratio * len(ordered)) - 1)]


async def benchmark(args: argparse.Namespace) -> None:
    captured_at = datetime.now(UTC)
    frames = [
        CapturedFrame(
            frame_id=f"smoke-{index + 1:02d}",
            captured_at=captured_at + timedelta(seconds=index),
            path=path,
            sequence=index + 1,
            latency_ms=0,
        )
        for index, path in enumerate(args.images)
    ]
    results = []
    async with httpx.AsyncClient() as http:
        client = StepFunVlmClient(
            http=http,
            base_url=args.base_url,
            model=args.model,
            timeout=args.timeout,
            max_tokens=args.max_tokens,
        )
        for _ in range(args.runs):
            analysis = await client.analyze(frames)
            results.append(analysis.model_dump(mode="json"))

    completed = [
        result["latency_ms"] for result in results if result["status"] == "completed"
    ]
    print(
        json.dumps(
            {
                "image_count": len(frames),
                "runs": args.runs,
                "completed": len(completed),
                "p50_ms": percentile(completed, 0.50),
                "p95_ms": percentile(completed, 0.95),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--base-url", default="http://127.0.0.1:8040/v1")
    parser.add_argument("--model", default="step3-vl-focus")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-tokens", type=int, default=192)
    args = parser.parse_args()
    if not 1 <= len(args.images) <= 4:
        parser.error("provide one to four image paths")
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.max_tokens < 1:
        parser.error("--max-tokens must be at least 1")
    missing = [str(path) for path in args.images if not path.is_file()]
    if missing:
        parser.error(f"image files do not exist: {', '.join(missing)}")
    return args


if __name__ == "__main__":
    asyncio.run(benchmark(parse_args()))
