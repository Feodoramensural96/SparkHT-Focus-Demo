import importlib.util
import json
from pathlib import Path


_SCRIPT = Path(__file__).parents[2] / "scripts" / "summarize_focus_metrics.py"
_SPEC = importlib.util.spec_from_file_location("summarize_focus_metrics", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
summarize = _MODULE.summarize


def test_voice_latency_excludes_timer_summary(tmp_path) -> None:
    session = tmp_path / "fs_test"
    session.mkdir()
    events = [
        {
            "type": "voice.turn_completed",
            "data": {"intent": "start", "speech_to_first_audio_ms": 2400},
        },
        {
            "type": "voice.turn_completed",
            "data": {
                "source": "session_timer",
                "intent": "auto_summary",
                "speech_to_first_audio_ms": 600,
            },
        },
    ]
    (session / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events), encoding="utf-8"
    )

    metrics = summarize(session / "events.jsonl")

    assert metrics["speech_to_robot_first_audio"] == {
        "samples": 1,
        "p50_ms": 2400,
        "p95_ms": 2400,
        "observations_ms": [2400],
    }
