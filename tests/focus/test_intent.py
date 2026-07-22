import pytest

from focus.intent import FocusIntent, match_focus_intent, normalize_zh_text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("开始 专注！", FocusIntent.START),
        ("请开始统计。", FocusIntent.START),
        ("结束专注并生成总结", FocusIntent.STOP),
        ("统计 到哪了？", FocusIntent.STATUS),
        ("现在怎么样", FocusIntent.STATUS),
        ("不要统计了", FocusIntent.CANCEL),
    ],
)
def test_focus_commands(text: str, expected: FocusIntent) -> None:
    assert match_focus_intent(text) is expected


def test_normal_conversation_does_not_start_session() -> None:
    assert match_focus_intent("今天适合专注做什么？") is None


def test_normalize_full_width_spaces_and_punctuation() -> None:
    assert normalize_zh_text("　开始，统计！ ") == "开始统计"
