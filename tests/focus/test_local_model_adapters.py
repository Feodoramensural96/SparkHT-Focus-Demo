import json

import httpx
import pytest

from focus.infrastructure.asr import QwenAsrClient
from focus.infrastructure.ollama import OllamaClient
from focus.infrastructure.qwen_tts import QwenTtsClient


@pytest.mark.asyncio
async def test_qwen_asr_wraps_pcm_as_wav_multipart() -> None:
    observed = b""

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed
        observed = request.content
        return httpx.Response(200, json={"text": "开始统计"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = QwenAsrClient(http=http, base_url="http://asr")
        assert await client.transcribe(b"\x00\x00" * 160) == "开始统计"
    assert b"RIFF" in observed and b"audio.wav" in observed
    assert b'name="model"' not in observed


@pytest.mark.asyncio
async def test_ollama_limits_reply_to_sixty_chinese_characters() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3:0.6b"
        return httpx.Response(200, json={"message": {"content": "好" * 80}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OllamaClient(http=http, base_url="http://ollama", model="qwen3:0.6b")
        assert len(await client.reply("你好")) == 60


@pytest.mark.asyncio
async def test_tts_requests_streaming_pcm_and_yields_first_chunk() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["response_format"] == "pcm"
        assert payload["stream"] is True
        return httpx.Response(200, content=b"\x01\x00\x02\x00")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = QwenTtsClient(http=http, base_url="http://tts")
        chunks = [chunk async for chunk in client.synthesize("统计开始")]
    assert b"".join(chunks) == b"\x01\x00\x02\x00"
