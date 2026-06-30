# nonoka-cli / OpenCode Provider — 已知未实现项

> 当前为 Phase 1 验证版本，端到端文本回复已跑通。下面列出还需要补全或优化的地方，供后续迭代参考。

## 1. OpenCode Provider 层

### 1.1 Tool-call / Tool-result 透传
- 现状：provider 只处理 `text_delta`、`finish`、`error` 三种 NDJSON 事件。
- nonoka 后端自己有一个工具循环（read/bash/grep 等），它调用工具后会把结果写回自己的上下文，然后继续生成文本。
- 问题：OpenCode 看不到 nonoka 的工具调用过程，也无法用 OpenCode 统一的工具权限/审批去管理。
- 目标：把 nonoka 的 `tool_call` / `tool_result` 事件映射为 Vercel AI SDK 的 `tool-call` / `tool-result` stream parts，让 OpenCode 接管工具循环；或者提供一个开关让 nonoka 在 server 模式下禁用自带工具。

### 1.2 Session 持久化
- 现状：provider 每次 `doStream` 都会 `spawn` 一个新的 `nonoka-cli --server` 进程，旧的 session 上下文随着进程退出而丢失。
- 影响：多轮对话时 nonoka 后端无法记住之前的对话内容（虽然 OpenCode 会把历史消息拼进 prompt，但 nonoka 内部的 agent 状态会丢失）。
- 目标：让 provider 复用同一个 long-running server 进程，或在 `session_init` 后通过持久化存储恢复 session。

### 1.3 错误与取消处理
- `abortSignal` 目前会 kill 子进程，但 nonoka 后端对 SIGTERM/SIGKILL 的清理逻辑未充分测试。
- 需要规范 `error` 事件的格式，并在 provider 里生成正确的 `error` stream part。
- 子进程 stdout 解析失败的行当前被静默丢弃，应该记录日志或抛出。

### 1.4 构建与分发
- 当前只有 ESM 输出（`tsc` 编译到 `dist/`）。
- 建议增加 CJS / dual-build，或直接用 `bun build` 打成单文件 bundle，避免用户环境里出现 `@ai-sdk/provider` 等依赖解析问题。
- `package.json` 需要补齐 `exports`、`keywords`、`repository`、`bugs`、`homepage` 等字段。

### 1.5 Provider 配置校验
- `serverCommand` 目前支持字符串和数组，但没有校验命令是否为空、是否可执行。
- `configPath` / `cwd` 等路径未做 exists 检查。
- 建议用 `zod` 做一次配置 schema 校验。

## 2. nonoka-cli `--server` 后端

### 2.1 协议扩展
- 当前 NDJSON 协议只有 `chat` 请求和 `session_init` / `text_delta` / `finish` / `error` 响应。
- 需要增加：
  - `tool_call` / `tool_result` 事件（配合 1.1）。
  - `approval_request` / `approval_response` 事件，让 OpenCode 侧决定敏感操作。
  - `thinking` / `reasoning` 事件（如果模型支持）。
  - `usage` 字段（input/output tokens），让 OpenCode 统计消耗。

### 2.2 并发与生命周期
- `BridgeServer` 目前一个进程只处理一条请求然后退出。
- 需要支持多条 NDJSON 请求顺序或并发处理，并正确维护 Orchestrator 生命周期。
- 需要处理客户端断开、stdin EOF、子进程异常退出等边界情况。

### 2.3 配置与模型
- `nonoka.yaml` 里的 `model: deepseek-chat` 在 LiteLLM 某些版本下需要写成 `deepseek/deepseek-chat`。
- 建议把 provider / base_url / api_key 也显式暴露到配置里，而不是只依赖环境变量。

## 3. OpenCode 集成体验

### 3.1 本地路径 vs npm 包名
- 现在 `opencode.json` 里用绝对路径指向 provider 包，只是为了避免 OpenCode 去 npm registry 安装时卡死（当前环境有代理问题）。
- 发布 npm 后应改回 `"npm": "@nonoka/opencode-provider"`，并验证 OpenCode 能自动安装。

### 3.2 模型元数据
- `models.default` 目前只有 `name`。
- 应补齐 `limit.context`、`limit.output`、`modalities` 等，让 OpenCode 正确计算上下文窗口和费用。

### 3.3 TUI 验证
- 目前主要用 `opencode run` 非交互验证。
- 需要在 TUI 里手动验证 `/models` 选择、`/connect` 流程、多轮对话渲染等。

## 4. 测试

- [ ] provider 单元测试（stream transformer、协议编解码）。
- [ ] `nonoka-cli --server` 端到端测试（发送 NDJSON 请求，断言事件顺序）。
- [ ] OpenCode 集成测试（用本地 registry 安装 provider 后跑 `opencode run`）。
- [ ] 错误场景测试（无效命令、模型不可用、网络超时）。

## 5. 文档

- [ ] provider README（安装、配置、options 说明）。
- [ ] nonoka-cli README 增加 `--server` 模式说明。
- [ ] OpenCode 配置示例（本地路径 / npm 包名 / 私有 registry）。
