from __future__ import annotations

import asyncio

import httpx
import uvicorn

from .api import create_app
from .infrastructure.asr import QwenAsrClient
from .infrastructure.ollama import OllamaClient
from .infrastructure.qwen_tts import QwenTtsClient
from .infrastructure.session_store import FileSessionStore
from .infrastructure.stepfun_vlm import StepFunVlmClient
from .infrastructure.watcher_sdk import WatcheRobotAdapter
from .service import FocusService
from .settings import FocusSettings
from .voice import VoiceController


class FocusRuntime:
    def __init__(self, settings: FocusSettings) -> None:
        self.settings = settings
        self.http = httpx.AsyncClient(limits=httpx.Limits(max_connections=8))
        pairing_code = settings.watcher_pairing_code.get_secret_value()
        self.robot = None
        if settings.focus_enable_robot and pairing_code:
            self.robot = WatcheRobotAdapter(
                pairing_code=pairing_code,
                discovery_port=settings.watcher_sdk_discovery_port,
                websocket_port=settings.watcher_sdk_websocket_port,
                host=settings.watcher_sdk_host,
            )
        self.asr = QwenAsrClient(http=self.http, base_url=settings.qwen_asr_base_url)
        self.llm = OllamaClient(
            http=self.http,
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
        )
        self.tts = QwenTtsClient(
            http=self.http,
            base_url=settings.qwen_tts_base_url,
            model=settings.qwen_tts_model,
            voice=settings.qwen_tts_voice,
        )
        self.vision = StepFunVlmClient(
            http=self.http,
            base_url=settings.stepfun_vlm_base_url,
            model=settings.stepfun_vlm_model,
            timeout=settings.stepfun_vlm_timeout_seconds,
            max_tokens=settings.stepfun_vlm_max_tokens,
        )
        self.service = FocusService(
            store=FileSessionStore(settings.focus_data_dir),
            robot=self.robot,
            vision=self.vision,
            asr=self.asr,
            llm=self.llm,
            tts=self.tts,
            demo_capture_interval=settings.focus_demo_capture_interval_seconds,
            normal_capture_interval=settings.focus_normal_capture_interval_seconds,
            batch_size=settings.focus_batch_size,
            voice_idle_seconds=settings.focus_voice_idle_seconds,
        )
        self.voice = None
        if self.robot is not None:
            self.voice = VoiceController(
                service=self.service,
                robot=self.robot,
                asr=self.asr,
                llm=self.llm,
                tts=self.tts,
            )
        self.app = create_app(self.service)
        self._voice_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await asyncio.gather(self.service.start(), self._prewarm_fast_chain())
        if self.voice is not None:
            self._voice_task = asyncio.create_task(self.voice.run(), name="focus-voice")

    async def _prewarm_fast_chain(self) -> None:
        async def warm_asr() -> None:
            await self.asr.transcribe(b"\x00\x00" * 3_200)

        async def warm_llm() -> None:
            await self.llm.reply("只回答好", max_chinese_chars=2)

        async def warm_tts() -> None:
            stream = self.tts.synthesize("系统就绪")
            try:
                await anext(stream)
            finally:
                await stream.aclose()

        await asyncio.gather(warm_asr(), warm_llm(), warm_tts(), return_exceptions=True)

    async def close(self) -> None:
        if self.voice is not None:
            await self.voice.stop()
        if self._voice_task is not None:
            self._voice_task.cancel()
            try:
                await self._voice_task
            except asyncio.CancelledError:
                pass
        await self.service.close()
        await self.http.aclose()


async def serve(settings: FocusSettings | None = None) -> None:
    settings = settings or FocusSettings()
    runtime = FocusRuntime(settings)
    boot = asyncio.create_task(runtime.start(), name="focus-runtime-boot")
    server = uvicorn.Server(
        uvicorn.Config(
            runtime.app,
            host=settings.focus_host,
            port=settings.focus_port,
            log_level="info",
            access_log=True,
        )
    )
    try:
        await server.serve()
    finally:
        if not boot.done():
            boot.cancel()
            try:
                await boot
            except asyncio.CancelledError:
                pass
        elif boot.exception() is not None:
            boot.exception()
        await runtime.close()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
