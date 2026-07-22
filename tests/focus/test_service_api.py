from pathlib import Path

from fastapi.testclient import TestClient

from focus.api import create_app
from focus.events import EventHub
from focus.infrastructure.session_store import FileSessionStore
from focus.models import FocusEvent, SessionState
from focus.service import FocusService


def make_client(tmp_path: Path) -> tuple[TestClient, FocusService]:
    service = FocusService(store=FileSessionStore(tmp_path), robot=None, vision=None)
    return TestClient(create_app(service)), service


def test_create_reuses_active_session_and_stop_is_idempotent(tmp_path) -> None:
    client, _ = make_client(tmp_path)
    first = client.post("/api/focus/sessions", json={"mode": "demo", "duration_seconds": 90})
    assert first.status_code == 201
    session_id = first.json()["session_id"]
    assert first.json()["state"] == "running"

    duplicate = client.post("/api/focus/sessions", json={"mode": "demo"})
    assert duplicate.status_code == 200
    assert duplicate.json()["session_id"] == session_id
    assert duplicate.json()["reused_existing_session"] is True

    stopped = client.post(f"/api/focus/sessions/{session_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "completed"
    assert client.post(f"/api/focus/sessions/{session_id}/stop").status_code == 200

    report = client.get(f"/api/focus/sessions/{session_id}/report")
    assert report.status_code == 200
    assert report.json()["focus_proxy_score"] is None
    assert "有效视觉样本不足" in report.json()["summary"]


def test_cancel_is_idempotent_and_has_no_report(tmp_path) -> None:
    client, _ = make_client(tmp_path)
    created = client.post("/api/focus/sessions", json={"mode": "demo"}).json()
    url = f"/api/focus/sessions/{created['session_id']}/cancel"
    assert client.post(url).json()["state"] == "cancelled"
    assert client.post(url).json()["state"] == "cancelled"
    assert client.get(f"/api/focus/sessions/{created['session_id']}/report").status_code == 404


def test_health_is_unhealthy_without_sdk_but_model_degradation_is_distinct(tmp_path) -> None:
    client, _ = make_client(tmp_path)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "unhealthy"
    assert response.json()["components"]["watcher_sdk"]["status"] == "unhealthy"
    assert response.json()["components"]["stepfun_vlm"]["status"] == "degraded"


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
