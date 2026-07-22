# Seeing Focus

> An edge-native fast/slow dual-system robot demo built with StepFun multimodal models, DGX Spark, and WatcheRobot.

English | [简体中文](README.md)

SparkHT Focus is an independent Python orchestration service for a hackathon demo. Its fast path handles low-latency Chinese voice interaction. Its slow path analyzes periodic 640×480 robot-camera frames with Step3-VL and turns structured observations into deterministic focus statistics.

The project runs independently of `WatcheRobot_server`. A single connection to the robot's `sdk.control.app` carries microphone audio, camera frames, speaker audio, facial animations, and servo actions.

## Highlights

- Start, query, stop, or cancel a focus session by voice.
- Run short conversations through Ollama `qwen3:0.6b` with trusted live statistics only.
- Capture one frame every 10 seconds and analyze four-frame batches with Step3-VL-10B-FP8.
- Preempt or cancel slow vision work when speech is detected.
- Show native 4:3 images, upstream/downstream dialogue, core metrics, health, and events in a compact vertical dashboard.
- Play a focus animation and a head-up/nod/neutral gesture when a session starts, then keep the focus expression looping.
- Store sessions, events, frames, and reports locally on the Spark.

## Architecture

```text
WatcheRobot sdk.control.app
          |
          v
SparkHT Focus (FastAPI :8780)
  |-- fast path: ASR -> deterministic intent -> Qwen 0.6B -> TTS
  |-- slow path: camera -> Step3-VL -> deterministic statistics
  `-- dashboard: HTTP API + SSE
```

Default local services:

| Service | Address | Purpose |
|---|---|---|
| SparkHT Focus | `0.0.0.0:8780` | Robot gateway, state machine, API, dashboard |
| Qwen ASR | `127.0.0.1:8010` | Chinese speech recognition |
| Qwen3-TTS | `127.0.0.1:8030` | Chinese speech synthesis |
| Ollama | `127.0.0.1:11434` | Short `qwen3:0.6b` replies |
| Step3 vLLM | `127.0.0.1:8040` | Structured four-image observations |

## Quick start

Python 3.12 is required. Keep the gateway and vLLM in separate virtual environments.

```bash
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install --extra-index-url https://test.pypi.org/simple \
  -e '.[test,model]'
cp .env.example .env

FOCUS_ENABLE_ROBOT=false .venv/bin/focus-demo
```

Open `http://127.0.0.1:8780/` and check `http://127.0.0.1:8780/health`. Model services may appear unavailable in this gateway-only mode.

For model setup, Windows-to-Spark weight transfer, firewall rules, dual-NIC troubleshooting, and robot pairing, follow the Chinese [complete quick-start guide](docs/quickstart.md) and [configuration reference](docs/configuration.md).

## Development

Tests use fakes, spies, and HTTP mocks; they do not require a GPU, model server, or physical robot.

```bash
.venv/bin/ruff format --check src tests scripts
.venv/bin/ruff check src tests scripts
.venv/bin/pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), [SECURITY.md](SECURITY.md), and [CHANGELOG.md](CHANGELOG.md) before contributing.

## Privacy and scope

- The system does not perform OCR, identity recognition, emotion recognition, or infer screen/work content.
- Cup changes are reported only as possible movement or possible drinking events.
- Focus trends are low-resolution visual proxy metrics, not medical or productivity assessments.
- Runtime frames, audio, logs, pairing codes, model weights, and `.env` files are excluded from Git.

## License

The source code is licensed under the [Apache License 2.0](LICENSE). Models, the robot SDK, and other third-party components remain subject to their respective licenses.
