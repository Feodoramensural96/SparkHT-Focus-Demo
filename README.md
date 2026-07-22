# 看见专注：SparkHT 端侧快慢双系统 Demo

这是独立运行于 DGX Spark 的 Python 编排服务，不依赖 `WatcheRobot_server`。机器人只打开一个 `sdk.control.app` 连接；同一连接承载麦克风、640×480 抓拍和 24 kHz PCM 播放。

## 架构

```text
WatcheRobot sdk.control.app
           |
           v
SparkHT FastAPI :8780
  |-- 快系统：Qwen ASR :8010 -> Ollama qwen3:0.6b :11434 -> Qwen3-TTS :8030
  `-- 慢系统：定时抓拍 -> Step3-VL-10B-FP8 :8040 -> 确定性统计 -> SSE 仪表盘
```

语音开始后，调度器立即取消当前视觉客户端请求并停止提交新批次；语音链路空闲 5 秒后，最多重试被暂停批次一次。待处理队列只保留最新一批。

## 启动

```bash
python -m venv .venv
.venv/bin/pip install -e '.[test,model]'
cp .env.example .env
# 在 .env 临时填写机器人屏幕显示的 WATCHER_PAIRING_CODE
.venv/bin/focus-demo
```

无需机器人验证 HTTP 层时：

```bash
FOCUS_ENABLE_ROBOT=false .venv/bin/focus-demo
```

打开 `http://<Spark-IP>:8780/`。活动会话仪表盘可使用 `/?session=<session_id>`。

### Step3-VL 服务

Step3 使用单独环境，避免改变网关和已有语音服务的依赖：

```bash
python -m venv .vllm-venv
.vllm-venv/bin/pip install 'vllm==0.22.0' 'transformers==4.57.6'
HF_XET_HIGH_PERFORMANCE=1 .venv/bin/hf download \
  stepfun-ai/Step3-VL-10B-FP8 \
  --local-dir .models/Step3-VL-10B-FP8 \
  --max-workers 4
scripts/start_step3_vllm.sh
```

服务就绪后，`http://127.0.0.1:8040/v1/models` 应返回 `step3-vl-focus`。可用一至四张本地图重复执行真实协议与时延检查：

```bash
.venv/bin/python scripts/benchmark_step3_vlm.py --runs 3 frame-1.jpg frame-2.jpg
```

## API

- `POST /api/focus/sessions`
- `POST /api/focus/sessions/{id}/stop`
- `POST /api/focus/sessions/{id}/cancel`
- `GET /api/focus/sessions/{id}`
- `GET /api/focus/sessions/{id}/report`
- `GET /api/focus/sessions/{id}/events`
- `GET /health`

## 测试

```bash
.venv/bin/pytest
```

测试默认使用 fake/spy 和 HTTP mock，不需要 GPU、模型或机器人。

真实本地快链路基准和同连接 SDK 三能力冒烟：

```bash
.venv/bin/python scripts/benchmark_fast_chain.py
WATCHER_PAIRING_CODE=屏幕六位码 .venv/bin/python scripts/smoke_watcher_sdk.py
```

## 边界

- 不做 OCR、身份识别、情绪识别或工作内容判断。
- 杯子变化只称为“疑似杯子移动/疑似饮水事件”，不声称确认喝水。
- 专注趋势指数是低分辨率视觉代理指标，仅供参考。
- `runtime/`、模型权重、图片、音频和配对码均不进入 Git。
