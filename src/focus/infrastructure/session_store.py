from __future__ import annotations

import json
import os
from pathlib import Path

from focus.models import FocusEvent, FocusReport, FocusSession, SessionState


class FileSessionStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def session_dir(self, session_id: str) -> Path:
        if not session_id or "/" in session_id or ".." in session_id:
            raise ValueError("invalid session_id")
        return self.root / session_id

    def frame_dir(self, session_id: str) -> Path:
        path = self.session_dir(session_id) / "frames"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_session(self, session: FocusSession) -> None:
        self._atomic_json(
            self.session_dir(session.session_id) / "session.json",
            session.model_dump(mode="json"),
        )

    def load_session(self, session_id: str) -> FocusSession:
        raw = (self.session_dir(session_id) / "session.json").read_text(encoding="utf-8")
        return FocusSession.model_validate_json(raw)

    def save_report(self, report: FocusReport) -> None:
        self._atomic_json(
            self.session_dir(report.session_id) / "report.json",
            report.model_dump(mode="json"),
        )

    def load_report(self, session_id: str) -> FocusReport:
        raw = (self.session_dir(session_id) / "report.json").read_text(encoding="utf-8")
        return FocusReport.model_validate_json(raw)

    def append_event(self, event: FocusEvent) -> None:
        directory = self.session_dir(event.session_id)
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(event.model_dump_json() + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def mark_unfinished_interrupted(self) -> list[str]:
        changed: list[str] = []
        terminal = {SessionState.COMPLETED, SessionState.CANCELLED, SessionState.FAILED}
        for path in self.root.glob("*/session.json"):
            session = FocusSession.model_validate_json(path.read_text(encoding="utf-8"))
            if session.state in terminal:
                continue
            session.state = SessionState.FAILED
            session.interrupted = True
            self.save_session(session)
            changed.append(session.session_id)
        return changed

    @staticmethod
    def _atomic_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
