from datetime import UTC, datetime

import pytest

from focus.infrastructure.watcher_sdk import WatcheRobotAdapter


class FakePlayback:
    def __init__(self, *, fail: bool = False) -> None:
        self.waited = False
        self.fail = fail

    def wait(self, timeout=None):
        self.waited = True
        if self.fail:
            raise TimeoutError("playback timeout")
        return self


class FakeMicrophone:
    closed = False

    def read(self, timeout=None):
        raise TimeoutError

    def close(self):
        self.closed = True


class FakeRobot:
    def __init__(self) -> None:
        self.capture_calls = 0
        self.audio_chunks: list[bytes] = []
        self.audio_stop_calls = 0
        self.fail_playback = False
        self.closed = False
        self._closed = False
        self.camera = self.Camera(self)
        self.audio = self.Audio(self)
        self.microphone = self.Microphone()

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
            return FakePlayback(fail=self.parent.fail_playback)

        def stop(self):
            self.parent.audio_stop_calls += 1
            return None

    class Microphone:
        def open(self):
            return FakeMicrophone()

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
    assert robot.audio_chunks == [b"\x00\x00" * 10, b"\x01\x00" * 10]
    assert adapter.last_playback_started_at is not None


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
