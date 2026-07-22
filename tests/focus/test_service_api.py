from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from focus.api import create_app
from focus.events import EventHub
from focus.infrastructure.session_store import FileSessionStore
from focus.models import FocusEvent, FocusSessionCreate, SessionState
from focus.service import FocusService


def make_client(tmp_path: Path) -> tuple[TestClient, FocusService]:
    service = FocusService(store=FileSessionStore(tmp_path), robot=None, vision=None)
    return TestClient(create_app(service)), service


def test_create_reuses_active_session_and_stop_is_idempotent(tmp_path) -> None:
    client, _ = make_client(tmp_path)
    assert client.get("/api/focus/active").status_code == 404
    assert client.get("/api/focus/recent").status_code == 404
    first = client.post(
        "/api/focus/sessions", json={"mode": "demo", "duration_seconds": 90}
    )
    assert first.status_code == 201
    session_id = first.json()["session_id"]
    assert first.json()["state"] == "running"
    active = client.get("/api/focus/active")
    assert active.status_code == 200
    assert active.json()["session_id"] == session_id

    duplicate = client.post("/api/focus/sessions", json={"mode": "demo"})
    assert duplicate.status_code == 200
    assert duplicate.json()["session_id"] == session_id
    assert duplicate.json()["reused_existing_session"] is True

    stopped = client.post(f"/api/focus/sessions/{session_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "completed"
    assert client.post(f"/api/focus/sessions/{session_id}/stop").status_code == 200
    assert client.get("/api/focus/active").status_code == 404

    report = client.get(f"/api/focus/sessions/{session_id}/report")
    assert report.status_code == 200
    assert report.json()["focus_proxy_score"] is None
    assert "有效视觉样本不足" in report.json()["summary"]
    assert client.get("/api/focus/recent").json()["session_id"] == session_id


def test_cancel_is_idempotent_and_has_no_report(tmp_path) -> None:
    client, _ = make_client(tmp_path)
    created = client.post("/api/focus/sessions", json={"mode": "demo"}).json()
    url = f"/api/focus/sessions/{created['session_id']}/cancel"
    assert client.post(url).json()["state"] == "cancelled"
    assert client.post(url).json()["state"] == "cancelled"
    assert (
        client.get(f"/api/focus/sessions/{created['session_id']}/report").status_code
        == 404
    )


def test_health_is_unhealthy_without_sdk_but_model_degradation_is_distinct(
    tmp_path,
) -> None:
    client, _ = make_client(tmp_path)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "unhealthy"
    assert response.json()["components"]["watcher_sdk"]["status"] == "unhealthy"
    assert response.json()["components"]["stepfun_vlm"]["status"] == "degraded"


def test_dashboard_uses_compact_vertical_regions_and_real_model_name(tmp_path) -> None:
    client, _ = make_client(tmp_path)
    response = client.get("/")
    assert response.status_code == 200
    for text in (
        "机器人原始画面",
        "核心指标",
        "机器人对话",
        "事件时间线",
        "技术状态",
        "Step3-VL-10B-FP8",
        "机器人上行 · 麦克风 → SparkHT",
        "机器人下行 · SparkHT → 扬声器",
    ):
        assert text in response.text
    assert "fetch('/api/focus/active')" in response.text
    assert "fetch('/api/focus/recent')" in response.text
    assert "最近 Step3 批次" in response.text
    assert "display:flex;flex-direction:column" in response.text
    assert "width:640px;max-width:100%;aspect-ratio:4/3" in response.text
    assert "grid-template-columns" not in response.text
    for element_id in (
        "frame",
        "presence",
        "phone",
        "phoneTransitions",
        "drink",
        "score",
        "conversation",
        "timeline",
        "health",
    ):
        assert f'id="{element_id}"' in response.text


def test_event_history_reads_persisted_events(tmp_path) -> None:
    client, service = make_client(tmp_path)
    service.store.append_event(
        FocusEvent(
            event_id="evt-history",
            session_id="fs_history",
            type="voice.turn_completed",
            data={"reply": "这是机器人下行回复。"},
        )
    )

    response = client.get("/api/focus/sessions/fs_history/history")

    assert response.status_code == 200
    assert response.json()[0]["event_id"] == "evt-history"
    assert response.json()[0]["data"]["reply"] == "这是机器人下行回复。"


def test_event_hub_replays_after_last_event_id() -> None:
    hub = EventHub(max_events=3)
    for index in range(4):
        hub.publish(
            FocusEvent(
                event_id=f"e-{index}",
                session_id="fs_test",
                type="stats.updated",
                data={"index": index},
            )
        )
    assert [event.event_id for event in hub.replay("fs_test", "e-1")] == ["e-2", "e-3"]
    assert hub.replay("fs_test", "e-3") == []


@pytest.mark.asyncio
async def test_sdk_warmup_failure_marks_session_failed(tmp_path) -> None:
    class BrokenRobot:
        connected = True

        async def warmup_camera(self):
            raise RuntimeError("camera unavailable")

    class Vision:
        async def analyze(self, frames):
            raise AssertionError("must not analyze")

        async def health(self):
            return True

    service = FocusService(
        store=FileSessionStore(tmp_path), robot=BrokenRobot(), vision=Vision()
    )
    session, reused = await service.create_session(FocusSessionCreate())
    assert reused is False
    assert session.state is SessionState.FAILED
    assert session.degraded_components["watcher_sdk"] == "camera unavailable"
    assert (await service.stop_session(session.session_id)).state is SessionState.FAILED
    assert (
        await service.cancel_session(session.session_id)
    ).state is SessionState.FAILED


@pytest.mark.asyncio
async def test_sdk_connect_failure_keeps_http_service_available(tmp_path) -> None:
    class OfflineRobot:
        connected = False

        async def connect(self):
            raise TimeoutError("waiting for sdk.control.app")

        async def close(self):
            return None

    service = FocusService(
        store=FileSessionStore(tmp_path), robot=OfflineRobot(), vision=None
    )

    await service.start()

    assert (await service.health()).status == "unhealthy"
    await service.close()
