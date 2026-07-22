from __future__ import annotations

import httpx


class OllamaClient:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "qwen3:0.6b",
        timeout: float = 10.0,
    ) -> None:
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def reply(self, text: str, *, max_chinese_chars: int = 60) -> str:
        response = await self.http.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "think": False,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是机器人语音助手。只用中文口语回答，不用 Markdown，"
                            f"最多 {max_chinese_chars} 个字。"
                        ),
                    },
                    {"role": "user", "content": text},
                ],
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        content = response.json().get("message", {}).get("content")
        if not isinstance(content, str):
            raise ValueError("Ollama response is missing message.content")
        return content.strip()[:max_chinese_chars]

    async def health(self) -> bool:
        try:
            response = await self.http.get(f"{self.base_url}/api/tags", timeout=2.0)
            return response.is_success
        except httpx.HTTPError:
            return False
