from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from focus.prompts import parse_stepfun_response, user_prompt


TIMES = {
    "f-001": datetime(2026, 7, 22, 5, 0, tzinfo=UTC),
    "f-002": datetime(2026, 7, 22, 5, 0, tzinfo=UTC) + timedelta(seconds=10),
}

VALID = """{
  "frames": [
    {"frame_id":"f-001","person":"present","phone":"visible","cup":"visible","cup_motion":"stable","confidence":0.91,"evidence":"桌前有人，手机杯子可见"},
    {"frame_id":"f-002","person":"absent","phone":"not_visible","cup":"visible","cup_motion":"changed","confidence":0.82,"evidence":"座位无人，杯子位置改变"}
  ]
}"""


def test_parse_valid_json() -> None:
    parsed = parse_stepfun_response(VALID, list(TIMES), TIMES)
    assert [item.frame_id for item in parsed] == ["f-001", "f-002"]
    assert parsed[1].captured_at == TIMES["f-002"]


def test_user_prompt_embeds_exact_frame_ids_in_output_skeleton() -> None:
    prompt = user_prompt(list(TIMES), list(TIMES.values()))
    assert '"frame_id":"f-001"' in prompt
    assert '"frame_id":"f-002"' in prompt
    assert prompt.index('"frame_id":"f-001"') < prompt.index('"frame_id":"f-002"')


def test_extract_json_from_markdown_fence() -> None:
    parsed = parse_stepfun_response(f"说明\n```json\n{VALID}\n```", list(TIMES), TIMES)
    assert len(parsed) == 2


@pytest.mark.parametrize(
    "bad",
    [
        VALID.replace('"present"', '"sleeping"', 1),
        VALID.replace('"f-002"', '"f-999"', 1),
        VALID.replace("0.91", "1.91", 1),
    ],
)
def test_reject_unknown_enum_wrong_frame_and_confidence(bad: str) -> None:
    with pytest.raises((ValueError, ValidationError)):
        parse_stepfun_response(bad, list(TIMES), TIMES)
