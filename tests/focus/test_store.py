import os
from datetime import UTC, datetime, timedelta

from focus.models import FocusEvent, FocusMode, FocusSession, SessionState
from focus.infrastructure.session_store import FileSessionStore


def test_store_marks_unfinished_session_interrupted(tmp_path) -> None:
    store = FileSessionStore(tmp_path)
    session = FocusSession(
        session_id="fs_test",
        mode=FocusMode.DEMO,
        duration_seconds=90,
        state=SessionState.RUNNING,
        created_at=datetime.now(UTC),
    )
    store.save_session(session)
    interrupted = store.mark_unfinished_interrupted()
    assert interrupted == ["fs_test"]
    assert store.load_session("fs_test").interrupted is True
    assert store.load_session("fs_test").state is SessionState.FAILED


def test_store_cleans_sessions_older_than_retention(tmp_path) -> None:
    store = FileSessionStore(tmp_path)
    session = FocusSession(
        session_id="fs_old",
        mode=FocusMode.DEMO,
        duration_seconds=90,
        state=SessionState.COMPLETED,
        created_at=datetime.now(UTC) - timedelta(days=2),
    )
    store.save_session(session)
    old = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    os.utime(store.session_dir("fs_old"), (old, old))
    removed = store.cleanup_expired(retention_hours=24, now=datetime.now(UTC))
    assert removed == ["fs_old"]
    assert not store.session_dir("fs_old").exists()


def test_store_load_events_keeps_order_and_applies_limit(tmp_path) -> None:
    store = FileSessionStore(tmp_path)
    for index in range(3):
        store.append_event(
            FocusEvent(
                event_id=f"evt-{index}",
                session_id="fs_events",
                type="stats.updated",
                data={"index": index},
            )
        )

    events = store.load_events("fs_events", limit=2)

    assert [event.event_id for event in events] == ["evt-1", "evt-2"]


def test_store_returns_most_recent_session(tmp_path) -> None:
    store = FileSessionStore(tmp_path)
    for session_id, created_at in (
        ("fs_older", datetime(2026, 1, 1, tzinfo=UTC)),
        ("fs_newer", datetime(2026, 1, 2, tzinfo=UTC)),
    ):
        store.save_session(
            FocusSession(
                session_id=session_id,
                mode=FocusMode.DEMO,
                duration_seconds=90,
                state=SessionState.COMPLETED,
                created_at=created_at,
            )
        )

    assert store.latest_session().session_id == "fs_newer"


def test_store_prefers_latest_session_with_visual_observations(tmp_path) -> None:
    store = FileSessionStore(tmp_path)
    observed = FocusSession(
        session_id="fs_observed",
        mode=FocusMode.DEMO,
        duration_seconds=90,
        state=SessionState.COMPLETED,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    observed.stats.analyzed_frames = 8
    store.save_session(observed)
    store.save_session(
        FocusSession(
            session_id="fs_newer_smoke",
            mode=FocusMode.DEMO,
            duration_seconds=1,
            state=SessionState.COMPLETED,
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
    )

    assert store.latest_session().session_id == "fs_observed"
