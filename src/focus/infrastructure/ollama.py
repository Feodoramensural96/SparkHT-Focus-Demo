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

    async def reply(
        self,
        text: str,
        *,
        max_chinese_chars: int = 60,
        focus_context: str | None = None,
    ) -> str:
        system_prompt = (
            "你是一个自然、有温度的桌面陪伴机器人，正在帮助用户保持专注。"
            "只用简短的中文口语回答，不用 Markdown，不复述用户原话，也不要说教。"
            "只可依据提供的实时状态谈论专注数据；状态中没有的数据不要猜测或编造。"
            f"每次回答一到两句话，最多 {max_chinese_chars} 个字，并保证句子完整。"
        )
        if focus_context:
            system_prompt += f"\n当前实时专注状态：{focus_context}"
        response = await self.http.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "think": False,
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt,
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
