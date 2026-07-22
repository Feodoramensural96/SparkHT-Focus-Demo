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
from .voice import EnergyVad, VoiceController


class FocusRuntime:
    def __init__(self, settings: FocusSettings) -> None:
        self.settings = settings
        self.http = httpx.AsyncClient(limits=httpx.Limits(max_connections=8))
        self._pair_lock = asyncio.Lock()
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
            demo_duration_seconds=settings.focus_demo_duration_seconds,
            normal_duration_seconds=settings.focus_normal_duration_seconds,
            batch_size=settings.focus_batch_size,
            voice_idle_seconds=settings.focus_voice_idle_seconds,
            max_frames_per_session=settings.focus_max_frames_per_session,
        )
        self.voice = None
        if self.robot is not None:
            self.voice = self._build_voice_controller(self.robot)
        self.app = create_app(
            self.service,
            pair_robot=self.pair_robot if settings.focus_enable_robot else None,
        )
        self._voice_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await asyncio.gather(self.service.start(), self._prewarm_fast_chain())
        if self.voice is not None:
            self._start_voice_supervisor()

    def _start_voice_supervisor(self) -> None:
        if self.robot is None or self.voice is None:
            return
        self._voice_task = asyncio.create_task(
            self._run_voice_supervisor(self.robot, self.voice),
            name="focus-voice-supervisor",
        )

    async def _run_voice_supervisor(
        self, robot: WatcheRobotAdapter, voice: VoiceController
    ) -> None:
        while True:
            if not robot.connected:
                try:
                    await robot.connect()
                    await self.service.refresh_light()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await asyncio.sleep(2.0)
                    continue
            try:
                await voice.run()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await robot.close()
            await asyncio.sleep(2.0)

    async def _stop_voice_supervisor(self) -> None:
        if self.voice is not None:
            await self.voice.stop()
        task, self._voice_task = self._voice_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _build_voice_controller(self, robot: WatcheRobotAdapter) -> VoiceController:
        return VoiceController(
            service=self.service,
            robot=robot,
            asr=self.asr,
            llm=self.llm,
            tts=self.tts,
            vad=EnergyVad(threshold=self.settings.focus_vad_idle_threshold),
            focus_vad_threshold=self.settings.focus_vad_threshold,
        )

    async def pair_robot(self, pairing_code: str) -> dict[str, str]:
        """Pair and hot-attach the robot; the code remains process-memory only."""
        async with self._pair_lock:
            if self.robot is not None and self.robot.connected:
                return {
                    "status": "already_connected",
                    "message": "机器人 SDK 已连接，无需重复配对。",
                }

            old_robot = self.robot
            await self._stop_voice_supervisor()
            self.voice = None
            self.robot = None
            if old_robot is not None:
                self.service.detach_robot(old_robot)
                await old_robot.close()

            candidate = WatcheRobotAdapter(
                pairing_code=pairing_code,
                discovery_port=self.settings.watcher_sdk_discovery_port,
                websocket_port=self.settings.watcher_sdk_websocket_port,
                host=self.settings.watcher_sdk_host,
            )
            try:
                await candidate.connect()
            except Exception:
                await candidate.close()
                raise

            self.robot = candidate
            self.voice = self._build_voice_controller(candidate)
            await self.service.attach_robot(candidate)
            self._start_voice_supervisor()
            return {"status": "connected", "message": "机器人 SDK 配对成功。"}

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
        await self._stop_voice_supervisor()
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
