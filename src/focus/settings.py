from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class FocusSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    focus_host: str = "0.0.0.0"
    focus_port: int = Field(default=8780, ge=1, le=65_535)
    focus_data_dir: Path = Path("runtime/focus")

    focus_enable_robot: bool = True
    watcher_pairing_code: SecretStr = SecretStr("")
    watcher_sdk_discovery_port: int = 37_021
    watcher_sdk_websocket_port: int = 8_766
    watcher_sdk_host: str = "auto"

    focus_demo_duration_seconds: int = 90
    focus_demo_capture_interval_seconds: float = 10.0
    focus_normal_capture_interval_seconds: float = 30.0
    focus_batch_size: int = 4
    focus_voice_idle_seconds: float = 5.0

    stepfun_vlm_base_url: str = "http://127.0.0.1:8040/v1"
    stepfun_vlm_model: str = "step3-vl-focus"
    stepfun_vlm_timeout_seconds: float = 30.0
    stepfun_vlm_max_tokens: int = 192

    fast_asr_backend: str = "qwen_asr"
    qwen_asr_base_url: str = "http://127.0.0.1:8010"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:0.6b"
    qwen_tts_base_url: str = "http://127.0.0.1:8030"
    qwen_tts_model: str = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
    qwen_tts_voice: str = "Aiden"
