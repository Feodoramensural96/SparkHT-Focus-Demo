import os
from datetime import UTC, datetime, timedelta

from focus.models import FocusMode, FocusSession, SessionState
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
