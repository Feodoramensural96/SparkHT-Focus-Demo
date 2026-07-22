import asyncio

import pytest

import focus.runtime as runtime_module
from focus.runtime import FocusRuntime
from focus.settings import FocusSettings


class PairingRobot:
    instances: list["PairingRobot"] = []

    def __init__(self, *, pairing_code: str, **kwargs) -> None:
        self.pairing_code = pairing_code
        self.connected = False
        self.closed = False
        self.behavior_ids: list[str] = []
        self.light_colors: list[str] = []
        self.__class__.instances.append(self)

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False
        self.closed = True

    async def play_behavior(self, behavior_id: str) -> bool:
        self.behavior_ids.append(behavior_id)
        return True

    async def set_light(self, color: str, *, brightness: float = 1.0) -> bool:
        self.light_colors.append(color)
        return True


class PairingVoice:
    def __init__(self, **kwargs) -> None:
        self.robot = kwargs["robot"]
        self.stopped = False
        self._wait = asyncio.Event()

    async def run(self) -> None:
        await self._wait.wait()

    async def stop(self) -> None:
        self.stopped = True
        self._wait.set()


@pytest.mark.asyncio
async def test_runtime_hot_pairs_robot_and_starts_voice_supervisor(
    tmp_path, monkeypatch
) -> None:
    PairingRobot.instances.clear()
    monkeypatch.setattr(runtime_module, "WatcheRobotAdapter", PairingRobot)
    monkeypatch.setattr(runtime_module, "VoiceController", PairingVoice)
    runtime = FocusRuntime(
        FocusSettings(focus_data_dir=tmp_path, focus_enable_robot=True)
    )

    result = await runtime.pair_robot("123456")
    await asyncio.sleep(0)

    robot = PairingRobot.instances[0]
    assert result["status"] == "connected"
    assert robot.pairing_code == "123456"
    assert runtime.robot is robot
    assert runtime.service.robot is robot
    assert runtime.voice is not None
    assert runtime._voice_task is not None
    assert robot.behavior_ids == ["standby2"]
    assert robot.light_colors == ["#FFFFFF"]

    repeated = await runtime.pair_robot("654321")
    assert repeated["status"] == "already_connected"
    assert len(PairingRobot.instances) == 1

    await runtime.close()
    assert robot.closed is True


@pytest.mark.asyncio
async def test_runtime_pair_failure_leaves_gateway_without_robot(
    tmp_path, monkeypatch
) -> None:
    class BrokenRobot(PairingRobot):
        async def connect(self) -> None:
            raise TimeoutError("pairing timed out")

    PairingRobot.instances.clear()
    monkeypatch.setattr(runtime_module, "WatcheRobotAdapter", BrokenRobot)
    runtime = FocusRuntime(
        FocusSettings(focus_data_dir=tmp_path, focus_enable_robot=True)
    )

    with pytest.raises(TimeoutError, match="pairing timed out"):
        await runtime.pair_robot("123456")

    assert runtime.robot is None
    assert runtime.voice is None
    assert runtime.service.robot is None
    assert PairingRobot.instances[0].closed is True
    await runtime.close()
