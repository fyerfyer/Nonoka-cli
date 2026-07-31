# Nonoka CLI 面试现场演示 Runbook

这份文档用于一次由演示者亲自操作的完整演示。目标不是执行一个预制脚本后展示结果，而是从陌生用户安装开始，进入一个已有代码仓库，通过自然语言让 OpenCode/Nonoka 创建并配置 MCP Server、Skill 和自定义 Tool，重启后真实加载这些能力，最后让 Agent 修复代码并完成验证。

建议把正式演示控制在 10–15 分钟。依赖下载和模型响应都有不确定性，因此现场保留一个预热环境和一份成功 trace 作为兜底，但主流程仍由演示者逐条输入命令和自然语言消息。

## 1. 演示要证明什么

演示结束时，面试官应该能清楚看到以下边界：

1. `install.sh` 从 GitHub 获取，负责安装 CLI、OpenCode 和 provider，并生成基础配置。
2. `nonoka-cli` 通过 stdio bridge 接入 OpenCode，而不是替换 OpenCode TUI。
3. OpenCode 原生工具负责读取、编辑和执行命令。
4. Nonoka runtime 负责模型循环、MCP 生命周期、Skill、项目自定义 Tool、运行预算和验证收据。
5. MCP、Skill 和自定义 Tool 不是写进 YAML 就算完成，而是会被真实发现和调用。
6. 在已有仓库中，Agent 先读代码和测试、保留已有内容，再进行有界修改和 focused verification。

## 2. 面试前必须完成的发布准备

当前工作区中的修复不能仅推送 `install.sh`。普通安装模式会从 PyPI/npm 获取实际包，因此发布顺序应为：

1. 发布包含 MCP SDK 与 finalization 修复的 `nonoka-agent` 新版本。
2. 更新并发布依赖该 Agent 版本的 `nonoka-cli` 新版本。
3. 确认 `nonoka-opencode-provider` 的兼容版本已发布到 npm。
4. 更新 CLI 中的 provider fallback 版本。
5. 提交并推送两个仓库以及本目录的 demo 文件。
6. 在全新 HOME 和虚拟环境中重新执行本文件的完整流程。

发布完成前，可以使用本文的“GitHub 源码安装”路径，但必须明确称为源码预览安装，不能称为最终用户安装。

## 3. 屏幕与凭据准备

不要在共享屏幕上输入 API key。面试前把 key 放到一个不会展示内容的文件中，例如：

```bash
mkdir -p ~/.config/nonoka
chmod 700 ~/.config/nonoka
# 在非共享环境中提前创建 ~/.config/nonoka/.env，并 chmod 600。
```

现场只展示检查结果：

```bash
test -s ~/.config/nonoka/.env && echo 'credential file: ready'
```

建议使用至少 100×30 的终端。关闭会弹出通知、用户名或 token 的 shell 插件。

## 4. 第一幕：模拟陌生用户从 GitHub 安装

### 4.1 正式发布后的推荐路径

使用临时 HOME，避免演示成功依赖自己电脑上的既有配置：

```bash
export NONOKA_USER_ROOT=/tmp/nonoka-interview-user
mkdir -p "$NONOKA_USER_ROOT/home" "$NONOKA_USER_ROOT/workspace"

uv venv "$NONOKA_USER_ROOT/.venv" --python 3.13
export HOME="$NONOKA_USER_ROOT/home"
export VIRTUAL_ENV="$NONOKA_USER_ROOT/.venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"

cd "$NONOKA_USER_ROOT/workspace"
curl -fL \
  https://raw.githubusercontent.com/fyerfyer/Nonoka-cli/main/install.sh \
  -o "$NONOKA_USER_ROOT/install.sh"

bash "$NONOKA_USER_ROOT/install.sh" --help
bash "$NONOKA_USER_ROOT/install.sh" \
  --uv --yes \
  --npm-opencode \
  --opencode-version 1.18.9 \
  --cli-version <已发布的 CLI 版本>
```

先下载、查看参数、再执行，比直接 `curl | bash` 更容易说明供应链边界。如果面试官希望看一行安装，可以补充展示：

```bash
curl -fsSL \
  https://raw.githubusercontent.com/fyerfyer/Nonoka-cli/main/install.sh |
  bash -s -- --uv --yes --cli-version <已发布的 CLI 版本>
```

将预先准备的 key 文件复制到临时 HOME 时，不要输出文件内容：

```bash
mkdir -p "$HOME/.config/nonoka"
cp /安全位置/nonoka-demo.env "$HOME/.config/nonoka/.env"
chmod 600 "$HOME/.config/nonoka/.env"
```

### 4.2 尚未发布时的 GitHub 源码路径

`--dev` 必须在完整 checkout 中运行；不能对 raw `install.sh` 使用 `--dev`。CLI 和 Agent 应保持相邻目录：

```bash
export NONOKA_SOURCE_ROOT=/tmp/nonoka-source-install
mkdir -p "$NONOKA_SOURCE_ROOT"
cd "$NONOKA_SOURCE_ROOT"

git clone --depth 1 \
  https://github.com/fyerfyer/Nonoka-agent.git nonoka-agent
git clone --depth 1 \
  https://github.com/fyerfyer/Nonoka-cli.git nonoka-cli

uv venv "$NONOKA_SOURCE_ROOT/.venv" --python 3.13
export VIRTUAL_ENV="$NONOKA_SOURCE_ROOT/.venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"

cd "$NONOKA_SOURCE_ROOT/nonoka-cli"
NONOKA_AGENT_ROOT="$NONOKA_SOURCE_ROOT/nonoka-agent" \
  bash ./install.sh --dev --uv --yes
```

### 4.3 安装验收

```bash
nonoka-cli --version
opencode --version
nonoka-cli doctor
```

讲解重点：`doctor` 不只是检查二进制，还检查 framework/provider/bridge protocol/config/API key 和 OpenCode provider 配置。

## 5. 第二幕：进入一个已有代码仓库

获取只包含业务代码和故意失败测试的 ParcelWatch baseline。不要复制本目录的 `capabilities/`，因为这一幕要让 Agent 自己创建能力：

```bash
export NONOKA_ASSET_ROOT=/tmp/nonoka-demo-assets
export NONOKA_PROJECT=/tmp/nonoka-live-project

git clone --depth 1 \
  https://github.com/fyerfyer/Nonoka-cli.git "$NONOKA_ASSET_ROOT"
mkdir -p "$NONOKA_PROJECT"
cp -R "$NONOKA_ASSET_ROOT/examples/interview-demo/project/." "$NONOKA_PROJECT/"

cd "$NONOKA_PROJECT"
git init -q
git add .
git -c user.name='Nonoka Demo' -c user.email='demo@nonoka.local' \
  commit -qm 'parcelwatch baseline'

uv pip install --python "$VIRTUAL_ENV/bin/python" -e .
pytest -q
```

此时预期是稳定的 `3 failed`。这一步先向面试官证明 bug 已存在，而不是让 Agent 修改一个没有验收标准的空目录。

### 5.1 创建项目本地配置

务必显式使用项目本地配置，不要依赖全局配置发现顺序：

```bash
nonoka-cli config init \
  --config ./nonoka.yaml \
  --yes \
  --model deepseek/deepseek-v4-pro \
  --auto-approve

nonoka-cli opencode init \
  --config ./nonoka.yaml \
  --cwd . \
  --yes

nonoka-cli doctor --config ./nonoka.yaml
```

启动第一轮 TUI：

```bash
nonoka-cli run --config ./nonoka.yaml --cwd .
```

## 6. 第三幕：用自然语言让 Agent 建立能力

这一轮只创建项目能力，不修业务 bug。把下面整段作为第一条自然语言消息发送给 OpenCode：

```text
请先把当前已有仓库配置成一个可验证的 Nonoka 项目，但暂时不要修复 ParcelWatch 的业务代码和失败测试。

请先检查仓库、现有 nonoka.yaml 和已安装的 Nonoka 配置格式，然后完成以下工作：

1. 在 .nonoka/mcp/ 下创建一个轻量、无额外网络依赖的 stdio MCP Server。它必须实现 initialize、notifications/initialized、ping、tools/list 和 tools/call，并暴露 get_reconciliation_contract 工具，返回 carrier-feed 的权威约束。
2. 在 .agents/skills/reconciliation-workflow/ 下创建一个标准 SKILL.md，以及一个可调用的 check_transition Skill Tool，用于检查 CREATED -> IN_TRANSIT -> DELIVERED 状态转换。
3. 在 .nonoka/tools/ 下创建 profile_feed 自定义 Python Tool。它应只读分析 JSONL，返回行数、malformed 行号、重复 event_id 和时区后缀。
4. 更新项目根目录的 nonoka.yaml：加入上述 mcp_servers、skills 和 tool_paths；使用相对项目根目录的路径；保留现有 model、预算、安全和 git 设置。
5. 创建一个有界的 verify_capabilities.py。它必须真实启动 MCP 子进程、完成工具发现，并分别调用 MCP Tool、Skill Tool 和 custom Tool；不能只检查文件存在。
6. 运行 Python 语法检查和 capability verifier。不要修改 src/parcelwatch 或 tests。
7. 最后明确告诉我哪些能力需要重启 OpenCode/Nonoka 后才会进入下一次模型上下文。

直接编辑工作区并验证，不要只给示例代码或补丁文本。
```

### 为什么需要人工重启

当前 server 在初始化时启动 YAML 中的 MCP，并据当时的 `tool_paths` 和 `skills` 构建 Agent。虽然代码中存在显式 `reload_config()`/`reload_tools()` API，但 OpenCode bridge 当前没有把它们暴露为自然语言可调用的运行时控制命令，也不会自动重启新增 MCP。

因此第一轮结束后，应由演示者执行：

```bash
git diff -- nonoka.yaml .nonoka .agents
python verify_capabilities.py
nonoka-cli doctor --config ./nonoka.yaml
nonoka-cli opencode init --config ./nonoka.yaml --cwd . --yes
```

然后退出并重新启动：

```bash
nonoka-cli run --config ./nonoka.yaml --cwd .
```

这不是演示失败，而是一个清晰的生命周期边界：Agent 可以通过自然语言安装项目能力；新能力在下一次 runtime 初始化时生效。

## 7. 第四幕：让 Agent 使用新能力修复已有项目

重启后发送第二条自然语言消息：

```text
现在修复 ParcelWatch 的 carrier-feed reconciliation，并把这次运行作为能力集成审计。

开始改代码前必须：
1. load reconciliation-workflow Skill，并调用它的 check_transition 工具检查至少一个合法和一个非法状态转换；
2. 调用 product_contract MCP 的 get_reconciliation_contract 获取 carrier-feed 契约；
3. 调用 custom profile_feed 分析 fixtures/carrier_feed.jsonl。

然后检查现有源码和失败测试，修复以下行为：重复 event_id 必须 first-valid-wins；时区时间戳按同一时间线排序；非法状态跳转不能改变当前状态；所有拒绝项保留原始 source line；CLI JSON 必须确定性输出并以换行结束。

保留所有无关代码和已有测试。必要时增加有针对性的边界测试。完成 TODO bookkeeping 后，从项目根目录执行：

NONOKA_VERIFY=focused ../.venv/bin/pytest -q

只有看到真实收集的测试通过后才结束，并总结根因、修改行为和精确测试结果。
```

演示者应在 TUI 中指出这些可见事件：

- `load_skill`
- `skill__reconciliation-workflow__check_transition`
- `mcp__product_contract__get_reconciliation_contract`
- `custom__profile_feed`
- OpenCode 原生 `read` / `edit` / `bash` / `todowrite`
- 最后的 focused verification receipt 和纯文本总结

## 8. 演示后的人工验收

```bash
git status --short
git diff --stat
git diff
NONOKA_VERIFY=focused ../.venv/bin/pytest -q
```

如果启用了 trace：

```bash
jq -c \
  'select(.event=="execution_trace" and .trace.termination.success==true)' \
  /tmp/nonoka-trace/trace-$(date +%Y%m%d).jsonl |
  tail -1 |
  jq '{termination:.trace.termination,
       verification:.trace.verifications[-1],
       tools:([.trace.tool_calls[]?.name] | unique)}'
```

## 9. 可选：从空目录创建新项目

不建议在同一场面试里同时演示“从零创建”和“已有项目修 bug”，因为模型和依赖下载会占用时间。如果面试官更关心 greenfield，可以在空仓库发送：

```text
请在当前空仓库创建一个最小但完整的 Python 包 parcelwatch：使用 src layout、argparse CLI 和 pytest。先写 README 中的验收标准和失败测试，再实现 JSONL carrier event reconciliation。要求 first-valid-wins、UTC 时间线排序、状态机校验、带 source line 的拒绝记录和确定性 JSON 输出。创建 pyproject.toml，安装到当前虚拟环境，最后运行 NONOKA_VERIFY=focused pytest -q。不要引入 Web 框架或数据库。
```

已有项目修 bug 更适合展示代码理解、约束保留和验证纪律；从空目录创建更适合展示自主规划。现场选择一个作为主任务即可。

## 10. 已有代码目录的当前行为审查

### 当前做得比较好的部分

- `opencode init` 会解析并合并已有 `opencode.json`，不会直接覆盖未知顶层字段。
- 已有顶层 model 会被保留。
- 自定义 `.opencode/agents/build.md` 不会被覆盖；只有 Nonoka 管理的文件会刷新。
- repo map 和 git status 会在执行前注入上下文，Agent 能先看到已有仓库结构与 dirty 状态。
- OpenCode provider 会对宿主 edit/bash 生成 workspace mutation receipt，focused pytest 生成 typed verification receipt。
- 路径工具和 workspace attestation 会限制任务工作区，减少误改外部文件。

### 建议优先做的简单优化

#### P0：项目配置优先级

当前隐式查找顺序是：显式 `--config` > 全局配置 > `./nonoka.yaml`。对于已有项目，更符合直觉的顺序通常是：

```text
显式 --config > ./nonoka.yaml > 全局配置
```

否则用户在项目内创建了 MCP/Skill 配置，却可能继续使用全局配置。修改前要增加兼容提示或迁移期 warning。面试演示暂时始终显式传入 `--config ./nonoka.yaml`。

#### P0：不能只判断 opencode.json 是否存在

`nonoka-cli run` 当前看到已有 `opencode.json` 就跳过初始化。建议至少验证：

- `provider.nonoka` 是否存在；
- `serverCommand` 和 `configPath` 是否有效；
- provider 版本是否兼容；
- 当前 model 是否真的指向 `nonoka/default`。

如果不是，应提示用户执行 merge，或者提供 `--repair-opencode-config`，而不是静默启动一个可能没使用 Nonoka 的 TUI。

#### P1：已有权限配置的合并预览

`opencode init` 会保留大部分未知字段，但会重建 permission block，并强制关闭 OpenCode 原生 Skill 以避免命名空间冲突。建议增加：

- `--dry-run` 或 diff preview；
- 写入前备份；
- 明确列出被 Nonoka 管理的字段；
- 对用户已有 deny 规则给出冲突提示。

#### P1：dirty worktree 的诚实提示

当前 git status 会进入 Agent 上下文，但 OpenCode 原生 `edit` 并不经过 CLI standalone file tool 的 `GitService` checkpoint。因此不要宣称 `git.auto_checkpoint` 已覆盖所有宿主编辑。

建议启动时展示：

```text
Existing repository: dirty (3 paths)
Host edits are workspace-attested; automatic Git checkpoint is not active for host-native edits.
Recommended: commit/stash or continue explicitly.
```

不要自动提交或 stash 用户已有修改。

#### P2：目录与语言预检

对不存在的 `--cwd`、无写权限目录、非 Git 大仓库、未安装测试依赖分别给出准确提示。目前不存在的 cwd 可能被错误归类成 OpenCode executable 消失。

## 11. LSP 到底有没有用

### 当前真实状态

Nonoka Core 有两处 LSP 相关实现：

1. `lsp_document_symbols`：通过 `multilspy` 请求单个文件的 document symbols。
2. repo map：当配置了 `repo_map.lsp_languages` 且安装了可用后端时，批量使用 LSP 提取符号；失败或超时后降级到 tree-sitter、ctags、regex。

但是在当前 OpenCode external-tools 路径中：

- 独立的 `lsp_document_symbols` 没有注册成 `nonoka__...` bridge tool；
- TUI 侧显示的 `LSPs are disabled` 是 OpenCode 自己的 LSP 状态，不是 Nonoka repo map 状态；
- 默认 demo venv 没有安装 `multilspy`、tree-sitter 或语言服务，因此这次成功演示实际使用的是 repo-map regex fallback；
- 开发环境虽然安装了 `multilspy/tree-sitter`，当前 PATH 中没有 `jedi-language-server`、`pyright-langserver` 等独立 binary。

所以面试时最准确的说法是：

> Nonoka 有可选 LSP-backed symbol indexing，但它不是当前默认安装和 OpenCode bridge 的强保证。默认能力是带缓存的 repo map，并有 tree-sitter/ctags/regex 降级；OpenCode 自己的 LSP 是另一个宿主能力。当前 demo 不依赖 LSP 才能成功。

### 建议的最小产品优化

1. 增加 `nonoka-cli doctor --check-lsp`，输出依赖、语言、binary 和一次 document-symbol smoke test。
2. 在 repo-map trace/cache 中记录实际 backend：`lsp`、`tree_sitter`、`ctags` 或 `regex`，避免“配置了 LSP 就认为已经用了”。
3. 给 installer 增加明确的 `--repo-map` 或 `--lsp` extra 安装选项，而不是默认配置开启、依赖却未安装。
4. 二选一明确产品边界：要么在 external-tools Agent 中条件注册 `nonoka__lsp_document_symbols`；要么正式声明代码诊断交给 OpenCode native LSP，Nonoka 只负责 repo map。
5. 为 Python/TypeScript 各增加一个真实集成测试，断言 symbol 坐标和 backend，而不只断言输出里出现函数名。

如果时间只够做一个改动，优先实现 `doctor --check-lsp` 加 backend 可观测性。这比直接安装更多 language server 更容易解释，也更符合“能力必须可证明”的设计原则。

## 12. 面试官常见追问

### “为什么新增 MCP 后要重启？”

因为 MCP 是有生命周期的子进程，Agent 的工具 schema 也在 runtime 初始化时生成。当前 bridge 没有暴露安全的动态重载控制面；重启能保证旧进程关闭、配置重新校验、tool schema 一致。后续可以做显式 reload transaction，但不应该偷偷热更新。

### “Skill 和 prompt 有什么区别？”

Skill 不只是长 prompt。它有标准目录、惰性加载入口、可携带脚本/参考材料，并可暴露带 schema 的工具。模型先调用 `load_skill`，再通过 `skill__<skill>__<tool>` 使用能力。

### “为什么 MCP 不直接用 bash 调脚本？”

MCP 提供标准初始化、工具发现、JSON Schema 和结构化调用边界。bash 只能证明脚本能运行，不能证明 Agent 通过协议发现并调用了它。

### “已有代码安全吗？”

回答应限定在已经实现的边界：OpenCode 工具受工作目录和 permission 控制；provider 生成 workspace attestation；Nonoka 使用验证收据和 completion contract。对于用户已有 dirty changes，目前应先提示并由用户选择，不能声称宿主 edit 已有完整自动 Git checkpoint。

### “为什么不用 LSP 也能做代码任务？”

repo map、精确搜索、文件读取和测试反馈已经构成可靠的基础闭环。LSP 能提高符号定位和诊断质量，但不是正确性的唯一来源；最终正确性仍由真实测试和 typed verification receipt 证明。

## 13. 故障兜底

### 模型 API 或现场网络失败

```bash
cd /tmp/nonoka-interview-demo
./verify-wiring.sh
tmux attach -t nonoka-interview-final3
```

说明这是之前同版本的成功演练，然后展示 trace 中的实际工具名和 verification receipt。

### MCP 没有加载

1. 确认使用的是项目配置：`nonoka-cli doctor --config ./nonoka.yaml`。
2. 检查 MCP command 是否使用当前虚拟环境的 Python。
3. 直接运行 `python verify_capabilities.py`。
4. 退出并重新启动 OpenCode；不要在旧 session 中反复要求模型调用不存在的 tool。

### TUI 显示 LSP disabled

不要临时下载一套语言服务拖慢演示。说明 OpenCode native LSP 与 Nonoka repo map 是两个能力层；本任务通过 repo map/search/tests 完成，LSP 不属于本次验收前提。

### 恢复 baseline

```bash
cd "$NONOKA_PROJECT"
git restore .
git status --short
pytest -q  # 应重新出现 3 failed
```

只恢复本次 demo 的已跟踪文件，不对包含用户资料的宽泛目录执行清理命令。
