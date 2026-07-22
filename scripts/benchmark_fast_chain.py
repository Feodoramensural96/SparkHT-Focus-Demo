#!/usr/bin/env python3
"""Measure real local ASR, Ollama, and streaming TTS calls without a robot."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time

import httpx

from focus.infrastructure.asr import QwenAsrClient
from focus.infrastructure.ollama import OllamaClient
from focus.infrastructure.qwen_tts import QwenTtsClient
from focus.settings import FocusSettings


def percentile(values: list[int], ratio: float) -> int:
    """Return an observed nearest-rank percentile without interpolation."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(ratio * len(ordered)) - 1)]


async def benchmark(runs: int) -> None:
    settings = FocusSettings()
    async with httpx.AsyncClient() as http:
        asr = QwenAsrClient(http=http, base_url=settings.qwen_asr_base_url)
        llm = OllamaClient(
            http=http,
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
        )
        tts = QwenTtsClient(
            http=http,
            base_url=settings.qwen_tts_base_url,
            model=settings.qwen_tts_model,
            voice=settings.qwen_tts_voice,
        )

        asr_runs: list[int] = []
        asr_texts: list[str] = []
        llm_runs: list[int] = []
        tts_first_audio_runs: list[int] = []
        tts_total_runs: list[int] = []
        tts_audio_bytes: list[int] = []
        for _ in range(runs):
            started = time.monotonic()
            asr_texts.append(await asr.transcribe(b"\x00\x00" * 8_000))
            asr_runs.append(round((time.monotonic() - started) * 1000))

            started = time.monotonic()
            await llm.reply("只回答好", max_chinese_chars=2)
            llm_runs.append(round((time.monotonic() - started) * 1000))

            started = time.monotonic()
            first_audio_ms = None
            audio_bytes = 0
            async for chunk in tts.synthesize("专注统计测试成功"):
                if first_audio_ms is None:
                    first_audio_ms = round((time.monotonic() - started) * 1000)
                audio_bytes += len(chunk)
            if first_audio_ms is None:
                raise RuntimeError("TTS returned no PCM audio")
            tts_first_audio_runs.append(first_audio_ms)
            tts_total_runs.append(round((time.monotonic() - started) * 1000))
            tts_audio_bytes.append(audio_bytes)

    def summarize(values: list[int]) -> dict[str, object]:
        return {
            "runs_ms": values,
            "p50_ms": percentile(values, 0.50),
            "p95_ms": percentile(values, 0.95),
        }

    print(
        json.dumps(
            {
                "runs": runs,
                "asr": summarize(asr_runs),
                "asr_texts": asr_texts,
                "ollama": summarize(llm_runs),
                "tts_first_audio": summarize(tts_first_audio_runs),
                "tts_total": summarize(tts_total_runs),
                "tts_audio_bytes": tts_audio_bytes,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    asyncio.run(benchmark(args.runs))
