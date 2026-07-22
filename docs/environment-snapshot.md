# DGX Spark 环境快照

记录时间：2026-07-22 13:45–14:06（Asia/Shanghai）。

## 硬件与系统

- 主机：NVIDIA DGX Spark，GB10，aarch64。
- 内存：119 GiB；清理前已用约 100 GiB、可用约 19 GiB。
- CUDA：13.0；驱动：580.142。
- 工作盘：917 GiB，总剩余约 364 GiB。

## 初始服务

| 端口 | 进程/模型 | 初始状态 |
|---|---|---|
| 8775 | Paraformer/Qwen ASR + Qwen3-4B + Qwen TTS | listening |
| 8776 | Parakeet TDT + Ollama + Qwen3-TTS 1.7B | listening |
| 8000 | vLLM Qwen3-4B | listening |
| 8010 | Qwen3-ASR sidecar | listening |
| 8030 | Qwen3-TTS 0.6B | listening |
| 11434 | Ollama `qwen3:0.6b` | listening |
| 8040 | Step3-VL | 未启动 |
| 8780 | SparkHT Focus | 未启动 |

## 资源切换

确认独立网关、8010、8030 和 11434 可调用后，通过所属 systemd user service 正常停止：

- `hf-speech-to-speech-parakeet-canary.service`（8776）
- `hf-speech-to-speech-qwen.service`（8775）
- `vilab-recovery-vllm-8000.service`（8000）

另确认一个约占 17.6 GiB 的 `VLLM::EngineCore` 属于状态为 closing 的旧 SSH session，父 API 进程已不存在且无对外端口；对该孤儿 PID 发送 SIGTERM。未停止属于 8010 ASR 和 8030 TTS 的 EngineCore。

清理后内存约为：已用 55 GiB，可用 64 GiB。上述 systemd 服务均可通过原 unit 恢复。

## 快链路探测

- 8010 `/health` 返回 healthy 不能替代真实请求。首次请求错误携带模型别名会覆盖 sidecar 本地路径并尝试访问远端；适配器修复为不覆盖 sidecar 模型后，真实 WAV 请求返回 200。
- 8030 `/v1/audio/speech` 支持 `response_format=pcm`、`stream=true`。
- 11434 已安装 `qwen3:0.6b`。

## Step3 隔离环境

- 路径：`.vllm-venv`，不引用 VILab 或 `WatcheRobot_server` 的环境。
- 版本：vLLM 0.22.0、Torch 2.11.0+cu130、Transformers 4.57.6；`torch.cuda.is_available()` 为 true。
- 本机 `pip check` 会将 `nvidia-cusparselt-cu13==0.8.0` 标记为 platform unsupported；机器上正常提供 8030 的 TTS 隔离环境有同一条元数据警告。最终以 vLLM 导入、CUDA 探测和 8040 实际启动为判据。

## Step3 实际启动（16:23–16:34）

- 五个 safetensors 分片全部通过官方 SHA-256，模型总权重约 14.03 GiB。
- 8040 由 user-systemd 临时单元 `sparkht-step3-vllm.service` 承载，返回模型名 `step3-vl-focus`。
- 首次启动定位到 FlashInfer JIT 找不到隔离环境中的 `ninja`；启动脚本补充 `.vllm-venv/bin` 到 `PATH` 后修复。
- 模型自带模板会无条件进入长思维链。服务改用专用无思维链模板，并通过 JSON Schema 约束结构化输出；不展示或持久化完整思维链。
- 实际加载识别 `StepVLForConditionalGeneration`、FP8 和 Cutlass FP8 kernel；权重加载约 82 秒，EngineCore 统一内存占用约 34.1 GiB。
- 8040、8010、8030、11434 同时运行时系统约有 42 GiB 可用内存。
