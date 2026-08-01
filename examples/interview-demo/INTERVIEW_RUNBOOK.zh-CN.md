# Nonoka CLI 技术面试演示与答辩 Runbook

> 最后实测：2026-07-31。本文是供演示者逐步操作的手册，不是一键脚本。

这套演示要呈现一条完整但诚实的产品链路：陌生用户从 PyPI/npm 安装，进入一个已有且测试失败的代码仓库，配置项目 YAML，创建 MCP Server、Skill 和自定义 Tool，验证并重启加载，最后让 OpenCode/Nonoka 调用三类能力修复真实 Bug。正式面试建议只演示 10–15 分钟主路径；完整冷启动留作讲解或备份。

## 1. 一句话叙事与成功标准

建议开场这样说：

> Nonoka 不替代 OpenCode，而是在它下面提供 Agent runtime、能力注册、预算、验证收据和安全边界。今天我会从一个干净环境开始，让它进入已有仓库，真实装载 Skill、MCP 和自定义 Tool，再用这些能力完成一个带状态机、去重、时区排序和审计要求的 Bug 修复。

结束时必须有可核验的五项证据：

1. PyPI 安装的 `nonoka-cli`、`nonoka` 与 npm 安装的 OpenCode/provider 版本。
2. baseline 确实是 `3 failed`。
3. capability verifier 不只看文件存在，而是真实启动 MCP 并调用三类能力。
4. TUI trace 中出现 `load_skill`、`skill__...`、`mcp__...`、`custom__...` 和宿主读写/测试工具。
5. 独立终端复验为 `3 passed`，并人工检查 `git diff`；模型的最终自述不作为证据。

## 2. 面试前的发布与屏幕准备

普通安装会从 PyPI/npm 拉包，不能只把本地 `install.sh` 改好。面试前确认以下版本组合已发布并重新冷启动验证：

```text
nonoka-cli                 0.2.13
nonoka                     1.3.8
nonoka-opencode-provider   0.2.17
OpenCode                   1.18.10
Python                     3.13.9
```

上述是 2026-07-31 实测组合，不代表未来版本永远兼容。面试当天应冻结版本，不临场升级。

API key 预先放进不展示内容的文件，并限制权限：

```bash
mkdir -p ~/.config/nonoka
chmod 700 ~/.config/nonoka
# 在停止共享屏幕时创建 ~/.config/nonoka/.env，并 chmod 600。
```

现场只显示：

```bash
test -s ~/.config/nonoka/.env && echo 'credential file: ready'
```

建议准备两个 tmux 窗口：`demo` 跑 TUI，`audit` 看 diff、日志和独立测试。终端至少 100×30，并关闭可能显示用户名或 token 的 shell 插件。

## 3. 完整冷启动：在 `/tmp` 从零安装

### 3.1 创建隔离目录

安装器现在可以自己创建 venv 和隔离的 npm prefix，不需要预先激活 venv，
也不需要为安装后的每条命令反复 `export`。先只保存本次演示根目录：

```bash
NONOKA_DEMO_ROOT="$(mktemp -d /tmp/nonoka-interview-XXXXXX)"
mkdir -p "$NONOKA_DEMO_ROOT/artifacts"
```

复制凭据时不要打印内容：

```bash
mkdir -p "$NONOKA_DEMO_ROOT/config"
cp /安全位置/nonoka-demo.env "$NONOKA_DEMO_ROOT/config/.env"
chmod 600 "$NONOKA_DEMO_ROOT/config/.env"
```

### 3.2 先下载再执行安装器

```bash
curl -fL \
  https://raw.githubusercontent.com/fyerfyer/Nonoka-cli/main/install.sh \
  -o "$NONOKA_DEMO_ROOT/install.sh"

bash "$NONOKA_DEMO_ROOT/install.sh" --help
UV_CACHE_DIR="$NONOKA_DEMO_ROOT/uv-cache" \
bash "$NONOKA_DEMO_ROOT/install.sh" \
  --uv --yes --npm-opencode \
  --install-dir "$NONOKA_DEMO_ROOT/install" \
  --config-dir "$NONOKA_DEMO_ROOT/config" \
  --npm-prefix "$NONOKA_DEMO_ROOT/npm" \
  --opencode-version 1.18.10 \
  --cli-version 0.2.13
```

如果要在现场展示交互选择，去掉 `--yes` 和三个目录参数。安装器会逐项显示
“这里存什么 + 示例 + 默认值”，输入自定义路径或直接按 Enter 即可。

先落盘审阅比 `curl | bash` 更容易解释供应链边界。源码预览安装必须 clone 相邻的 `nonoka-agent` 与 `nonoka-cli` checkout 后使用 `--dev`，不能对 raw 脚本直接传 `--dev`。

如果面试前的新修复尚未发布，在同一个 venv 中覆盖为相邻 checkout 的 editable 版本，并明确称为“源码候选版”：

```bash
uv pip install --python "$NONOKA_DEMO_ROOT/install/.venv/bin/python" \
  -e /home/fyerfyer/fyerfyer/Projects/nonoka-agent \
  -e /home/fyerfyer/fyerfyer/Projects/nonoka-cli
```

### 3.3 安装验收

更新后的 editable 版本已经补齐统一入口。现场使用：

```bash
NONOKA="$NONOKA_DEMO_ROOT/install/bin/nonoka"
NONOKA_PYTHON="$NONOKA_DEMO_ROOT/install/.venv/bin/python"
OPENCODE="$NONOKA_DEMO_ROOT/install/npm/bin/opencode"

"$NONOKA" --version
"$OPENCODE" --version
"$NONOKA" doctor
```

这三个只是当前 shell 的普通变量，不会污染子进程环境；也可以始终输入上面的
绝对路径。launcher 会在内部设置 venv、配置目录、npm prefix 和 PATH。

旧版 0.2.11 的 `nonoka-cli --version` 会报 `unrecognized arguments: --version`；这是本次彩排发现并已在源码中修复的问题。

## 4. 创建“已有项目”baseline

从仓库复制的只是故意带 Bug 的业务项目，不复制预制 capabilities：

```bash
NONOKA_SOURCE=/home/fyerfyer/fyerfyer/Projects/nonoka-cli
NONOKA_PROJECT="$NONOKA_DEMO_ROOT/project"
mkdir -p "$NONOKA_PROJECT"
cp -R "$NONOKA_SOURCE/examples/interview-demo/project/." "$NONOKA_PROJECT/"

cd "$NONOKA_PROJECT"
git init -q
git add .
git -c user.name='Nonoka Demo' -c user.email='demo@nonoka.local' \
  commit -qm 'parcelwatch baseline'

uv pip install --python "$NONOKA_PYTHON" -e . pytest
"${NONOKA_PYTHON%/python}/pytest" -q
```

预期输出必须是：

```text
3 failed
```

讲解话术：

> 我先固定失败基线，避免演示变成“模型改了很多，但没有可验证验收标准”。这个仓库也已经提交 baseline，便于明确区分用户原有内容和 Agent 修改。

## 5. 初始化项目配置

当前查找顺序已经是：

```text
显式 --config > <cwd>/nonoka.yaml > ~/.config/nonoka/config.yaml
```

项目配置优先于全局配置是合理的；现场仍显式传路径，让命令、OpenCode provider 与 bridge 使用同一文件：

```bash
"$NONOKA" config init \
  --config ./nonoka.yaml \
  --yes \
  --model deepseek/deepseek-v4-pro \
  --auto-approve
```

### 5.1 sandbox 缺陷、源码修复与发布版兼容

0.2.11 的默认配置是 `sandbox: auto`、`required: true`，但 `doctor` 与 `run` 对 `auto` 的解析不一致：doctor 会在无 SRT 时回退 Docker，而 `run` 只接受 SRT。因此 Docker 检查全绿时，启动仍可能报：

```text
Error: required SRT sandbox is unavailable.
```

安装 `@anthropic-ai/sandbox-runtime` 后又会出现第二个问题：外层 TUI 已由 SRT 包裹，bridge 初始化再次执行 required SRT smoke test，形成嵌套 sandbox，可能因 `/tmp/claude/srt-mux-*.sock` 的 `EPERM` 失败。

更新后的源码会由外层 launcher 标记 SRT process-tree ownership；bridge required preflight 和 custom command 识别该 owner 后不再嵌套启动 SRT。`auto` 模式的 standalone command 在没有 SRT 时也会正确回退 Docker。源码候选版可以保持：

```yaml
safety:
  enabled: true
  sandbox: auto
  required: true
  allowed_domains:
    - api.deepseek.com
    - models.opencode.ai
    - registry.npmjs.org
```

并安装 process-tree backend：

```bash
npm install -g --prefix "$NONOKA_DEMO_ROOT/npm" @anthropic-ai/sandbox-runtime
"$NONOKA" doctor --config ./nonoka.yaml --check-sandbox
```

只有继续演示未修复的 PyPI 0.2.11 时，才使用本次实测过的临时兼容配置：保留外层 SRT，但关闭 bridge required preflight：

```yaml
safety:
  enabled: true
  sandbox: auto
  required: false  # 仅为绕过 0.2.11 的双重 preflight 缺陷
  allowed_domains:
    - api.deepseek.com
    - models.opencode.ai
    - registry.npmjs.org
```

`required: false` 不是建议的生产安全默认值。答辩时说明源码已修掉嵌套 ownership 和 standalone fallback；后续还应让 `doctor` 与 TUI process-tree readiness 共用同一份面向宿主工具的诊断语义。

SRT 网络默认 deny；空 allowlist 会让 `models.opencode.ai`、npm registry 或模型 API 返回 403。Linux SRT 长运行时还可能在项目根创建临时只读 mount point（例如 `.bashrc`、`.env`、`.gitconfig`），导致 OpenCode Git snapshot 报“can only add regular files”；SRT 退出会清理它们。若现场看到这类日志，应说明是宿主 snapshot 与长运行 sandbox mount 的交互，不要把临时占位提交进仓库。

初始化 OpenCode 并检查：

```bash
"$NONOKA" init --config ./nonoka.yaml --cwd . --yes
"$NONOKA" doctor --config ./nonoka.yaml
```

本次彩排发现旧实现的 `cli.auto_approve: true` 没有覆盖 `glob/grep`，并且 `CLIConfig` 缺少 `permissions` 字段，导致 YAML 覆盖项被静默忽略。更新后的源码已把 `permissions` 纳入 schema 和 round-trip 测试，并默认允许这两个只读工具。仍可在 YAML 中显式收紧某项，然后重新生成：

```yaml
permissions:
  glob: deny
  bash: ask
```

## 6. 第一轮：只生成能力文件，不修改生效配置

先启动 TUI：

```bash
tmux new-session -s nonoka-interview
cd "$NONOKA_PROJECT"
"$NONOKA" run --config ./nonoka.yaml --cwd .
```

将下面整段作为第一条自然语言指令发送给 OpenCode：

```text
请把当前已有 ParcelWatch 仓库准备成一个可验证的 Nonoka 能力项目，但这一轮不要修改 nonoka.yaml，也不要修复 src/parcelwatch 或 tests。

先检查仓库和已安装的 Nonoka Python API，然后只创建这些业务文件：

1. 在 .nonoka/mcp/server.py 创建一个只依赖 Python 标准库的 stdio MCP Server。它实现 initialize、notifications/initialized、ping、tools/list 和 tools/call，暴露 get_reconciliation_contract，返回状态值、合法转换、first-valid-wins 去重、时区排序和 source-line 审计约束。
2. 在 .agents/skills/reconciliation-workflow/ 创建标准 SKILL.md 和 check_transition.py。SKILL.md 必须有 name、description、tools 的 YAML frontmatter，tool entry 指向 check_transition.py:check_transition；Python 函数必须使用 from nonoka import tool 和 @tool。
3. 在 .nonoka/tools/profile_feed.py 创建只读 custom Tool，也使用 from nonoka import tool 和 @tool。它分析 JSONL 并返回总行数、malformed 行号、重复 event_id 和时区后缀分布。
4. 在项目根创建 verify_capabilities.py，但先不要运行。它应通过 ConfigLoader 读取 nonoka.yaml，真实启动 MCPManager，使用 AgentFactory 和 ToolLoader 发现能力，再分别 invoke MCP Tool、Skill Tool 和 custom Tool，最后 stop_all；不能只检查文件存在。
5. 运行 py_compile 检查你创建的 Python 文件。最后列出建议写入 nonoka.yaml 的配置片段，但不要亲自写入该文件，也不要重启 runtime。

直接编辑工作区，不要只返回示例或补丁文本。
```

为何不让 Agent 同时改 YAML：实测第一轮曾写成 `type: stdio` 和 mapping 形式的 `skills`。下一条消息触发 bridge 重载后，Pydantic 因缺少 `transport`、`skills` 不是 list 而拒绝启动，模型把自己的当前会话写坏，无法再收到错误并自修复。这是典型的配置自举陷阱。

第一轮结束后退出 TUI。先人工检查：

```bash
git diff -- .nonoka .agents verify_capabilities.py
python -m py_compile \
  .nonoka/mcp/server.py \
  .nonoka/tools/profile_feed.py \
  .agents/skills/reconciliation-workflow/check_transition.py \
  verify_capabilities.py
```

## 7. 人工事务式接线与能力验收

用编辑器把以下经过实测的 schema 添加到 `nonoka.yaml`；注意字段名是 `transport`，`skills` 是字符串列表：

```yaml
mcp_servers:
  parcelwatch:
    transport: stdio
    command: python3
    args:
      - .nonoka/mcp/server.py

skills:
  - reconciliation-workflow

tool_paths:
  - .nonoka/tools
```

然后按顺序运行：

```bash
"$NONOKA_PYTHON" verify_capabilities.py
"$NONOKA" doctor --config ./nonoka.yaml
"$NONOKA" init --config ./nonoka.yaml --cwd . --yes
```

verifier 的通过标准应同时包含发现与真实调用：

```text
Capability wiring OK: custom__profile_feed, load_skill, mcp__parcelwatch__get_reconciliation_contract, skill__reconciliation-workflow__check_transition
Capability invocation OK: MCP contract, Skill transition, custom profiler
```

建议讲解：

> YAML 写入本身不等于能力可用。这里先启动协议子进程、发现 schema、逐个 invoke，再重启 runtime 让新的工具集合进入下一次模型上下文。未来 CLI 应提供 scaffold 和“临时文件写入 → validate → 原子替换”，避免活跃会话被无效配置破坏。

当前 bridge 没有把 `reload_config()`/`reload_tools()` 暴露成安全的自然语言控制面，MCP 也有进程生命周期，所以重启是明确的产品边界，不应包装成热加载。

## 8. 第二轮：调用三类能力并修复 Bug

重新启动：

```bash
"$NONOKA" run --config ./nonoka.yaml --cwd .
```

发送本次实测成功的原始话术：

```text
现在修复 ParcelWatch 的 carrier-feed reconciliation，并把这次运行作为能力集成审计。

开始改代码前必须：
1. load reconciliation-workflow Skill，并调用 skill__reconciliation-workflow__check_transition 检查至少一个合法和一个非法状态转换；
2. 调用 mcp__parcelwatch__get_reconciliation_contract 获取 carrier-feed 契约；
3. 调用 custom__profile_feed 分析 fixtures/carrier_feed.jsonl。

然后检查现有源码和失败测试，修复以下行为：重复 event_id 必须 first-valid-wins；时区时间戳按同一时间线排序；非法状态跳转不能改变当前状态；所有拒绝项保留原始 source line；CLI JSON 必须确定性输出并以换行结束。

保留所有无关代码和已有测试。必要时增加有针对性的边界测试。完成 TODO bookkeeping 后，从项目根目录执行：

NONOKA_VERIFY=focused ../.venv/bin/pytest -q

只有看到真实收集的测试通过后才结束，并总结根因、修改行为和精确测试结果。
```

TUI 中应依次指出：

- `load_skill`
- `mcp__parcelwatch__get_reconciliation_contract`
- 两次 `skill__reconciliation-workflow__check_transition`
- `custom__profile_feed`
- OpenCode 原生 `read/edit/bash/todowrite`
- typed focused verification receipt 与 termination success

2026-07-31 冷启动实测为 15 个 framework turns、约 4 分 26 秒，最终 `3 passed in 0.01s`。因此不要承诺在 10–15 分钟内现场完成依赖下载、两轮能力生成和 Bug 修复的全部流程。

## 9. 独立验收：不要照读模型总结

另开 `audit` 窗口：

```bash
tmux new-window -t nonoka-interview -n audit
cd "$NONOKA_PROJECT"
git status --short
git diff --stat
git diff
NONOKA_VERIFY=focused ../.venv/bin/pytest -q
```

实测模型最终总结声称“新增了 targeted boundary tests”，但 `git diff` 显示它没有修改测试。因此应说：

> Agent 的总结是调查线索，不是审计记录。我以 diff、独立 pytest 和 trace receipt 为准。

trace 中还应核对：

- 真实 tool names，而非只看自然语言声明；
- verification receipt 是否有 collected tests 和 exit code；
- termination 是否 success；
- workspace attestation 是否对应当前 cwd。

当前 provider trace 的 usage 仍可能是 `{}`，token/cost 展示为零。这是 P1 instrumentation gap，不能把零解释为“没有成本”，也不要现场估算一个数字。

## 10. 正式面试的 10–15 分钟剪辑版

完整冷启动适合说明，不适合全部直播。推荐时间分配：

| 时间 | 现场动作 | 要证明的能力 |
| --- | --- | --- |
| 0–2 分钟 | 展示安装命令、版本和 `doctor` 结果 | 可安装性、依赖边界 |
| 2–4 分钟 | baseline `3 failed`，讲 YAML 和三类能力 | 已有项目理解、可组合能力 |
| 4–6 分钟 | 运行 capability verifier | 协议/工具真实可调用 |
| 6–11 分钟 | 在预热 TUI 发送第二轮话术并观察 tool calls | agent harness、任务执行 |
| 11–13 分钟 | 展示 diff 与独立 `3 passed` | 验证纪律、可审计性 |
| 13–15 分钟 | 主动讲 sandbox/LSP/usage 三个缺口 | 工程判断、诚实边界 |

准备三层兜底：

1. 主路径：已经接线但代码仍是 baseline 的预热项目。
2. 备份：同版本的成功 trace、provider/server 日志和最终 diff。
3. 最坏情况：不再请求模型，运行 verifier 和独立测试，沿 trace 讲解完整链路。

## 11. 已有代码目录的逻辑审查

### 当前合理的行为

- 配置优先级是显式路径、项目本地、用户全局，符合 locality 原则。
- `run` 会检查 cwd 存在且为目录，并校验 `--config` 与 `opencode.json` 中 `configPath` 的一致性。
- readiness banner 会展示配置、MCP/Skill/tool path 数量和 dirty path 数量。
- `opencode init` 合并现有 `opencode.json`，保留未知顶层字段和已有顶层 model；自定义 build agent 文件不会被无条件覆盖。
- repo map、git status、workspace attestation 和 typed verification receipt 能为已有仓库提供基础审计闭环。

### 建议按优先级打磨

#### P0：统一 sandbox resolution 和 ownership

`doctor` 的 `auto` 会选 SRT 或 Docker，`run` 的外层包装只认 SRT，standalone command 又有另一条路径。抽成同一个 resolver，并只让外层 TUI或 bridge 中的一层负责 preflight/隔离。加入无 SRT + Docker、SRT、嵌套启动三条回归测试。

#### P0：配置更新必须是事务

能力注册应由 CLI scaffold/template 生成，Agent 只填业务内容。写入流程采用临时文件、schema validate、capability smoke、atomic replace；失败时保持旧配置和当前会话可用。

#### P0：bridge 必须绑定初始化时的 Python 环境

本次 editable smoke 发现旧的生成值 `serverCommand: ["nonoka-cli", "--server"]` 会再次按宿主 PATH 查找 CLI：即使用户用 venv 里的绝对路径执行 `nonoka init`，OpenCode 仍可能启动另一份全局 CLI，最终表现为 provider 未收到 bridge protocol acknowledgement。源码现已生成：

```json
"serverCommand": ["/active/venv/bin/python", "-m", "nonoka_cli", "--server"]
```

这样 init、provider 和 bridge 使用同一个 editable/venv 环境；doctor 也会校验该绝对 executable 是否存在。

#### P1：已有配置 merge 可预览、可恢复

为 `opencode init` 增加 `--dry-run`/diff preview、写前备份和 managed-fields 列表。用户已有 deny 规则被重建或冲突时明确警告。

#### P1：dirty worktree 提示要诚实

OpenCode native `edit` 不经过 standalone file tool 的 `GitService` checkpoint，不能声称 `git.auto_checkpoint` 覆盖所有宿主编辑。启动时可显示：

```text
Existing repository: dirty (3 paths)
Host edits are workspace-attested; automatic Git checkpoint is not active for host-native edits.
Recommended: commit/stash or continue explicitly.
```

不要自动 stash、commit 或覆盖用户已有修改。

#### P1：权限语义对齐

本次已修复配置模型缺少 `permissions`、覆盖项被静默忽略及 `glob/grep` 未自动允许的问题，并增加 YAML → Pydantic → `opencode.json` round-trip 测试。后续仍建议生成配置前展示最终 permission matrix，并把 auto-approve 的精确范围写进 help。

#### P2：可诊断性

本次已补充标准 `--version`，并修复 `doctor` 在 `opencode --version` 超时后直接抛 traceback 的问题；诊断子进程现在把 timeout/OS error 转换为结构化检查结果。后续补充 `doctor --check-lsp`、config validate/scaffold，以及 trace 中的真实 repo-map backend、token/cost。错误信息应继续区分 cwd、OpenCode executable、provider、bridge、sandbox 与模型 API。

## 12. LSP 功能评估与答辩

### 当前事实

- `nonoka/tools/lsp.py` 有 `lsp_document_symbols`，通过可选 `multilspy` 请求单文件 symbols。
- repo map 可尝试 LSP，失败或超时后降级到 tree-sitter、ctags、regex。
- 2026-07-31 的 PyPI 冷启动环境没有 `multilspy`、tree-sitter、Python/TypeScript language-server binary；TUI 日志明确显示 OpenCode 的 LSP 全部 disabled。
- standalone 工具集合包含 `lsp_document_symbols`，当前 OpenCode external-tools Agent 没有把它暴露为模型可见 bridge tool。
- OpenCode native LSP 与 Nonoka repo-map LSP 是两个层次。本次成功修复依靠 repo map fallback、search/read 和真实测试，不依赖 LSP。

推荐答辩话术：

> Nonoka 的 LSP 是可选的 symbol-indexing enhancement，不是默认安装保证。默认可用能力是 repo map，并降级到 tree-sitter、ctags 或 regex；OpenCode native LSP 是宿主层的另一套能力。本次演示不依赖 LSP，最终正确性由真实测试和 verification receipt 证明。当前需要改进的是安装契约和 backend 可观测性，而不是宣称“代码里有 LSP 就已经生效”。

面试官可能继续问：

**“那为什么保留 LSP 代码？”**

> 对大型仓库，结构化 symbol、definition/reference 能降低搜索噪声和上下文成本；但它必须是可检测、可降级的加速层，不能成为正确性的单点依赖。

**“你怎么证明它真的工作？”**

> 目前还不能从默认 demo 证明。最小闭环是 `doctor --check-lsp` 输出依赖、binary、语言和 document-symbol smoke test，并在 trace/cache 标记实际 backend。然后为 Python 和 TypeScript 各做一个真实坐标集成测试。

**“会把它暴露给模型吗？”**

> 产品边界需要二选一：要么条件注册 `nonoka__lsp_document_symbols`；要么明确由 OpenCode native LSP 提供交互诊断，Nonoka 只做 repo map。当前状态介于两者之间，需要收敛。

## 13. 结合当前 Agent 岗位 JD 的演示侧重点

检索日期为 2026-07-31，优先使用官方在招页面：

- [Anthropic — Research Engineer, Agents](https://job-boards.greenhouse.io/anthropic/jobs/4017544008)：强调完成复杂任务的 LLM 项目、agent harness、memory/context、量化 benchmark 和 roadblocks。
- [Anthropic — Research Engineer, Model Evaluations](https://job-boards.greenhouse.io/anthropic/jobs/5198255008)：强调 agentic eval、可靠评测平台、observability、retry/regression，以及区分 model、harness、data、infrastructure 问题。
- [Anthropic — Engineering Manager, Agent Runtime Platform](https://job-boards.greenhouse.io/anthropic/jobs/5316593008)：强调安全隔离、credential-managed runtime、可组合 primitives、可靠性与运营质量。

因此这场演示不要只突出“模型会写代码”，而要主动映射：

| JD 关注点 | 演示证据 |
| --- | --- |
| Agent harness / composability | OpenCode host + Nonoka bridge + MCP/Skill/custom Tool |
| Complex task / roadblocks | ParcelWatch 多约束 Bug，以及配置自举和 sandbox 缺陷复盘 |
| Rigorous eval | 固定 baseline、真实 capability invoke、focused pytest、独立复验 |
| Observability | typed receipts、tool names、termination、workspace attestation；坦白 usage 缺口 |
| Secure runtime | allowlist、sandbox ownership 分析、凭据不进入参数/产物 |
| Context efficiency | repo map 与可选 LSP/fallback，Skill 惰性加载 |

如果被问“这个项目最能体现你的判断是什么”，推荐回答：

> 我没有把一次成功轨迹当成产品完成。我把模型、harness、配置、sandbox、provider 和 verifier 分层验证；例如模型说加了测试但 diff 并没有，我以独立测试和 trace 为准。这个区分能力比单次 demo 的成功更重要。

## 14. 常见追问的短答

**为什么 MCP 新增后要重启？**

MCP 是有生命周期的子进程，Agent tool schema 在 runtime 初始化时构建。当前没有安全的动态 reload transaction；重启能确保旧进程关闭、配置重新校验和 schema 一致。

**Skill 和 prompt 有什么不同？**

Skill 有标准目录和 frontmatter，可惰性加载说明、携带参考资料，并暴露带 schema 的 namespaced Tool。模型先 `load_skill`，再调用 `skill__<skill>__<tool>`。

**为什么 MCP 不直接用 bash 调脚本？**

MCP 提供初始化、发现、JSON Schema 和结构化调用边界。bash 只能证明脚本能跑，不能证明 runtime 经协议发现并调用它。

**已有代码安全吗？**

现有边界包括 cwd/permission、workspace attestation、验证收据和 completion contract。对 dirty changes 只提示、不自动 stash；宿主 native edit 目前没有完整自动 Git checkpoint，不能过度承诺。

**为什么失败测试命令写 `../.venv/bin/pytest`？**

本次 venv 位于项目同级的临时根目录；精确路径让 trace 能判定 focused runner，也避免意外使用系统 pytest。若目录结构不同应按实际 venv 调整。

## 15. 现场故障树

### 启动报 required SRT unavailable

确认 `doctor` 与 `run` 的 backend 不一致问题，使用第 5.1 节的已记录演示 workaround；不要临场反复安装组件。

### 模型 API、npm 或 OpenCode metadata 返回 403

检查 SRT `allowed_domains` 是否包含模型 API、`models.opencode.ai` 和 registry。不要打印 key。

### MCP/Skill/custom Tool 不可见

依次检查：

```bash
"$NONOKA_PYTHON" verify_capabilities.py
"$NONOKA" doctor --config ./nonoka.yaml
"$NONOKA" init --config ./nonoka.yaml --cwd . --yes
```

然后退出旧 session 并重新启动；不要在旧上下文里反复要求模型调用不存在的 Tool。

### YAML 写坏导致 bridge 起不来

对照第 7 节 schema：`transport` 不是 `type`，`skills` 是 list。恢复上一个已验证配置，再运行 verifier；未来产品用 atomic config transaction 解决。

### TUI 显示 LSP disabled

这是预期可解释状态。说明 OpenCode native LSP 与 Nonoka repo map 的边界，本次验收不依赖 LSP。

### 模型超时或现场网络不稳定

停止继续消耗时间，展示同版本成功 trace、provider/server 日志和最终 diff；现场再运行 capability verifier 与独立 pytest，仍可完整证明协议接线和结果。

### 恢复 baseline

只在专用 demo 仓库内执行：

```bash
cd "$NONOKA_PROJECT"
git restore .
git status --short
"${NONOKA_PYTHON%/python}/pytest" -q  # 应重新出现 3 failed
```

不要对工作区根目录或包含个人资料的目录执行递归清理。

## 16. 本次真实彩排记录

隔离根目录：`/tmp/nonoka-interview-audit-5AZGUr`。

冷启动结果：

```text
PyPI/npm install: success
doctor after Docker access: all green
baseline: 3 failed in 0.03s
capability discovery and invocation: passed
TUI framework turns: 15
TUI duration: 4m 26s
focused verification: 3 passed in 0.01s
independent verification: 3 passed in 0.01s
termination: success
usage: {}  # 未采集，不解释为零成本
```

修复后的 editable smoke（Python 3.13.9）还验证了：

```text
nonoka --version: 0.2.13
nonoka init: provider 0.2.17 installed and config generated
generated serverCommand: <editable-venv>/bin/python -m nonoka_cli --server
top/build permissions: glob + grep allowed; YAML override round-trip passed
outer SRT -> provider -> interpreter-pinned bridge: handshake passed
model response: NONOKA_EDITABLE_SMOKE_OK
completion guard: correctly rejected the deliberately unverified no-tool response
```

保留的本机证据：

```text
/tmp/nonoka-interview-audit-5AZGUr/artifacts/provider-phase1.log
/tmp/nonoka-interview-audit-5AZGUr/artifacts/provider-phase2.log
/tmp/nonoka-interview-audit-5AZGUr/artifacts/server-phase1.log
/tmp/nonoka-interview-audit-5AZGUr/artifacts/server-phase2.log
/tmp/nonoka-interview-audit-5AZGUr/artifacts/traces-phase2/trace-20260731.jsonl
```

这些 `/tmp` 路径只适用于当前机器且可能被系统清理。正式面试前应把去敏后的 trace、版本清单、diff 和测试输出复制到受控的演示 artifacts 目录，并再次确认不含 API key、HOME 真实路径或其他个人信息。
