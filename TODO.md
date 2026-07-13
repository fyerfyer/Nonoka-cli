# nonoka-cli / OpenCode Provider — 迭代清单

> 本文件记录已实现的能力与仍需后续迭代的事项。

## 已稳定实现 ✅

### Provider 层
- [x] `tool_call` / `tool_result` / `error` / `finish` / `text_delta` NDJSON 事件透传。
- [x] `tool_call` 携带 `metadata`，用于 host 路由外部 tool / MCP / skill。
- [x] Title generation 使用最小化 system prompt，并剥离 tools / external 定义，避免触发 API 400。
- [x] 独立的 chat session id 与 title session id，支持 `session_init` 持久化。

### nonoka-cli `--server` 后端
- [x] 外部工具 / 外部 MCP / 外部 Skill 的统一注册与命名空间前缀（`mcp__<server>__<tool>`、`skill__<skill>__<tool>`）。
- [x] `approval_request` / `approval_response` 事件与 deferred HITL。
- [x] 内部 MCP 与内部 Skill 在 OpenCode 模式下同样可用，且带命名空间前缀。
- [x] `load_skill` 返回 guidance 字符串，不再向 memory 注入 system message，避免破坏 tool_calls 后必须紧跟 tool message 的 API 约束。
- [x] `nonoka-cli opencode init` 生成 `opencode.json` 时自动设置 `"tools": {"skill": false}`，避免与 OpenCode 原生 skill 工具冲突。
- [x] 单测覆盖：nonoka-cli 126 项，nonoka-agent 455 项，provider 23 项。

### 日志
- [x] nonoka-agent skills 模块从标准库 logging 迁移到 structlog，解决 `Logger._log() got an unexpected keyword argument 'name'` 错误。

## 仍需后续迭代 ⏳

### Provider 层
- [ ] **Session 持久化**：当前 provider 每次 `doStream` 仍 spawn 新 `nonoka-cli --server` 进程；长期应复用 long-running server 或基于 `session_init` + 持久化存储恢复 session。
- [ ] **CJS / dual-build**：当前只有 ESM 输出，建议增加 CJS 或单文件 bundle。
- [ ] **Provider 配置校验**：`serverCommand`、`configPath`、`cwd` 等未做完整 schema 校验。
- [ ] **模型元数据**：`models.default` 应补齐 `limit.context`、`limit.output`、`modalities` 等。

### nonoka-cli `--server` 后端
- [ ] **并发与生命周期**：`BridgeServer` 当前按请求处理并退出；应支持顺序/并发多请求及优雅断开。
- [ ] **usage 字段**：NDJSON 中未返回 input/output token 消耗。
- [ ] **abortSignal**：kill 子进程后的清理逻辑需更充分测试。

### OpenCode 集成体验
- [ ] **外部 MCP host 支持**：OpenCode 1.17.18 仍不向 provider 下发 `mcpServers`，外部 MCP 路径暂无真实 host。
- [ ] **发布 npm 包后改回包名**：当前 `opencode.json` 使用 `"npm": "nonoka-opencode-provider"` 并从本地 verdaccio 安装；正式发布后验证 registry 安装。

### 文档
- [x] README 已更新 OpenCode 原生 skill 冲突、skill import 路径、配置示例。
- [ ] provider README（安装、配置、options 说明）待补齐。
