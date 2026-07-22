import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from focus.infrastructure.stepfun_vlm import StepFunVlmClient
from focus.models import CapturedFrame


@pytest.mark.asyncio
async def test_multimage_contract_preserves_order_and_constraints(tmp_path) -> None:
    requests: list[dict] = []
    frames: list[CapturedFrame] = []
    for index in range(2):
        path = tmp_path / f"f-{index}.jpg"
        path.write_bytes(b"\xff\xd8" + bytes([index]) + b"\xff\xd9")
        frames.append(
            CapturedFrame(
                frame_id=f"f-{index}",
                captured_at=datetime.now(UTC) + timedelta(seconds=index),
                path=path,
                sequence=index + 1,
                latency_ms=10,
            )
        )

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "frames": [
                                        {
                                            "frame_id": frame.frame_id,
                                            "person": "present",
                                            "phone": "not_visible",
                                            "cup": "visible",
                                            "cup_motion": "stable",
                                            "confidence": 0.9,
                                            "evidence": "一人和杯子清晰可见",
                                        }
                                        for frame in frames
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = StepFunVlmClient(
            http=http, base_url="http://step/v1", model="step3-vl-focus"
        )
        result = await client.analyze(frames)

    assert result.status == "completed"
    assert requests[0]["temperature"] == 0
    assert requests[0]["max_tokens"] == 192
    assert requests[0]["model"] == "step3-vl-focus"
    schema = requests[0]["response_format"]["json_schema"]["schema"]
    prefix = schema["properties"]["frames"]["prefixItems"]
    assert [item["properties"]["frame_id"]["const"] for item in prefix] == [
        "f-0",
        "f-1",
    ]
    assert schema["properties"]["frames"]["minItems"] == 2
    assert schema["properties"]["frames"]["maxItems"] == 2
    assert prefix[0]["properties"]["evidence"]["maxLength"] == 12
    content = requests[0]["messages"][1]["content"]
    image_urls = [
        part["image_url"]["url"] for part in content if part["type"] == "image_url"
    ]
    assert len(image_urls) == 2
    assert image_urls[0] != image_urls[1]


@pytest.mark.asyncio
async def test_invalid_json_gets_exactly_one_correction_retry(tmp_path) -> None:
    path = tmp_path / "frame.jpg"
    path.write_bytes(b"jpeg")
    frame = CapturedFrame(
        frame_id="f-1",
        captured_at=datetime.now(UTC),
        path=path,
        sequence=1,
        latency_ms=1,
    )
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "not json"}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = StepFunVlmClient(http=http, base_url="http://step/v1", model="step3")
        result = await client.analyze([frame])
    assert calls == 2
    assert result.status == "analysis_failed"
    assert result.observations == []


@pytest.mark.asyncio
async def test_http_failure_is_not_retried(tmp_path) -> None:
    path = tmp_path / "frame.jpg"
    path.write_bytes(b"jpeg")
    frame = CapturedFrame(
        frame_id="f-1",
        captured_at=datetime.now(UTC),
        path=path,
        sequence=1,
        latency_ms=1,
    )
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, text="warming up")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = StepFunVlmClient(http=http, base_url="http://step/v1", model="step3")
        result = await client.analyze([frame])

    assert calls == 1
    assert result.status == "analysis_failed"
