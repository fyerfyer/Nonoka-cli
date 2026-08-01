# nonoka-cli

[English](README.md) | 简体中文

[Nonoka](https://pypi.org/project/nonoka/) Agent 框架的 OpenCode 后端。

Nonoka 以 stdio NDJSON 桥接服务器（`python -m nonoka_cli --server`）的方式运行，
与 `nonoka-opencode-provider` TypeScript 包通信。在 OpenCode 内部使用时，
Nonoka 负责对话与决策，而 OpenCode 使用其原生工具负责工具执行和
人在回路（human-in-the-loop，HITL）审批。

## 快速安装

获取 nonoka + OpenCode 最简单的方式是一行安装脚本：

```bash
curl -fsSL https://raw.githubusercontent.com/fyerfyer/Nonoka-cli/main/install.sh | bash
```

安装脚本会：

1. 检查 Python 3.10+ 和 Node/npm。
2. 安装或更新 OpenCode。
3. 安装 `nonoka-cli` 和 OpenCode provider。
4. 询问安装目录、配置目录和 npm 包目录。
5. 生成 Nonoka 配置和项目级 OpenCode 配置。

每个提示都会说明目录用途并给出默认值：

```text
Installation directory (Python environment, launchers, and npm tools; e.g. ~/nonoka)
  [~/.local/share/nonoka]
Configuration directory (config.yaml and .env; e.g. ~/.config/nonoka)
  [~/.config/nonoka]
npm prefix (OpenCode/provider global packages)
  [~/.local/share/nonoka/npm]
```

直接按 Enter 接受默认值，也可以填写 `~/tools/nonoka` 之类的路径。安装器生成的
`INSTALL_DIR/bin/nonoka` launcher 会记住这三个目录，因此通过绝对路径运行时
不需要手工 `export`。

安装完成后，配置你的 API key 并运行 `nonoka`：

```bash
# Interactive: it will ask for your key and save it to ~/.config/nonoka/.env
nonoka config init

# Or set it manually
export DEEPSEEK_API_KEY=<your-key>

nonoka doctor
nonoka
```

`nonoka` 是主入口：不带子命令时启动 OpenCode TUI；也可以使用
`nonoka run --message "<task>"` 进行一次性 CLI 调用。旧的 `nonoka-cli`
可执行文件继续保留以兼容已有脚本。

`nonoka-cli` 启动时会自动加载 `~/.config/nonoka/.env` 和 `./.env`，
因此如果你把 key 保存在 `.env` 中，就不需要每次都 `export`。

> 检测到 `uv` 时安装器会默认全程使用 `uv`，否则回退到隔离环境中的 pip。
> 只有想明确使用 pip 时才传 `--pip`；非交互安装使用 `--yes`。

非交互安装到指定目录时，可以直接使用参数（也支持同名环境变量）：

```bash
bash install.sh --yes --uv --npm-opencode \
  --install-dir ~/tools/nonoka \
  --config-dir ~/.config/nonoka \
  --npm-prefix ~/tools/nonoka/npm

~/tools/nonoka/bin/nonoka doctor
```

对应环境变量为 `NONOKA_INSTALL_DIR`、`NONOKA_CONFIG_DIR` 和
`NONOKA_NPM_PREFIX`；CLI 参数优先级高于环境变量。

## 手动安装

```bash
# Install nonoka-cli
pip install nonoka-cli
# or with uv
uv pip install nonoka-cli

# Install the OpenCode provider globally so OpenCode can load it
npm install -g nonoka-opencode-provider
```

## 快速开始

1. 创建你的 nonoka 配置（它会询问你的 API key 并保存到
   `~/.config/nonoka/.env`）：

```bash
nonoka config init
```

对于脚本化安装，使用非交互模式（你仍需通过 `.env` 或 `export` 设置
API key）：

```bash
nonoka config init --yes --model deepseek/deepseek-v4-pro
```

2. 在当前项目或全局生成 OpenCode 配置：

```bash
# Project-level
nonoka init

# User-level
nonoka init --global
```

3. 确保你的模型 API key 已导出，然后运行：

```bash
nonoka
```

## `nonoka-cli doctor`

诊断你的安装与配置：

```bash
nonoka-cli doctor
```

示例输出：

```
nonoka-cli doctor
✓ nonoka-cli 0.2.14
✓ Python 3.11
✓ opencode 1.18.2
✓ provider nonoka-opencode-provider@0.2.17
✓ nonoka framework 1.3.8
✓ config ~/.config/nonoka/config.yaml
✓ API key DEEPSEEK_API_KEY set
✓ OpenCode provider config in /home/user/.config/opencode/opencode.json
```

如果有任何问题，`doctor` 会打印一行修复建议。如需通过一次真实的（小型）
调用同时验证 LLM API key，使用：

```bash
nonoka-cli doctor --check-llm
```

单独验证基于 Docker 的命令沙箱：

```bash
nonoka-cli doctor --check-sandbox
```

## 执行可观测性

每个本地 runner 会话都会向 `~/.local/share/nonoka/events.db` 写入已脱敏
（credential-redacted）的结构化事件。事件包括 LLM prompt/response、
工具 I/O、错误以及 LiteLLM 的 token/成本用量。无需直接打开数据库即可查看：

```bash
nonoka-cli sessions list
nonoka-cli sessions show <session-id>
nonoka-cli logs --session-id <session-id>
nonoka-cli logs --json
```

框架暴露了 provider 中立的 `TelemetryExporter` 协议和
`ObservabilityPipeline`；下游应用可以接入 Langfuse、OTLP 或其他导出器，
而无需让 `Runner` 耦合到某个厂商 SDK。导出失败是尽力而为（best-effort）的，
绝不会中断 agent 运行。

## 服务化部署

`nonoka-agent` 内置一个带认证的 FastAPI 应用，提供 `/run`、
`/chat`、`/tasks`、`/health` 和 `/metrics` 端点。启动前先设置 bearer token：

```bash
export NONOKA_API_TOKEN="replace-with-a-long-random-token"
uv run uvicorn nonoka.server.app:create_app --factory --host 0.0.0.0 --port 8000
```

容器化部署：复制 `.env.example` 为 `.env`，设置
`NONOKA_API_TOKEN`，然后运行 `docker compose up --build`。Compose 默认使用
PostgreSQL 持久化事件；本地开发保持使用 SQLite。该服务以非 root 用户运行、
文件系统只读、丢弃 Linux capabilities，并且不挂载宿主机的 Docker socket。

## 自定义 Tool、MCP 与 Skill

`nonoka-cli` 可以在 OpenCode 原生工具之外加载本地 Python Tool、MCP server
和按需加载的 Skill：

```yaml
tool_paths:
  - ~/.config/nonoka/tools

mcp_servers:
  filesystem:
    transport: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/docs"]

skills:
  - code-review
```

OpenCode 模式中，自定义 Tool、MCP Tool 和 Skill Tool 分别使用
`custom__<tool>`、`mcp__<server>__<tool>` 和
`skill__<skill>__<tool>` 命名空间；Skill 的完整说明通过 `load_skill`
按需载入。这样可避免与 OpenCode 原生工具重名。standalone 模式仍保留自定义
Tool 原本的名称。

## Agent 评估

`nonoka-cli eval` 是框架内置 benchmark 引擎的一个轻量前端。其计分内置数据集
是公开的 HumanEval 和 MBPP。附带的 `tool_use` 套件被明确标注为确定性的
冒烟/回归覆盖，并不作为开放 benchmark 的替代品。结果保存在当前项目的
`.nonoka/eval/` 下，可以在本地进行对比：

```bash
nonoka-cli eval list
nonoka-cli eval run --dataset humaneval --model deepseek/deepseek-v4-pro --limit 20
nonoka-cli eval run --dataset mbpp --model deepseek/deepseek-v4-pro --limit 20
nonoka-cli eval leaderboard
```

每次内置运行都会记录一个普通 Nonoka agent 和一个同模型的直接基线
（baseline），包括 pass@1、轮数、工具调用数、token 用量、wall time 以及
agent 提升幅度。真实的模型调用是可选开启（opt-in）的，从不属于默认测试
套件。

发布前对比：在产生任何模型调用成本之前，先创建一个 manifest。它会固定
模型策略，并包含 HumanEval、MBPP sanitized、EvalPlus、τ³ retail/airline
和 Terminal-Bench。EvalPlus 在独立环境中运行，因为它自带官方强化版
验证器（verifier）：

```bash
nonoka-cli eval matrix plan --model deepseek/deepseek-v4-pro --output .nonoka/eval/release-matrix.json
export NONOKA_EVALPLUS_PYTHON=/path/to/evalplus-python
nonoka-cli eval matrix run --manifest .nonoka/eval/release-matrix.json --include evalplus-humaneval
```

对于复杂 agent 任务，框架委托给官方评测框架（harness），而不是重新实现
它们的验证器。τ³-bench（即 `tau2-bench` 包）提供带策略、模拟用户、环境
工具和动作级奖励的多轮客服任务；由于其依赖版本钉死（dependency pins），
它需要在隔离的 Python 3.12 环境中运行：

```bash
export NONOKA_TAU2_PYTHON=/path/to/tau2-python
nonoka-cli eval external run --benchmark tau2-bench --model deepseek/deepseek-v4-pro --domain retail --limit 10
```

对于纯框架的终端 agent 任务，Terminal-Bench 2 将 Docker 生命周期和验证
委托给 Harbor。OpenCode 桥接有自己可复现的 benchmark 命令，因此框架得分
绝不会被当作 CLI 桥接得分。在隔离环境中安装 Harbor 以及本地的框架/CLI
检出（checkout），然后在开始真实运行前验证前置条件：

```bash
uv venv .venv-bench --python 3.13
uv pip install --python .venv-bench/bin/python -e ../nonoka-agent -e . harbor
export NONOKA_HARBOR_BIN="$PWD/.venv-bench/bin/harbor"
nonoka-cli doctor --check-benchmarks
nonoka-cli benchmark smoke --model deepseek/deepseek-v4-pro
nonoka-cli benchmark terminal-bench --model deepseek/deepseek-v4-pro
```

Terminal-Bench 命令会构建全新的本地 wheel，暂存（stage）构建好的 OpenCode
provider 及其依赖，并把经过验证的宿主机 OpenCode 与 Python 3.13 运行时
制品复制到每个 Harbor 任务容器中。该运行时还会注册到 uv 约定的
managed-Python 目录，因此非 root 的 agent 和官方验证脚本都能复用它，
无需再下载另一个解释器。这确保官方验证器观察到的是 OpenCode 通过当前
nonoka 桥接在任务文件系统上运行——而不是宿主机侧的 shell。要在消耗模型
token 之前验证环境 provisioning，先运行一个固定任务：

```bash
nonoka-cli benchmark terminal-bench --task regex-log --install-only
```

适配器还会把暂存的 `uv` 暴露为 `/root/.local/bin/uv` 和 `uvx`。
即使验证脚本替换了该二进制文件，仍能找到已注册的 Python 运行时。
这不会预装任何任务特定的测试包、数据或解答制品。

真实的 benchmark 运行默认不施加累计的模型轮数、工具调用或单次模型调用
限制。一小时的进程看门狗（watchdog）仅用于防止 agent 进程丢失或永久卡死。
当需要一个有意限额的运行配置时，使用 `--max-turns`、`--tool-budget`
或 `--timeout`。

Harbor 通过其 `${DEEPSEEK_API_KEY}` 环境变量模板接收模型凭据。该值绝不会
被写入 benchmark manifest 或制品目录。

每次桥接运行都会在 `.nonoka/eval/opencode/` 下写入已脱敏的 manifest、
OpenCode JSON 事件、provider/bridge 的 trace，以及指向官方 Harbor 任务
目录的引用。需要 Docker 访问权限。

SWE-bench Lite 使用官方验证器，其桥接制品与 Terminal-Bench 分开存放。
它需要官方的 `swebench` 包、Docker、至少 120 GiB 可用磁盘空间，以及
16 GiB 内存才能完成一次完整的 Lite 运行。在资源受限的主机上，可以生成并
验证单个明确指定的实例：

```bash
nonoka-cli benchmark swe-bench --instance-id django__django-10914 \
  --model deepseek/deepseek-v4-flash \
  --swebench-python /path/to/swebench-venv/bin/python \
  --max-workers 1 \
  --artifact-dir .nonoka/eval/swe-flash-django-10914
```

要在不再调用模型的情况下验证之前生成的预测结果，改为传入其官方的
`predictions.jsonl`：

```bash
nonoka-cli benchmark swe-bench --instance-id <instance-id> \
  --predictions /path/to/predictions.jsonl \
  --artifact-dir .nonoka/eval/swe-lite-<instance-id>
```

该命令会写入验证器命令、stdout/stderr、`diagnosis.json` 以及一份人类可读的
诊断报告。它把基础设施、bridge/provider、agent-loop 和验证器失败分开归类；
当一次健康的桥接运行失败时，可以使用同一实例做显式的 Aider 或原生
OpenCode 对比。

### 已验证的 benchmark 与回归结果

当前的 verification-contract（验证契约）与有界多 agent 实现已针对本地同级
`nonoka-agent` 检出通过了以下检查：

- `543` 个确定性的 nonoka-agent 测试通过（`45` 个可选的真实调用测试被取消选择）。
- `280` 个 nonoka-cli 单元、集成和桥接测试通过。
- `62` 个 OpenCode provider 测试通过，随后 TypeScript 构建成功。
- 一次干净的 OpenCode TUI 多 agent 运行使用 `agent__spawn` 完成规划与审查，通过有界的后续读取恢复了两次故意制造的不完整文件观察，完成了一次真实的工作区变更，并通过了全部 `16` 个专项验收测试。其最终回复轮未暴露任何工具，也未发起宿主机工具调用。
- 八个不同实例的官方 SWE-bench Lite 验证：`astropy__astropy-12907`、`django__django-10914`、`django__django-10924`、`django__django-11001`、`django__django-11099`、`pytest-dev__pytest-11143`、`pallets__flask-4045` 和 `sympy__sympy-11400`。

固定的 `swe-flash-selected10-v1` 回归样本使用 `deepseek/deepseek-v4-flash` 解决了 10 个实例中的 6 个。后续的 `deepseek/deepseek-v4-pro` 运行独立解决了多个 Django 及跨项目实例，并在 verification-contract 修复之后，解决了此前失败的 `pallets__flask-4045` 和 `sympy__sympy-11400` 实例。所有报告的结果均来自官方 SWE-bench 验证器，而非模型自行断言。它们是有针对性的工程样本，而不是对完整 SWE-bench Lite 得分的声明。

使用 `deepseek/deepseek-v4-pro` 针对固定的 Terminal-Bench 2 修订版本
（`69671fba`）进行的参考性端到端验证，在三个不同任务上获得了官方 Harbor
奖励 `1`：`sanitize-git-repo`、`configure-git-webserver` 和
`break-filter-js-from-html`。最近一次针对 `break-filter-js-from-html` 的
bridge 加固重跑在 10 分 21 秒内无异常完成。这些是单次试验的工程检查，
不是具有统计代表性的排行榜得分。

`benchmark smoke` 会通过本地 `file:` 依赖把 OpenCode 固定到检出中构建的
provider，并在 benchmark 工作区中临时写入一份隔离的 `opencode.json`。
请使用干净的 `--cwd`（或传入 `--provider-source`），这样它就绝不会覆盖
已有项目的 OpenCode 配置。

## 配置

### `nonoka-cli config init`

交互式向导，写入 `~/.config/nonoka/config.yaml`。它会询问模型标识符
（例如 `deepseek/deepseek-v4-pro`、`openai/gpt-4o`、`ollama/llama3.3`）、
一个以掩码方式输入的 API key，以及是否将其保存到
`~/.config/nonoka/.env`（推荐）、直接写入 `config.yaml`，或跳过保存。
它还会询问系统提示词（system prompt）以及是否自动批准所有工具调用。

非交互示例：

```bash
nonoka-cli config init --yes --model openai/gpt-4o
```

### `nonoka-cli config set <key> <value>`

更新单个配置值。支持点分隔的键：

```bash
nonoka-cli config set model openai/gpt-4o
nonoka-cli config set cli.theme light
nonoka-cli config set hitl.dangerous_tools '["write_file", "execute_command"]'
```

### `nonoka-cli config show`

打印解析后的配置及其文件路径。

### 成本控制与响应缓存

Runner 默认维护一个本地 SQLite 精确响应缓存。它只存储没有工具调用的完整
响应，其键包含模型、完整消息历史、工具 schema、生成参数和工作区命名空间。

语义复用是可选开启的，因为它有更严格的正确性契约。它只针对 Git worktree
中确定性的、无工具的、单轮请求使用 OpenAI 兼容的 embedding 端点。每次
completion 之前都会根据 `HEAD`、已跟踪/未跟踪的变更、repo-map 索引、
系统提示词和工作区路径重新计算缓存作用域。因此，同一 OpenCode 会话中
较早的一次写入会立即使语义候选失效。非 Git 工作区、工具调用和多轮对话
会回退到模型。

```yaml
cache:
  enabled: true
  path: ~/.cache/nonoka/llm-cache.sqlite3
  ttl_seconds: 604800
  semantic_enabled: true
  embedding_model: qwen3.7-text-embedding
  embedding_api_base: https://your-endpoint/compatible-mode/v1
  embedding_api_key_env: DASHSCOPE_API_KEY
  embedding_dimensions: 1024
  semantic_threshold: 0.92

budget:
  max_total_tokens: 120000
  max_cost_usd: 5.0
  fail_on_unknown_cost: true
```

请将 embedding 凭据保存在指定的环境变量或 `~/.config/nonoka/.env` 中，
切勿写入 `config.yaml`。语义命中被记录为节省的用量而非实际花费；事件存储
会暴露缓存来源和相似度得分，但不存储原始缓存查询。`max_total_tokens` 和
`max_cost_usd` 是随任务会话持久化的硬限制；当价格数据不可用时，
`fail_on_unknown_cost: true` 会终止任务，而不是默默地超出成本预算。

### `nonoka init`

在当前目录生成或合并 `opencode.json`，并根据你的 nonoka `system_prompt`
创建 `.opencode/agents/build.md`。生成的配置将 OpenCode 指向
`nonoka-opencode-provider` 包，并把 nonoka 配置路径传给后端。

## OpenCode 配置

`nonoka init` 生成两样东西。`nonoka opencode init` 作为显式的旧写法继续兼容：

1. 当前目录中的 `opencode.json`，它把 OpenCode 接入
   `nonoka-opencode-provider` 包并设置 HITL 权限。
2. `.opencode/agents/build.md`，其中包含 agent 提示词。

一个典型的生成后的 `opencode.json` 如下：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "nonoka/default",
  "provider": {
    "nonoka": {
      "npm": "nonoka-opencode-provider",
      "name": "Nonoka",
      "options": {
        "serverCommand": ["/path/to/python", "-m", "nonoka_cli", "--server"],
        "cwd": ".",
        "configPath": "~/.config/nonoka/config.yaml"
      },
      "models": {
        "default": { "name": "Nonoka deepseek-v4-pro" }
      }
    }
  },
  "permission": {
    "*": "ask",
    "bash": "ask",
    "edit": "ask",
    "write": "ask"
  },
  "agent": {
    "build": {
      "mode": "primary",
      "permission": {
        "*": "ask",
        "bash": "ask",
        "edit": "ask",
        "write": "ask"
      }
    }
  },
  "tools": {
    "skill": false
  }
}
```

`"tools": {"skill": false}` 这一行禁用了 OpenCode 原生的 `skill:<name>`
工具，以免与 nonoka 的 `load_skill` / `skill__<name>__<tool>` 工作流冲突。
`nonoka init` 会自动写入该设置。

## 提示词归属

Nonoka 通过 `~/.config/nonoka/config.yaml` 中的 `system_prompt` 拥有规范的
系统提示词。当你运行 `nonoka init` 时，该命令会调整这个提示词
并将其写入 `.opencode/agents/build.md`，使 OpenCode 将其用于主 agent。
OpenCode 特有的指南（工具名称、审批行为、路径约定）会自动追加；它们不会
混入 Nonoka 的核心提示词，因此同一份配置将来也能用于其他前端。

## 人在回路（HITL）

在 OpenCode 内部运行时，HITL 由 OpenCode 自己处理。生成的 `opencode.json`
设置了 `"*": "ask"`，因此每个工具都需要批准。由于 Nonoka 会把 OpenCode 的
原生工具定义转发给模型，`bash`、`read`、`write` 和 `edit` 操作的审批对话框
都能以原生方式渲染。

在 OpenCode 模式下，`nonoka init` 会从 `cli.auto_approve` 和
`nonoka.yaml` 中可选的 `permissions` 覆盖项派生两处生成权限块。要让 YAML
保持为单一事实来源，可以添加 `permissions` 后重新运行 `nonoka init`：

```yaml
permissions:
  read: allow
  bash: ask
  write: ask
  edit: ask
```

`cli.auto_approve: true` 会先自动允许核心编码工具，包括只读的 `glob` 和
`grep`，再应用显式覆盖。对于独立模式，Nonoka 自有工具的审批仍由
`hitl.policy` 控制。

## 外部工具模式

当 nonoka-cli 在 OpenCode 内部运行时，默认工作在**外部工具模式**
（external-tools mode）。OpenCode 将其原生工具列表（例如 `bash`、`read`、
`write`、`edit`、`todowrite`）发送给 provider；nonoka-cli 将它们注册为
`ExternalCapability` 对象，并由 OpenCode 负责执行。这意味着：

- OpenCode 负责工具执行、HITL 审批和 TUI 渲染。
- nonoka 负责决策：调用哪个工具、何时调用、使用什么参数。
- 工具结果由 OpenCode 返回，并通过 `Runner.resume_external_tools()` 恢复执行。

要启动外部工具模式，使用生成的 `opencode.json` 运行 OpenCode；
provider 会使用生成项目配置时的同一个 Python 解释器，自动派生
`python -m nonoka_cli --server`。

## MCP 与 Skill 支持

nonoka-cli 可以把 MCP 工具和懒加载的 skill 与 OpenCode 的原生工具合并使用。
在 `~/.config/nonoka/config.yaml` 中配置：

```yaml
model: deepseek/deepseek-v4-pro

mcp_servers:
  filesystem:
    transport: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/docs"]

skills:
  - code-review
  - nextjs-best-practices
```

每个 skill 应放在项目的 `.agents/skills/<name>/SKILL.md`，或作为用户级 skill 放在 `~/.agents/skills/<name>/SKILL.md`。项目定义优先；旧的扁平 `skills/<name>.md` 文件仍会被识别。

- **MCP 工具**由 nonoka-cli 在本地执行，并带有 `mcp__<server>__<tool>`
  命名空间前缀，因此不会与 OpenCode 原生工具冲突。
- **Skills** 使用 nonoka-agent 的懒加载 `SkillRegistry`。发现阶段只读取名称和描述，不导入 skill 工具；已启用 skill 的工具会在构建运行时目录时解析，而完整指南、skill 根目录和随附资源路径通过 `load_skill` 按需加载，并免受常规上下文压缩。在外部工具模式下，skill 工具带有 `skill__<skill>__<tool>` 前缀。

MCP 工具和 skill 工具在独立模式下均保持可用，且不加任何前缀。

### Skill 工具的导入路径

skill 文件在 YAML frontmatter 中用 `import` 条目列出其工具：

```yaml
---
name: greet
description: A simple greeting skill.
tools:
  - import: greet_tool:say_hello
---
When loaded, use the say_hello tool to greet the user by name.
```

`greet_tool:say_hello` 通过 Python 的常规导入机制解析，因此该模块必须能从项目工作目录（或 `PYTHONPATH` 上的某个目录）导入。对于随 skill 分发的工具文件，请使用相对于 `SKILL.md` 的路径，例如 `file: scripts/greet_tool.py:say_hello`。

### 避开 OpenCode 的原生 `skill` 工具

OpenCode 有自己的 `skill:<name>` 语法，与 nonoka 的 `skill__<name>__<tool>`
命名空间和 `load_skill` 工具冲突。生成的 `opencode.json` 通过
`"tools": {"skill": false}` 禁用原生 skill 工具。如果你手写
`opencode.json`，请保留该设置，确保模型只使用 nonoka 管理的 skills。

## Git 安全网

`nonoka-cli` 可以在每次文件变更前自动创建 git 检查点（checkpoint），并在
失败时回滚。在 `nonoka.yaml` 中启用：

```yaml
git:
  auto_checkpoint: true
  auto_commit: true
```

启用后，模型可以在危险操作前调用 `git_checkpoint`，出问题时调用
`git_rollback` 恢复到上一个检查点。

## Repo map

对于大型仓库，`nonoka-cli` 会构建一个轻量级符号索引，并把 repo map 注入
系统提示词。通过让模型获得类、函数和导出的结构性概览，减少盲目的文件读取。

在 `nonoka.yaml` 中配置：

```yaml
repo_map:
  enabled: true
  max_tokens: 4000
```

`build_repo_map` 和 `search_repo_map` 工具让模型可以按需刷新或查询索引。

## 子 agent 工作流

项目可以在 `.nonoka/plugin.json` 中声明有界的顾问型角色。每个有效角色会成为
一个名为 `agent__<role>` 的本地工具；由主 Agent 决定是否委派给它。

```json
{
  "schema_version": "1.0",
  "name": "project-agents",
  "agents": [
    {
      "name": "planner",
      "description": "Produce a concise implementation plan.",
      "model": "deepseek/deepseek-v4-pro",
      "system_prompt": "Return a numbered plan, risks, and focused checks.",
      "max_turns": 2,
      "max_invocations": 1,
      "allowed_tools": []
    },
    {
      "name": "reviewer",
      "description": "Review a proposed change for blocking defects.",
      "model": "deepseek/deepseek-v4-pro",
      "system_prompt": "Return blocking issues, missing requirements, and an approval decision.",
      "output_contract": "review",
      "max_turns": 3,
      "max_invocations": 2,
      "allowed_tools": []
    }
  ],
  "dynamic_agent": {
    "enabled": true,
    "model": "deepseek/deepseek-v4-pro",
    "base_system_prompt": "You are a temporary advisory sub-agent. Return concise, actionable findings to the parent.",
    "max_turns": 2,
    "max_invocations": 2,
    "max_instruction_chars": 2000,
    "max_context_chars": 16000
  }
}
```

工具输入为 `{"task": "...", "context": "..."}`。子 Agent 使用隔离的记忆、
单级委派、有界轮数，且不使用任何工具。它们不能读取或修改工作区，因此父级
必须在 `context` 中包含所有相关证据；它们的输出是顾问性的，不能替代编辑
或验证。

当 `dynamic_agent.enabled` 为 true 时，主 Agent 还会获得 `agent__spawn`。
它可以选择有界的 `role`、`instructions`、`task` 和 `context`，但项目策略
固定了模型、基础提示词、轮数预算、调用预算和输入大小。动态创建的子级同样
没有工具，也不能再创建其他 agent。该工具有意不接受任何 `model`、`tools`、
权限或预算参数。

在满足配置要求的变更与验证证据之后，Nonoka 会使用一个无工具的收尾轮
（finalization turn）。这防止任务已经完成之后，可选的清理或重复检查消耗
剩余的轮数预算。因此，对于证据门控（evidence-gated）的运行，`maxTurns`
只计入工作轮，运行时额外保留一次模型调用，专门用于产出最终回复。

验证生效的角色配置：

```bash
nonoka-cli plugin validate --manifest .nonoka/plugin.json
```

设置 `NONOKA_DISABLE_PROJECT_AGENTS=1` 可同时禁用静态项目角色和动态派生。
Benchmark 配置会自动设置该变量，使现有的单 Agent 得分保持可比。

## 插件 manifest

项目可以通过 `.nonoka/plugin.json` 声明自己的 Nonoka 插件：

```json
{
  "schema_version": "1.0",
  "name": "my-plugin",
  "skills": [{"name": "code-review"}],
  "agents": [],
  "mcp_servers": {},
  "allowed_tools": ["read", "edit", "bash"]
}
```

当 `nonoka-cli opencode init` 运行时，manifest 会与用户级配置合并，并转换为
OpenCode 的 skill/permission 格式。完整示例见
`.nonoka/plugin.json.example`。

## 已知限制

以下是在 OpenCode CLI 1.17.18 上观察到的当前行为。在此记录是因为它们影响
TUI/HITL 体验，但无法在 `nonoka-cli` 或 `nonoka-opencode-provider` 内部修复。

- [x] **OpenCode 原生 `skill` 工具与 nonoka skills 冲突**：生成的
  `opencode.json` 通过 `"tools": {"skill": false}` 禁用它，适配器提示词会
  告知模型只使用 `load_skill` 和 `skill__<name>__<tool>`。
- [x] **外部目录拒绝操作会导致 OpenCode 崩溃**：适配器现在会把当前工作目录
  注入系统提示词，并指示模型使用相对于它的路径，因此工作区之外的请求很
  少见。如果仍然发生并且你选择了 **Reject**，OpenCode 仍可能退出。请把请求
  限定在当前工作目录内，或者在路径安全时选择批准。
- [ ] **工作区内的 `write` 会被自动批准**：即使 `opencode.json` 中设置了
  `"*": "ask"`，OpenCode 也不会对工作区根目录内的 `write` 操作显示审批
  对话框。`bash`、`read` 和 `edit` 会询问。
- [ ] **代码块渲染为普通缩进文本**：OpenCode 把 Python 及其他代码渲染为普通
  缩进输出，而不是带语法高亮的围栏代码块。这是 OpenCode TUI 的渲染选择。
- [ ] **短回复会留下垂直空白**：OpenCode TUI 使用 flex 布局，因此简短的
  assistant 回复会显示在顶部，状态栏上方留有可见空白。这是 OpenCode 正常的
  布局行为。
- [ ] **模型可能对含糊的请求跳过工具调用**：适配器提示词缓解了这个问题，但
  含糊的请求仍可能导致模型直接回答而不调用 `read`/`edit`。请把文件/工具
  请求写明确。

## 服务器日志与请求 trace

在 OpenCode 内部运行时，provider 会将绑定当前解释器的
`python -m nonoka_cli --server` 作为长驻的
NDJSON 桥接派生。provider 会把服务器 stderr 重定向到一个按工作目录区分的
日志文件，以免污染 OpenCode 的 TUI：

```text
/tmp/nonoka-server-<cwd-hash>.log
```

此外，`nonoka-cli --server` 会为调试写入每个请求和流事件的结构化 NDJSON
trace：

```text
/tmp/nonoka-trace/trace-YYYYMMDD.jsonl
```

你可以用 `NONOKA_TRACE_DIR` 环境变量覆盖 trace 目录。

### 调试环境变量

| Variable | Effect |
| --- | --- |
| `NONOKA_DEBUG=1` | Emit `debug` NDJSON events from the bridge for every request and stream transition. |
| `NONOKA_TRACE_DIR=/path` | Directory for NDJSON request/event traces (default: `/tmp/nonoka-trace`). |
| `NONOKA_SERVER_LOG=/path` | Override the server stderr log path when running the bridge manually. |

## 开发

```bash
# Install in editable mode
uv pip install -e .

# Run the bridge server
nonoka-cli --server --config ./nonoka.yaml

# Lint and test
uv run --no-sync ruff check .
uv run --no-sync pytest tests/unit
```

## 项目结构

```text
src/nonoka_cli/
├── bridge/          # NDJSON protocol, request handler, server
├── commands/        # CLI subcommands (config, doctor, opencode)
├── config/          # YAML config loading and Pydantic models
├── core/            # Orchestrator, RunnerService, SessionService, ToolService,
│                    # MCPService, AgentFactory, prompt/context/task-state/output pruning,
│                    # git safety net, repo map, project agents, and plugin manifest
│                    #   agent_factory.py              # Build nonoka Agent from CLI config
│                    #   prompt_builder.py             # System prompt assembly for OpenCode mode
│                    #   context_trimmer.py            # Turn-based context window trimming
│                    #   task_state.py                 # Local TODO state mirror
│                    #   tool_output_policy.py         # Tool output pruning / spill policy
│                    #   git_service.py                # Git checkpoint / rollback helpers
│                    #   repo_map_service.py           # Symbol index generation and search
│                    #   plugin_manifest.py            # .nonoka/plugin.json loader
│                    #   project_agents.py             # Compile bounded manifest roles
│                    #   plugin_manifest_converter.py  # OpenCode skill/permission conversion
├── mcp/             # MCP server lifecycle manager (thin wrapper around nonoka-agent)
├── sessions/        # Session metadata persistence
├── skills/          # Skill loading shim (delegates to nonoka-agent SkillRegistry)
├── tools/           # Built-in and local tool loader
└── utils/           # Errors, logging, trace logger

packages/nonoka-opencode-provider/  # TypeScript provider for OpenCode
install.sh                          # One-line installer
```

## 许可证与署名

`nonoka-cli` 和 `nonoka-opencode-provider` 基于 MIT License 发布。

终端 TUI 和 OpenCode 客户端/服务器架构由
[OpenCode](https://github.com/anomalyco/opencode)（MIT License）提供。
agent 核心由 [Nonoka](https://pypi.org/project/nonoka/) 框架提供。

完整细节见 `LICENSE` 和 `NOTICE`。
