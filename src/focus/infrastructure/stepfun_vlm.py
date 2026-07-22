from __future__ import annotations

import base64
import time
import uuid

import httpx

from focus.models import BatchAnalysis, CapturedFrame
from focus.prompts import (
    CORRECTION_PROMPT,
    SYSTEM_PROMPT,
    parse_stepfun_response,
    user_prompt,
)


class StepFunVlmClient:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        base_url: str = "http://127.0.0.1:8040/v1",
        model: str = "step3-vl-focus",
        timeout: float = 30.0,
        max_tokens: int = 192,
    ) -> None:
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens

    async def analyze(self, frames: list[CapturedFrame]) -> BatchAnalysis:
        if not 1 <= len(frames) <= 4:
            raise ValueError("Step3 accepts one to four frames")
        batch_id = f"vb_{uuid.uuid4().hex[:12]}"
        started = time.monotonic()
        deadline = started + self.timeout
        messages = self._messages(frames)
        last_error: Exception | None = None
        previous_raw: str | None = None
        for attempt in range(2):
            request_messages = messages
            if attempt and previous_raw is not None:
                request_messages = messages + [
                    {"role": "assistant", "content": previous_raw},
                    {"role": "user", "content": CORRECTION_PROMPT},
                ]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last_error = TimeoutError("Step3 batch exceeded its total timeout")
                break
            try:
                response = await self.http.post(
                    f"{self.base_url}/chat/completions",
                    json={
                        "model": self.model,
                        "messages": request_messages,
                        "temperature": 0,
                        "max_tokens": self.max_tokens,
                        "response_format": self._response_format(frames),
                    },
                    timeout=remaining,
                )
                response.raise_for_status()
            except httpx.HTTPError as error:
                last_error = error
                break
            try:
                raw = response.json()["choices"][0]["message"]["content"]
                if not isinstance(raw, str):
                    raise TypeError("Step3 response content must be a string")
                previous_raw = raw
                observations = parse_stepfun_response(
                    raw,
                    [frame.frame_id for frame in frames],
                    {frame.frame_id: frame.captured_at for frame in frames},
                )
            except (KeyError, IndexError, TypeError, ValueError) as error:
                last_error = error
                continue
            return BatchAnalysis(
                batch_id=batch_id,
                observations=observations,
                model_name=self.model,
                latency_ms=round((time.monotonic() - started) * 1000),
                status="completed",
            )
        return BatchAnalysis(
            batch_id=batch_id,
            observations=[],
            model_name=self.model,
            latency_ms=round((time.monotonic() - started) * 1000),
            status="analysis_failed",
            error=str(last_error)[:300] if last_error else "unknown Step3 error",
        )

    async def health(self) -> bool:
        try:
            response = await self.http.get(f"{self.base_url}/models", timeout=2.0)
            return response.is_success
        except httpx.HTTPError:
            return False

    @staticmethod
    def _messages(frames: list[CapturedFrame]) -> list[dict]:
        content: list[dict] = [
            {
                "type": "text",
                "text": user_prompt(
                    [frame.frame_id for frame in frames],
                    [frame.captured_at for frame in frames],
                ),
            }
        ]
        for frame in frames:
            encoded = base64.b64encode(frame.path.read_bytes()).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                }
            )
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]

    @staticmethod
    def _response_format(frames: list[CapturedFrame]) -> dict:
        def observation(frame_id: str) -> dict:
            return {
                "type": "object",
                "properties": {
                    "frame_id": {"const": frame_id},
                    "person": {
                        "type": "string",
                        "enum": ["present", "absent", "uncertain"],
                    },
                    "phone": {
                        "type": "string",
                        "enum": ["visible", "not_visible", "uncertain"],
                    },
                    "cup": {
                        "type": "string",
                        "enum": ["visible", "not_visible", "uncertain"],
                    },
                    "cup_motion": {
                        "type": "string",
                        "enum": ["stable", "changed", "uncertain"],
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": {"type": "string", "maxLength": 6},
                },
                "required": [
                    "frame_id",
                    "person",
                    "phone",
                    "cup",
                    "cup_motion",
                    "confidence",
                    "evidence",
                ],
                "additionalProperties": False,
            }

        schema = {
            "type": "object",
            "properties": {
                "frames": {
                    "type": "array",
                    "prefixItems": [observation(frame.frame_id) for frame in frames],
                    "minItems": len(frames),
                    "maxItems": len(frames),
                }
            },
            "required": ["frames"],
            "additionalProperties": False,
        }
        return {
            "type": "json_schema",
            "json_schema": {"name": "step3_observations", "schema": schema},
        }
