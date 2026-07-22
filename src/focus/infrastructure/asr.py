from __future__ import annotations

import io
import wave

import httpx


class QwenAsrClient:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        base_url: str = "http://127.0.0.1:8010",
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def transcribe(self, pcm: bytes) -> str:
        wav = self._pcm_to_wav(pcm)
        form = {"language": "zh"}
        if self.model:
            form["model"] = self.model
        response = await self.http.post(
            f"{self.base_url}/v1/audio/transcriptions",
            data=form,
            files={"file": ("audio.wav", wav, "audio/wav")},
            timeout=self.timeout,
        )
        response.raise_for_status()
        text = response.json().get("text")
        if not isinstance(text, str):
            raise ValueError("ASR response is missing text")
        return text.strip()

    async def health(self) -> bool:
        try:
            response = await self.http.get(f"{self.base_url}/health", timeout=2.0)
            return response.is_success
        except httpx.HTTPError:
            return False

    @staticmethod
    def _pcm_to_wav(pcm: bytes) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(16_000)
            stream.writeframes(pcm)
        return output.getvalue()
