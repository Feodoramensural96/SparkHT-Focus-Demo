from datetime import UTC, datetime

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
