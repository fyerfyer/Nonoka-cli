# Nonoka 面试前可靠性加固设计

状态：2026-07-31 已实现 P0，待完成全新 HOME 安装演练与真实 TUI 冒烟。

## 1. 目标与边界

本轮目标不是扩展 Agent 能力，而是让已经能完成复杂任务的链路具备三个性质：

1. 启动前能证明配置和依赖是一致且可执行的；
2. 启动失败时能在一分钟内定位到配置、provider、bridge 或 runtime；
3. 面试演示只陈述可由 trace、测试和 verifier 支持的能力。

本轮不做 Skill/MCP 热加载、插件市场、大规模 LSP、多 Agent 编排或新内置工具。

## 2. 架构判断

```text
OpenCode TUI
  -> 项目 opencode.json
  -> nonoka-opencode-provider (TypeScript / AI SDK LanguageModelV3)
  -> nonoka-cli --server (stdio NDJSON bridge)
  -> nonoka-agent (Agent / Runner / Session / Tool runtime)
```

职责边界：

- OpenCode 拥有 TUI、原生工具执行和审批交互；
- provider 负责 AI SDK stream、host tool receipt、workspace attestation 和进程生命周期；
- CLI 负责配置、能力发现、bridge、doctor、日志与安全 preflight；
- agent core 负责模型循环、持久 session、预算、tool/MCP/skill 执行、trace 和 verification contract。

因此，启动可靠性不是 agent core 的新能力问题，而是 OpenCode 配置、npm provider、CLI 可执行文件和 YAML 配置之间的发布契约问题。

## 3. 单一配置真相

统一解析顺序：

```text
显式 --config
  > <cwd>/nonoka.yaml
  > ~/.config/nonoka/config.yaml
```

`opencode init` 必须将本次实际解析到的绝对路径写入
`provider.nonoka.options.configPath`。`run` 不再独立重新猜测配置，而是读取并验证
`opencode.json` 记录的路径；用户又传入 `--config` 时，两者必须相同。

这与 OpenCode 官方的 locality 规则一致：项目 `opencode.json` 覆盖全局配置；官方自定义
provider 也通过 `provider.<id>.npm` 指定实际加载的 AI SDK 包。

参考：

- https://opencode.ai/docs/config/
- https://opencode.ai/docs/providers/#custom-provider
- https://ai-sdk.dev/

## 4. Readiness contract

`nonoka-cli run --cwd PROJECT` 启动 OpenCode 前必须通过以下检查：

| 项目 | 失败条件 | 处理 |
| --- | --- | --- |
| working directory | 不存在或不是目录 | 立即失败，不创建目录 |
| opencode.json | 缺失 | 自动调用项目 init |
| JSON/provider | JSON 无效或没有 `provider.nonoka` | 失败，提示 init/doctor |
| model | 不是 `nonoka/*` | 失败，避免静默绕过 Nonoka |
| configPath | 缺失、不存在或 YAML 无效 | 失败，打印实际路径 |
| serverCommand | 缺失或不可执行 | 失败，提示安装 CLI |
| provider | 项目包缺失或版本低于兼容下限 | 失败；不被全局旧包掩盖 |
| explicit config | 与 opencode.json 不同 | 失败并给出重建命令 |

通过后显示有界 readiness 信息：Project、Config、Model、Provider、能力数量、Git dirty
状态，以及“repo-map backend 在 runtime 解析”的诚实说明。这里不声称已使用 LSP。

## 5. 初始化事务语义

初始化顺序调整为：

1. 校验 cwd；
2. 解析并验证 Nonoka YAML；
3. 解析已有 `opencode.json`；
4. 安装或确认精确版本的项目 provider；
5. 原子替换 `opencode.json`；
6. 生成/刷新受管理 agent prompt；
7. 输出 config/provider/readiness。

provider 安装失败时不写 `opencode.json` 和 `.opencode/agents/build.md`。包管理器仍可能创建
`package.json` 或 lockfile，这是它自身的事务边界；错误输出会明确说明“provider 未就绪、
OpenCode 配置未写入”。

## 6. 故障可观测性

provider 的进程错误现在包含：

- 直接 cause 或 server exit code；
- configPath；
- serverCommand；
- provider 精确版本；
- provider log 配置；
- Nonoka server stderr log；
- 可复制的 `doctor --cwd ... --config ...` 命令；
- 有界 stderr tail。

`doctor --cwd` 使用与 `run` 相同的项目目录和配置/provider 校验。项目 provider 缺失时，
全局或 cache 里的旧版本只作为补充事实报告，不再替代项目依赖。

`logs --trace ... --limit N` 现在只分析最后 N 个 execution traces；limit 不再只影响
events.db 查询。

## 7. 已确认而无需重复实现的点

- `.nonoka`、常见测试缓存以及由 `NONOKA_TRACE_DIR`、`NONOKA_EVENT_DB`、
  `NONOKA_RUN_EVIDENCE_PATH`、`NONOKA_PROVIDER_LOG_PATH` 指定的 runtime artifacts 已从
  provider workspace effect 中排除，并已有 provider 测试。
- OpenCode 原生工具和 Nonoka 本地 MCP/Skill 的 trust boundary 已在架构文档、doctor 和
  bridge receipt 中区分。
- provider/CLI/framework 已有 bridge protocol version 和 capability handshake。

## 8. LSP 与 repo-map 陈述

推荐面试表述：

> Nonoka 的 repo-map 可以配置 LSP/Tree-sitter/ctags 等解析能力，但实际 backend 会根据
> 环境解析和降级。OpenCode TUI 的 LSP 状态属于宿主。当前 readiness 只报告 repo-map
> 已配置，不把配置存在等同于本次运行使用了 LSP。

后续可新增独立的 `doctor --check-lsp`，输出语言、可执行文件、握手耗时和实际 fallback；
不应在面试前扩展语言矩阵。

## 9. 演示脚本

建议按以下顺序演示：

```bash
nonoka-cli doctor --cwd "$PROJECT" --config "$PROJECT/nonoka.yaml"
nonoka-cli run --cwd "$PROJECT" --config "$PROJECT/nonoka.yaml"
nonoka-cli logs --trace "$TRACE" --limit 20
```

叙事顺序：

1. readiness 证明运行的是哪个项目、YAML、模型和 provider；
2. 执行一个需要 MCP、Skill、自定义 tool、代码修改和 focused verifier 的任务；
3. 展示 tool host/receipt、verification 和 terminal reason；
4. 故意展示一个无害的无效配置，说明 preflight 如何阻止半初始化或静默绕过；
5. 用有界 trace 总结任务，而不是滚动数千行原始日志。

不要现场声称 OpenCode 原生 edit/write 受 `GitService.auto_checkpoint` 完整保护；应描述为
provider workspace attestation + OpenCode 权限/沙箱边界，Git checkpoint 只覆盖其实际接管
的 Nonoka 路径。

## 10. 发布门禁

发布前必须满足：

- CLI deterministic pytest 全通过；
- provider Bun tests 和 TypeScript build 全通过；
- agent deterministic pytest 使用相邻源码 checkout 全通过；
- 三方版本和 bridge protocol/capabilities 一致；
- `git diff --check` 在双仓通过；
- 全新 HOME 下完成安装、init、doctor、run、失败恢复演练；
- 真实 TUI 任务保留 trace、provider log、server log、focused verifier 输出；
- 发布使用现有 release script，先 dry-run/build，不在验证任务中自动 publish。

## 11. 后续优先级

P1：

- provider 安装后的显式 readiness probe（不仅检查 package.json）；
- `doctor --check-lsp` 和实际 repo-map backend/降级原因；
- 将 dirty worktree 的路径摘要写入 session metadata；
- 配置文件变化检测并显示 `restart required`；
- 统一 OpenCode/provider/server 日志索引，而不是依赖用户知道多个路径。

P2：

- 只有固定 eval slice 证明收益后再做热加载、更多 LSP 或受限 delegation；
- 只有本地 operational signals 出现真实扩展瓶颈后再考虑分布式控制面。
