from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import FrameObservation


SYSTEM_PROMPT = """你是低分辨率桌面场景的时间序列观察器。输入是同一摄像头按时间排序的多张图片。
只判断：人是否在场、明显的手机是否可见、杯子是否可见、杯子相对前一帧是否明显移动。
禁止 OCR，禁止识别身份、情绪、工作内容或屏幕内容。
杯子变化只能写为 suspected，不得断言用户喝了水。
看不清时必须输出 uncertain，不要猜测。
只返回符合给定 schema 的 JSON，不输出 Markdown 或解释。"""

CORRECTION_PROMPT = (
    "仅修复上一响应的 JSON 格式和 schema，不增加、删除或猜测观察结果。只返回 JSON。"
)


def response_schema_prompt(frame_ids: list[str]) -> str:
    frames = [
        {
            "frame_id": frame_id,
            "person": "present|absent|uncertain",
            "phone": "visible|not_visible|uncertain",
            "cup": "visible|not_visible|uncertain",
            "cup_motion": "stable|changed|uncertain",
            "confidence": "按图估计",
            "evidence": "不超过6个中文字",
        }
        for frame_id in frame_ids
    ]
    skeleton = json.dumps({"frames": frames}, ensure_ascii=False, separators=(",", ":"))
    return (
        "返回对象必须严格采用以下骨架；逐字保留其中的真实 frame_id、数量和顺序。"
        "confidence 的占位文字必须替换为 0 到 1 的数值：\n"
        f"{skeleton}"
    )


def user_prompt(frame_ids: list[str], timestamps: list[datetime]) -> str:
    ids = "、".join(frame_ids)
    times = "、".join(item.isoformat() for item in timestamps)
    return (
        f"以下图片依次为 {ids}，拍摄时间依次为 {times}。\n"
        "逐帧观察，并比较相邻帧中的手机和杯子状态。\n"
        "confidence 必须按该帧可见清晰度独立估计：清晰约 0.85，部分遮挡约 0.65，"
        "只有整体无法判断时才低于 0.55。\n"
        "evidence 使用不超过 6 个中文字，只写可见证据。\n"
        f"{response_schema_prompt(frame_ids)}"
    )


class _StepfunFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frame_id: str
    person: Literal["present", "absent", "uncertain"]
    phone: Literal["visible", "not_visible", "uncertain"]
    cup: Literal["visible", "not_visible", "uncertain"]
    cup_motion: Literal["stable", "changed", "uncertain"]
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(max_length=30)


class _StepfunEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frames: list[_StepfunFrame]


def _first_json_object(raw: str) -> dict:
    decoder = json.JSONDecoder()
    for index, character in enumerate(raw):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("Step3 response does not contain a complete JSON object")


def parse_stepfun_response(
    raw: str,
    expected_frame_ids: list[str],
    captured_at: dict[str, datetime],
) -> list[FrameObservation]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = _first_json_object(raw)
    envelope = _StepfunEnvelope.model_validate(payload)
    returned_ids = [frame.frame_id for frame in envelope.frames]
    if returned_ids != expected_frame_ids:
        raise ValueError(
            f"Step3 frame_ids must exactly match request order: {returned_ids!r}"
        )
    return [
        FrameObservation(
            **frame.model_dump(),
            captured_at=captured_at[frame.frame_id],
        )
        for frame in envelope.frames
    ]
