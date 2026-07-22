from __future__ import annotations

from collections.abc import AsyncIterator

import httpx


class QwenTtsClient:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        base_url: str = "http://127.0.0.1:8030",
        model: str = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        voice: str = "Aiden",
        timeout: float = 30.0,
    ) -> None:
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.voice = voice
        self.timeout = timeout

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        async with self.http.stream(
            "POST",
            f"{self.base_url}/v1/audio/speech",
            json={
                "input": text,
                "model": self.model,
                "voice": self.voice,
                "language": "Chinese",
                "response_format": "pcm",
                "stream_format": "audio",
                "stream": True,
            },
            timeout=self.timeout,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(chunk_size=4096):
                if chunk:
                    yield chunk

    async def health(self) -> bool:
        try:
            response = await self.http.get(f"{self.base_url}/health", timeout=2.0)
            return response.is_success
        except httpx.HTTPError:
            return False
