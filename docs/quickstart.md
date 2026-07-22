# 快速开始

本指南从空目录开始，先验证不依赖机器人和 GPU 的 HTTP 网关，再逐步接入本地模型服务与 WatcheRobot。建议按顺序执行；不要一开始同时排查网络、模型和机器人。

## 1. 前置条件

网关开发与测试需要：

- Linux（项目实测环境为 DGX Spark）；
- Python 3.12；
- Git；
- 可访问 PyPI 与 TestPyPI 的网络。

完整 Demo 还需要：

- NVIDIA GPU 和可运行 Step3-VL-10B-FP8 的 vLLM 环境；
- 已启动的 Qwen ASR、Ollama 与 Qwen3-TTS HTTP 服务；
- 安装并打开 `sdk.control.app` 的 WatcheRobot；
- Spark 与机器人处于同一局域网。

`WatcheRobot_server` 不是运行依赖。模型权重、第三方模型服务和机器人固件不包含在本仓库中。

## 2. 获取源码与创建环境

```bash
git clone <repository-url> SparkHT-Focus
cd SparkHT-Focus

python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install --extra-index-url https://test.pypi.org/simple \
  -e '.[test,model]'
cp .env.example .env
```

`watcherobot==0.1.0a4` 当前发布在 TestPyPI，因此安装命令必须保留 `--extra-index-url`。不要在 `.env` 中写入准备提交的配对码。

## 3. 先验证网关

关闭机器人连接后启动网关：

```bash
FOCUS_ENABLE_ROBOT=false .venv/bin/focus-demo
```

另开终端检查：

```bash
curl -fsS http://127.0.0.1:8780/health
curl -I http://127.0.0.1:8780/
```

此时仪表盘与 API 应可访问。未启动的 ASR、LLM、TTS 或 VLM 会在健康状态中显示不可用，但不妨碍检查 Web 层。

创建一个 90 秒 Demo 会话：

```bash
curl -fsS -X POST http://127.0.0.1:8780/api/focus/sessions \
  -H 'Content-Type: application/json' \
  -d '{"mode":"demo","duration_seconds":90}'
```

运行自动测试：

```bash
.venv/bin/ruff format --check src tests scripts
.venv/bin/ruff check src tests scripts
.venv/bin/pytest
```

## 4. 准备快系统服务

网关默认连接以下本机服务：

| 服务 | 地址 | 最小验证 |
|---|---|---|
| Qwen ASR | `http://127.0.0.1:8010` | `curl -fsS http://127.0.0.1:8010/health` |
| Ollama | `http://127.0.0.1:11434` | `curl -fsS http://127.0.0.1:11434/api/tags` |
| Qwen3-TTS | `http://127.0.0.1:8030` | `curl -fsS http://127.0.0.1:8030/health` |

确认 Ollama 已有对话模型：

```bash
ollama pull qwen3:0.6b
ollama list
```

ASR 与 TTS 服务由部署环境提供，本仓库只实现 HTTP 客户端。若服务使用不同端口或模型名，请修改 `.env`，不要修改源码默认值。

## 5. 准备 Step3-VL

为 vLLM 使用独立虚拟环境：

```bash
python3.12 -m venv .vllm-venv
.vllm-venv/bin/pip install --upgrade pip
.vllm-venv/bin/pip install 'vllm==0.22.0' 'transformers==4.57.6'
```

在 Spark 上下载：

```bash
HF_XET_HIGH_PERFORMANCE=1 .venv/bin/hf download \
  stepfun-ai/Step3-VL-10B-FP8 \
  --local-dir .models/Step3-VL-10B-FP8 \
  --max-workers 4
```

网络受限时，可以在 Windows 11 主机下载后通过局域网传输：

```powershell
py -3.12 -m venv .hf-venv
.\.hf-venv\Scripts\python.exe -m pip install -U huggingface-hub
.\.hf-venv\Scripts\hf.exe download stepfun-ai/Step3-VL-10B-FP8 `
  --local-dir .\Step3-VL-10B-FP8 --max-workers 4
scp -r .\Step3-VL-10B-FP8 <user>@<spark-ip>:/path/to/SparkHT-Focus/.models/
```

传输后必须校验并启动：

```bash
scripts/verify_step3_model.sh
scripts/start_step3_vllm.sh
curl -fsS http://127.0.0.1:8040/v1/models
```

校验脚本会检查所需文件和五个权重分片的 SHA-256。校验失败时不要启动 vLLM，应重新传输对应文件。

## 6. 连接机器人

1. 在机器人上打开 `sdk.control.app`。
2. 确认机器人与 Spark 位于同一局域网。
3. 打开 `http://<spark-ip>:8780/`，在“技术状态 → 机器人 SDK 配对”中输入当次六位码。

Web 配对成功后，SDK、麦克风语音循环、默认表情和灯光会热挂载，无需重启 FastAPI。配对码提交后立即从输入框清除，只保留在当前进程内存中供断线重连使用。

无浏览器环境可以在首次启动时通过无回显输入注入：

```bash
read -r -s -p 'Pairing code: ' watcher_pairing_code
printf '\n'
WATCHER_PAIRING_CODE="$watcher_pairing_code" .venv/bin/focus-demo
unset watcher_pairing_code
```

SDK 支持双向发现，默认 `WATCHER_SDK_HOST=auto`。若电脑同时有 Wi-Fi 和有线网卡且二者位于相同子网，路由选择可能不稳定；优先禁用无关网卡，或将 `WATCHER_SDK_HOST` 设为机器人可达地址。网络与防火墙规则见[配置参考](configuration.md)。

## 7. 完整验收

打开 `http://<spark-ip>:8780/`，依次验证：

1. 健康卡片显示机器人、ASR、Ollama、TTS 与 Step3-VL 状态；
2. 说“开始专注”，机器人执行开始动作并循环播放专注表情；
3. 仪表盘按真实 4:3 比例展示抓拍图片；
4. 说“现在统计到哪了”，上下行语句和核心指标得到更新；
5. 说“停止专注”，生成最终报告并返回待机状态。

## 常见问题

### 仪表盘可用，但健康检查显示模型离线

逐个执行第 4、5 节中的最小验证。模型服务监听在 `127.0.0.1` 时只能由同一台 Spark 访问，这是推荐的默认安全配置。

### 机器人无法发现网关

确认 UDP `37021`、TCP `8766` 与仪表盘 TCP `8780` 未被防火墙拦截，并检查同一子网的双网卡路由。不要把临时配对码贴进 Issue。

### 语音容易被环境噪声触发

非专注与专注阈值分别由 `FOCUS_VAD_IDLE_THRESHOLD` 和 `FOCUS_VAD_THRESHOLD` 控制。先查看现场 PCM 振幅再小幅调整；过高会漏掉正常说话。

### Step3-VL 启动时提示文件或哈希错误

重新运行 `scripts/verify_step3_model.sh`，只重传失败的权重分片。不要跳过校验。
