# 配置参考

SparkHT Focus 使用 `pydantic-settings` 读取当前目录下的 `.env` 和进程环境变量。环境变量不区分大小写，进程环境变量优先于 `.env`。建议从 `.env.example` 复制，并只覆盖部署环境确实不同的值。

## 网关与持久化

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `FOCUS_HOST` | `0.0.0.0` | FastAPI 监听地址；仅本机使用时可改为 `127.0.0.1` |
| `FOCUS_PORT` | `8780` | 仪表盘和 API 端口 |
| `FOCUS_DATA_DIR` | `./runtime/focus` | 会话、事件、图片和报告的本地目录 |
| `FOCUS_ENABLE_ROBOT` | `true` | 是否启用机器人 SDK；开发 HTTP 层时设为 `false` |

## 机器人 SDK

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `WATCHER_PAIRING_CODE` | 空 | 可选的启动时临时六位码；通常改用 Web 内存配对，不得提交或记录 |
| `WATCHER_SDK_HOST` | `auto` | 自动双向发现；复杂路由下可设机器人可达地址 |
| `WATCHER_SDK_DISCOVERY_PORT` | `37021` | SDK UDP 发现端口 |
| `WATCHER_SDK_WEBSOCKET_PORT` | `8766` | SDK WebSocket 端口 |

启用机器人但配对码为空时，网关仍可启动。推荐启动后在 Web 技术状态卡片输入六位码：后端在内存中建立 SDK 并热挂载语音控制器，不写 `.env`、日志或会话文件。无浏览器部署才需要在进程启动时通过环境变量临时注入。

## 会话与调度

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `FOCUS_DEMO_DURATION_SECONDS` | `90` | Demo 模式默认时长 |
| `FOCUS_NORMAL_DURATION_SECONDS` | `1500` | 普通模式默认时长 |
| `FOCUS_DEMO_CAPTURE_INTERVAL_SECONDS` | `10` | Demo 模式抓拍间隔 |
| `FOCUS_NORMAL_CAPTURE_INTERVAL_SECONDS` | `30` | 普通模式抓拍间隔 |
| `FOCUS_BATCH_SIZE` | `4` | 每次视觉分析的图片数 |
| `FOCUS_VOICE_IDLE_SECONDS` | `5` | 语音结束后恢复慢任务的空闲时间 |
| `FOCUS_VAD_IDLE_THRESHOLD` | `1000` | 非专注状态的能量 VAD 阈值 |
| `FOCUS_VAD_THRESHOLD` | `2500` | 专注状态的能量 VAD 阈值 |
| `FOCUS_MAX_FRAMES_PER_SESSION` | `100` | 单会话最多保留的抓拍帧数 |

缩短抓拍间隔会增加相机、存储和 VLM 压力。`FOCUS_BATCH_SIZE` 应与 vLLM 的 `--limit-mm-per-prompt` 图片上限一致。

## Step3-VL

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `STEPFUN_VLM_BASE_URL` | `http://127.0.0.1:8040/v1` | OpenAI 兼容的 vLLM API 根地址 |
| `STEPFUN_VLM_MODEL` | `step3-vl-focus` | vLLM 对外模型名 |
| `STEPFUN_VLM_TIMEOUT_SECONDS` | `30` | 单批视觉请求超时 |
| `STEPFUN_VLM_MAX_TOKENS` | `192` | 结构化观察最大输出 token 数 |

`scripts/start_step3_vllm.sh` 将服务限制在本机并使用单并发，以降低 Demo 环境的显存竞争。修改脚本参数后应重新执行性能基准。

## 快系统

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `FAST_ASR_BACKEND` | `qwen_asr` | ASR 后端标识；当前实现支持 Qwen ASR 客户端 |
| `QWEN_ASR_BASE_URL` | `http://127.0.0.1:8010` | ASR HTTP 地址 |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama 地址 |
| `OLLAMA_MODEL` | `qwen3:0.6b` | 普通对话模型 |
| `QWEN_TTS_BASE_URL` | `http://127.0.0.1:8030` | TTS HTTP 地址 |
| `QWEN_TTS_MODEL` | `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` | TTS 模型标识 |
| `QWEN_TTS_VOICE` | `Aiden` | TTS 音色 |

## 端口与防火墙

| 协议/端口 | 方向 | 用途 | 建议范围 |
|---|---|---|---|
| TCP `8780` | 局域网 → Spark | 仪表盘和 API | 仅可信局域网 |
| UDP `37021` | Spark ↔ 机器人 | SDK 双向发现 | 仅机器人所在子网 |
| TCP `8766` | Spark ↔ 机器人 | SDK WebSocket | 仅机器人所在子网 |
| TCP `8010/8030/8040/11434` | 本机 | 模型服务 | 保持 `127.0.0.1`，无需对局域网开放 |

Windows 或 Linux 主机同时连接 Wi-Fi 和有线网络，且两者都位于 `192.168.1.0/24` 等相同子网时，操作系统可能从错误接口发送发现包。处理顺序：

1. 暂时禁用不参与机器人通信的接口；
2. 确认到机器人 IP 的路由和源地址；
3. 使用 `WATCHER_SDK_HOST` 显式指定可达地址；
4. 为实际运行 Python 的解释器添加入站防火墙规则。Anaconda、系统 Python 与虚拟环境解释器可能需要不同规则。

不要直接关闭整台机器的防火墙。仅为上述端口、解释器和可信局域网创建最小范围规则。

## 配置示例

仅验证 Web/API：

```dotenv
FOCUS_ENABLE_ROBOT=false
FOCUS_HOST=127.0.0.1
```

局域网完整 Demo：

```dotenv
FOCUS_ENABLE_ROBOT=true
FOCUS_HOST=0.0.0.0
WATCHER_SDK_HOST=auto
FOCUS_VAD_IDLE_THRESHOLD=1000
FOCUS_VAD_THRESHOLD=2500
```

配对码故意不出现在示例中。通过无回显终端输入并只注入当前进程。
