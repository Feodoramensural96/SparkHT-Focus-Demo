# 贡献与格式规范

本项目以黑客松可复现性和真实指标为优先目标。修改应保持 SparkHT 独立运行，不引入 `WatcheRobot_server` 运行依赖。

## 开发环境

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[test,model]'
```

## 提交前检查

```bash
.venv/bin/ruff format src tests scripts
.venv/bin/ruff check src tests scripts
.venv/bin/pytest
git diff --check
```

Python 使用 Ruff 的 88 字符行宽和 Python 3.12 目标版本。Shell 脚本使用 `set -euo pipefail`，变量使用任务相关名称，不覆盖 `HOME` 等系统变量。

## 代码边界

- `service.py` 和领域模块只依赖 `ports.py` 中的协议。
- 机器人、模型、HTTP 和文件系统细节放在 `infrastructure/`。
- 指标必须由确定性 Python 代码计算，LLM 只能改写已经提供的数据。
- 外部调用必须有超时、队列或重试上限。
- 机器人麦克风、相机、音频、表情和动作共用一个 SDK 对象。
- 新行为先写测试；默认测试不能要求 GPU、模型或真机。

## 文档与实测

- 理论吞吐、单样本和 P95 必须明确区分。
- 失败、超时和慢样本不能从报告中删除。
- 修改默认值、模型、端口、测试数量或协议行为时，同步更新 README、`.env.example` 和相关报告。
- 不展示或持久化模型完整思维链。

## 隐私与密钥

禁止提交以下内容：

- 机器人临时配对码或带真实值的 `.env`。
- `runtime/` 下的图片、音频、日志和会话数据。
- `.models/`、虚拟环境、模型缓存或生成视频。
- 可识别个人身份的截图和完整真实对话。

提交前可检查：

```bash
git status --short
git ls-files
```

## Git 约定

提交标题使用简短中文 Conventional Commit：

```text
feat: 增加功能
fix: 修复问题
docs: 整理文档
test: 补充测试
build: 调整构建或依赖
```

一次提交只表达一个意图。不要把模型权重、运行数据或无关工作区改动混入功能提交。
