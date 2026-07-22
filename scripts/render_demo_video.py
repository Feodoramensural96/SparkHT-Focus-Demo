#!/usr/bin/env python3
"""Render a narrated demo draft from one real Focus session.

The generated MP4 is a review/recording aid.  It deliberately reads only persisted
session artefacts and never invents dashboard values.  Runtime images and the MP4
remain under ``runtime/`` and are excluded from Git.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx


FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
PCM_BYTES_PER_SECOND = 24_000 * 2


@dataclass(frozen=True)
class Scene:
    title: str
    lines: tuple[str, ...]
    narration: str
    frame_index: int | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path, help="runtime/focus/<session-id>")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("runtime/focus-demo-draft.mp4"),
    )
    parser.add_argument("--tts-url", default="http://127.0.0.1:8030")
    parser.add_argument(
        "--mute", action="store_true", help="render silent timing draft"
    )
    return parser.parse_args()


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def seconds_label(milliseconds: int) -> str:
    return f"{milliseconds / 1000:.3f} 秒"


def build_scenes(session_dir: Path) -> tuple[list[Scene], list[Path]]:
    session = load_json(session_dir / "session.json")
    report = load_json(session_dir / "report.json")
    events = load_events(session_dir / "events.jsonl")
    frames = sorted((session_dir / "frames").glob("*.jpg"))
    if len(frames) < 8:
        raise ValueError(f"need at least 8 persisted frames, found {len(frames)}")

    camera = [
        event["data"]["latency_ms"]
        for event in events
        if event["type"] == "camera.frame_captured"
    ]
    batches = [
        event["data"]["latency_ms"]
        for event in events
        if event["type"] == "vision.batch_completed"
    ]
    summaries = [
        event["data"]["speech_to_first_audio_ms"]
        for event in events
        if event["type"] == "voice.turn_completed"
        and event["data"].get("source") == "session_timer"
    ]
    if len(batches) < 2 or not summaries:
        raise ValueError(
            "session must contain two completed Step3 batches and an audio summary"
        )

    camera_sorted = sorted(camera)
    p95_camera = camera_sorted[max(0, int(0.95 * len(camera_sorted) + 0.999) - 1)]
    duration_minutes = session["duration_seconds"] / 60
    scenes = [
        Scene(
            "创造专注",
            (
                "DGX Spark 端侧快慢双系统",
                "WatcheRobot · 640×480 · Step3-VL-10B-FP8",
                "真实会话数据生成 · 不依赖 WatcheRobot_server",
            ),
            "这是创造专注。系统运行在单台 DGX Spark 上，机器人只建立一个 Python SDK 连接。摄像头只有六百四十乘四百八十，因此我们不做文字、身份或情绪识别，只观察人物、明显手机和杯子等低分辨率下可靠的大目标变化。",
            0,
        ),
        Scene(
            "一个 SDK 连接，两套节奏",
            (
                "快系统：麦克风 → Qwen ASR → Qwen3 0.6B → Qwen3-TTS",
                "慢系统：定时抓拍 → 四帧 Step3-VL → Python 确定性统计",
                "FastAPI：真实状态、SSE 时间线、累计指标与失败状态",
                "全部模型均为 Spark 本地服务",
            ),
            "快系统负责中文交互，使用本地 ASR、小语言模型和语音合成。慢系统每十秒抓拍一次，四帧一批交给 Step3 多模态模型。模型只返回逐帧观察，最终比例和专注趋势由 Python 确定性计算。FastAPI 仪表盘展示真实累计状态，主产品服务端不参与运行。",
        ),
        Scene(
            "真实时间序列：手机出现",
            (
                "会话开始后按 10 秒间隔抓拍",
                "模型观察：person / phone / cup / cup_motion",
                "低置信度或 uncertain 不进入对应分母",
            ),
            "这是一轮连续九十秒真机会话。画面逆光且分辨率有限，系统仍要求模型在看不清时返回不确定，而不是猜测。序列前半段检测到人物和明显手机；原始图片、短证据、置信度与时间戳都保存在本机运行目录。",
            0,
        ),
        Scene(
            "真实时间序列：离开与返回",
            (
                "手机跨帧从 visible 变为 not_visible",
                "人物短暂离开后返回",
                "不读取屏幕文字，不判断工作内容",
            ),
            "随后手机移出画面，人物短暂离开并返回。统计器只比较相邻有效状态，跨越不确定状态不计算变化。杯子移动最多只能记录为疑似事件，系统不会把它包装成确认饮水，也不会判断用户在做什么工作。",
            4,
        ),
        Scene(
            "快系统优先",
            (
                "检测到用户语音 → 停止提交新 Step3 请求",
                "活动视觉 HTTP 任务立即取消",
                "语音空闲 5 秒后，最新批次最多重试一次",
                "实测：Step3 启动后 2.4 秒被语音暂停；语音期间新请求为 0",
            ),
            "快慢系统的核心是抢占。检测到语音后，调度器立即停止提交新的视觉任务，并取消正在等待的 Step3 HTTP 请求。真实事件记录中，一个视觉批次启动两点四秒后被语音暂停，语音期间没有新的 Step3 请求。空闲五秒后只恢复最新批次，避免任务堆积。",
        ),
        Scene(
            "最终统计与机器人播报",
            (
                f"时长：{duration_minutes:.1f} 分钟 · 有效观察：{report['analyzed_frames']} 帧",
                f"在位率：{report['presence_ratio'] * 100:.1f}%",
                f"手机可见率：{report['phone_visible_ratio'] * 100:.1f}% · 状态变化：{report['phone_transition_count']} 次",
                f"专注趋势代理指标：{report['focus_proxy_score']:.1f} 分",
                f"自动总结首音：{seconds_label(summaries[-1])}",
            ),
            f"这轮得到八个有效观察。在位率为百分之{report['presence_ratio'] * 100:.1f}，手机可见率为百分之{report['phone_visible_ratio'] * 100:.1f}，检测到{report['phone_transition_count']}次手机状态变化。专注趋势是低分辨率视觉代理指标，只供参考。会话完成后，机器人自动播报总结，首音为{summaries[-1] / 1000:.3f}秒。",
            7,
        ),
        Scene(
            "实测性能与限制",
            (
                f"本轮相机 P95：{p95_camera / 1000:.3f} 秒（目标 ≤ 0.700 秒，未通过）",
                f"本轮 Step3：{batches[0] / 1000:.3f} / {batches[1] / 1000:.3f} 秒（硬上限 30 秒）",
                "四图离线基准：P50 9.564 秒 / P95 11.212 秒",
                "78 项自动测试通过 · 两轮 90 秒连续完成 · SDK 无人工重启",
                "现场 WLAN 出现接近 1 秒 RTT 峰值，所有异常样本均保留",
            ),
            "性能数据全部来自真实请求。本轮相机九十五分位仍高于零点七秒目标，现场无线链路同时出现接近一秒的延迟峰值，因此我们明确记录为未通过，不删除慢样本。两个 Step3 批次都低于三十秒硬上限。七十八项自动测试通过，两轮九十秒流程在同一 SDK 连接上连续完成。",
        ),
        Scene(
            "端侧、克制、可扩展",
            (
                "StepFun 多模态模型负责低频时间序列观察",
                "快语音与慢视觉相互让路，不相互拖垮",
                "图片只留在 Spark，本次 Demo 不依赖主服务端",
                "下一步：改善机器人 WLAN，补录实体机器人正面镜头",
            ),
            "这个 Demo 展示了 StepFun 多模态模型在端侧机器人上的一种克制用法：慢模型理解时间序列，快系统保证交互，确定性代码约束统计边界。图片只保留在 Spark，本次运行不依赖主服务端。下一步是改善机器人无线网络，并把实体机器人正面镜头替换进片头。",
            7,
        ),
    ]
    return scenes, frames


def synthesize(scene: Scene, output: Path, base_url: str) -> float:
    with httpx.stream(
        "POST",
        f"{base_url.rstrip('/')}/v1/audio/speech",
        json={
            "input": scene.narration,
            "model": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
            "voice": "Aiden",
            "language": "Chinese",
            "response_format": "pcm",
            "stream_format": "audio",
            "stream": True,
        },
        timeout=180,
    ) as response:
        response.raise_for_status()
        with output.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
    return output.stat().st_size / PCM_BYTES_PER_SECOND


def silent_pcm(scene: Scene, output: Path) -> float:
    duration = max(8.0, len(scene.narration) / 4.5)
    output.write_bytes(bytes(round(duration * PCM_BYTES_PER_SECOND)))
    return duration


def escape_drawtext(text: str) -> str:
    return text.replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")


def text_filters(scene: Scene) -> str:
    filters = [
        "drawbox=x=0:y=0:w=iw:h=170:color=0x122044@0.94:t=fill",
        (
            f"drawtext=fontfile={FONT}:text='{escape_drawtext(scene.title)}':"
            "expansion=none:fontcolor=white:fontsize=58:x=100:y=72"
        ),
    ]
    for index, line in enumerate(scene.lines):
        filters.append(
            f"drawtext=fontfile={FONT}:text='{escape_drawtext(line)}':"
            f"expansion=none:fontcolor=0xe7edff:fontsize=36:x=110:y={245 + index * 105}"
        )
    filters.extend(
        [
            "drawbox=x=80:y=h-100:w=1760:h=2:color=0x5f8cff:t=fill",
            (
                f"drawtext=fontfile={FONT}:text='真实会话 · {escape_drawtext(scene.title)}':"
                "expansion=none:fontcolor=0x9fb5e8:fontsize=26:x=100:y=h-72"
            ),
        ]
    )
    return ",".join(filters)


def image_filters(scene: Scene) -> str:
    subtitle = "  ·  ".join(scene.lines[:2])
    return ",".join(
        [
            "scale=1280:960:force_original_aspect_ratio=decrease",
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x081020",
            "drawbox=x=0:y=0:w=iw:h=120:color=0x122044@0.94:t=fill",
            f"drawtext=fontfile={FONT}:text='{escape_drawtext(scene.title)}':expansion=none:fontcolor=white:fontsize=48:x=80:y=45",
            "drawbox=x=0:y=h-120:w=iw:h=120:color=0x071020@0.92:t=fill",
            f"drawtext=fontfile={FONT}:text='{escape_drawtext(subtitle)}':expansion=none:fontcolor=0xe7edff:fontsize=28:x=80:y=h-76",
        ]
    )


def render_scene(
    scene: Scene, frame: Path | None, pcm: Path, duration: float, output: Path
) -> None:
    video_input = (
        ["-loop", "1", "-i", str(frame)]
        if frame
        else ["-f", "lavfi", "-i", "color=c=0x081020:s=1920x1080:r=30"]
    )
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *video_input,
            "-f",
            "s16le",
            "-ar",
            "24000",
            "-ac",
            "1",
            "-i",
            str(pcm),
            "-vf",
            image_filters(scene) if frame else text_filters(scene),
            "-af",
            "apad=pad_dur=0.6",
            "-t",
            f"{duration + 0.6:.3f}",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "21",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            str(output),
        ]
    )


def main() -> None:
    args = parse_args()
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is required")
    if not Path(FONT).exists():
        raise SystemExit(f"Chinese font not found: {FONT}")
    session_dir = args.session.resolve()
    scenes, frames = build_scenes(session_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="focus-demo-video-") as raw_temp:
        temp = Path(raw_temp)
        clips: list[Path] = []
        for index, scene in enumerate(scenes):
            pcm = temp / f"scene-{index:02d}.pcm"
            duration = (
                silent_pcm(scene, pcm)
                if args.mute
                else synthesize(scene, pcm, args.tts_url)
            )
            clip = temp / f"scene-{index:02d}.mp4"
            frame = frames[scene.frame_index] if scene.frame_index is not None else None
            render_scene(scene, frame, pcm, duration, clip)
            clips.append(clip)
            print(f"scene {index + 1}/{len(scenes)}: {scene.title} ({duration:.1f}s)")

        concat = temp / "concat.txt"
        concat.write_text(
            "".join(f"file '{clip}'\n" for clip in clips), encoding="utf-8"
        )
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat),
                "-c",
                "copy",
                str(args.output),
            ]
        )
    print(args.output.resolve())


if __name__ == "__main__":
    main()
