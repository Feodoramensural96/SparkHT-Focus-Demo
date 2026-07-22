import asyncio
import threading
import time
from datetime import UTC, datetime

import pytest

from focus.infrastructure.watcher_sdk import WatcheRobotAdapter


class FakePlayback:
    def __init__(self, *, fail: bool = False) -> None:
        self.waited = False
        self.wait_timeout = None
        self.fail = fail

    def wait(self, timeout=None):
        self.waited = True
        self.wait_timeout = timeout
        if self.fail:
            raise TimeoutError("playback timeout")
        return self


class FakeJob:
    def __init__(self) -> None:
        self.wait_timeout = None

    def wait(self, timeout=None):
        self.wait_timeout = timeout
        return self


class FakeMicrophone:
    def __init__(self, parent) -> None:
        self.parent = parent
        self.closed = False

    def read(self, timeout=None):
        time.sleep(0.01)
        raise TimeoutError

    def close(self):
        if not self.closed:
            self.closed = True
            self.parent.microphone_close_calls += 1


class FakeRobot:
    def __init__(self) -> None:
        self.capture_calls = 0
        self.audio_chunks: list[bytes] = []
        self.playbacks: list[FakePlayback] = []
        self.audio_stop_calls = 0
        self.fail_playback = False
        self.microphone_open_calls = 0
        self.microphone_close_calls = 0
        self.animation_ids: list[str] = []
        self.behavior_ids: list[tuple[str, int]] = []
        self.motion_targets: list[dict] = []
        self.motion_jobs: list[FakeJob] = []
        self.animation_supported = True
        self.behavior_supported = True
        self.motion_supported = True
        self.closed = False
        self._closed = False
        self.camera = self.Camera(self)
        self.audio = self.Audio(self)
        self.microphone = self.Microphone(self)
        self.animation = self.Animation(self)
        self.behavior = self.Behavior(self)
        self.motion = self.Motion(self)

    class Camera:
        def __init__(self, parent) -> None:
            self.parent = parent

        def capture(self, **kwargs):
            self.parent.capture_calls += 1
            return type(
                "Image",
                (),
                {"data": b"jpeg-data", "timestamp": datetime.now(UTC).timestamp()},
            )()

    class Audio:
        def __init__(self, parent) -> None:
            self.parent = parent

        def play_pcm(self, data, **kwargs):
            self.parent.audio_chunks.append(data)
            playback = FakePlayback(fail=self.parent.fail_playback)
            self.parent.playbacks.append(playback)
            return playback

        def stop(self):
            self.parent.audio_stop_calls += 1
            return None

    class Microphone:
        def __init__(self, parent) -> None:
            self.parent = parent

        def open(self):
            self.parent.microphone_open_calls += 1
            return FakeMicrophone(self.parent)

    class Animation:
        def __init__(self, parent) -> None:
            self.parent = parent

        def play(self, animation_id):
            self.parent.animation_ids.append(animation_id)
            return object()

    class Behavior:
        def __init__(self, parent) -> None:
            self.parent = parent

        def play(self, behavior_id, *, repeat=1):
            self.parent.behavior_ids.append((behavior_id, repeat))
            return object()

    class Motion:
        def __init__(self, parent) -> None:
            self.parent = parent

        def move_to(self, **target):
            self.parent.motion_targets.append(target)
            job = FakeJob()
            self.parent.motion_jobs.append(job)
            return job

    def supports(self, capability):
        return {
            "animation": self.animation_supported,
            "behavior": self.behavior_supported,
            "motion": self.motion_supported,
        }.get(capability, True)

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_camera_and_audio_share_one_robot_connection_and_capture_is_atomic(
    tmp_path,
) -> None:
    robot = FakeRobot()
    factory_calls = 0

    def factory(**kwargs):
        nonlocal factory_calls
        factory_calls += 1
        return robot

    adapter = WatcheRobotAdapter(pairing_code="123456", robot_factory=factory)
    await adapter.connect()
    assert adapter.connected is True
    robot._closed = True
    assert adapter.connected is False
    robot._closed = False
    await adapter.warmup_camera()
    destination = tmp_path / "frame.jpg"
    frame = await adapter.capture(destination, "f-1", 1)

    async def chunks():
        yield b"\x00\x00" * 10
        yield b"\x01\x00" * 10

    await adapter.play_pcm(chunks())
    assert factory_calls == 1
    assert robot.capture_calls == 2
    assert destination.read_bytes() == b"jpeg-data"
    assert not destination.with_suffix(".jpg.tmp").exists()
    assert frame.path == destination
    assert robot.audio_chunks == [b"\x00\x00" * 10 + b"\x01\x00" * 10]
    assert adapter.last_playback_started_at is not None


@pytest.mark.asyncio
async def test_continuous_playback_timeout_includes_audio_duration() -> None:
    robot = FakeRobot()
    adapter = WatcheRobotAdapter(
        pairing_code="123456", robot_factory=lambda **kwargs: robot
    )
    await adapter.connect()

    async def chunks():
        yield b"\x00\x00" * (24_000 * 12)

    await adapter.play_pcm(chunks())

    assert len(robot.audio_chunks) == 1
    assert robot.playbacks[0].wait_timeout == 17.0
    await adapter.close()


@pytest.mark.asyncio
async def test_connect_replaces_a_disconnected_sdk_object() -> None:
    first = FakeRobot()
    second = FakeRobot()
    robots = iter([first, second])
    factory_calls = 0

    def factory(**kwargs):
        nonlocal factory_calls
        factory_calls += 1
        return next(robots)

    adapter = WatcheRobotAdapter(pairing_code="123456", robot_factory=factory)
    await adapter.connect()
    first._closed = True

    await adapter.connect()

    assert factory_calls == 2
    assert first.closed is True
    assert adapter.connected is True
    await adapter.close()


@pytest.mark.asyncio
async def test_playback_timeout_stops_stale_robot_audio() -> None:
    robot = FakeRobot()
    robot.fail_playback = True
    adapter = WatcheRobotAdapter(
        pairing_code="123456", robot_factory=lambda **kwargs: robot
    )
    await adapter.connect()

    async def chunks():
        yield b"\x00\x00" * 10

    with pytest.raises(TimeoutError, match="playback timeout"):
        await adapter.play_pcm(chunks())

    assert adapter.last_playback_started_at is not None
    assert robot.audio_stop_calls == 1
    await adapter.close()


@pytest.mark.asyncio
async def test_external_playback_pauses_and_resumes_active_microphone() -> None:
    robot = FakeRobot()
    adapter = WatcheRobotAdapter(
        pairing_code="123456", robot_factory=lambda **kwargs: robot
    )
    await adapter.connect()
    microphone = adapter.microphone_chunks()
    reader = asyncio.create_task(anext(microphone))
    for _ in range(100):
        if robot.microphone_open_calls == 1:
            break
        await asyncio.sleep(0.001)
    assert robot.microphone_open_calls == 1

    async def chunks():
        yield b"\x00\x00" * 10

    await adapter.play_pcm(chunks())
    for _ in range(100):
        if robot.microphone_open_calls == 2:
            break
        await asyncio.sleep(0.001)

    assert robot.microphone_open_calls == 2
    assert robot.microphone_close_calls == 1
    reader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reader
    assert robot.microphone_close_calls == 2
    await adapter.close()


@pytest.mark.asyncio
async def test_animation_play_is_capability_checked_and_deduplicated() -> None:
    robot = FakeRobot()
    adapter = WatcheRobotAdapter(
        pairing_code="123456", robot_factory=lambda **kwargs: robot
    )
    await adapter.connect()

    assert await adapter.play_animation("listening") is True
    assert await adapter.play_animation("listening") is True
    assert await adapter.play_animation("speaking") is True
    assert robot.animation_ids == ["listening", "speaking"]

    robot.animation_supported = False
    assert await adapter.play_animation("happy") is False
    assert robot.animation_ids == ["listening", "speaking"]
    await adapter.close()


@pytest.mark.asyncio
async def test_focus_entry_nods_once_then_enters_firmware_loop() -> None:
    robot = FakeRobot()
    adapter = WatcheRobotAdapter(
        pairing_code="123456", robot_factory=lambda **kwargs: robot
    )
    await adapter.connect()

    assert await adapter.enter_focus_mode() is True

    assert robot.animation_ids == ["concentration"]
    assert robot.motion_targets == [
        {
            "pan_deg": 90,
            "tilt_deg": 105,
            "duration_ms": 500,
        },
        {
            "pan_deg": 90,
            "tilt_deg": 125,
            "duration_ms": 300,
        },
        {
            "pan_deg": 90,
            "tilt_deg": 120,
            "duration_ms": 400,
        },
    ]
    assert all(job.wait_timeout == 5.0 for job in robot.motion_jobs)
    assert robot.behavior_ids == [("concentration", 1)]
    await adapter.close()


@pytest.mark.asyncio
async def test_looping_behavior_is_deduplicated_and_preempts_animation_cache() -> None:
    robot = FakeRobot()
    adapter = WatcheRobotAdapter(
        pairing_code="123456", robot_factory=lambda **kwargs: robot
    )
    await adapter.connect()

    assert await adapter.play_behavior("concentration") is True
    assert await adapter.play_behavior("concentration") is True
    assert await adapter.play_animation("speaking") is True
    assert await adapter.play_behavior("concentration") is True

    assert robot.behavior_ids == [("concentration", 1), ("concentration", 1)]
    assert robot.animation_ids == ["speaking"]
    await adapter.close()


@pytest.mark.asyncio
async def test_transient_face_waits_for_atomic_focus_entry() -> None:
    robot = FakeRobot()
    motion_started = threading.Event()
    release_motion = threading.Event()

    class BlockingMotion(robot.Motion):
        def move_to(self, **target):
            job = super().move_to(**target)
            original_wait = job.wait

            def wait(timeout=None):
                motion_started.set()
                assert release_motion.wait(2.0)
                return original_wait(timeout)

            job.wait = wait
            return job

    robot.motion = BlockingMotion(robot)
    adapter = WatcheRobotAdapter(
        pairing_code="123456", robot_factory=lambda **kwargs: robot
    )
    await adapter.connect()

    entry = asyncio.create_task(adapter.enter_focus_mode())
    assert await asyncio.to_thread(motion_started.wait, 1.0)
    transient = asyncio.create_task(adapter.play_behavior("thinking"))
    await asyncio.sleep(0.01)

    assert not transient.done()
    assert robot.behavior_ids == []

    release_motion.set()
    assert await entry is True
    assert await transient is True
    assert robot.behavior_ids == [("concentration", 1), ("thinking", 1)]
    await adapter.close()
