# 轻量项目插件示例

这个示例只有两个文件：`.nonoka/plugin.json` 标记项目插件，
`.nonoka/tools/feed_summary.py` 提供一个只读 Python Tool。发现到插件时，Nonoka
会自动扫描 `.nonoka/tools/`，因此不需要再在 `nonoka.yaml` 写 `tool_paths`。

在演示目录执行：

```bash
cp -R /path/to/nonoka-cli/examples/lightweight-plugin/.nonoka .
nonoka plugin validate
# OpenCode 中执行 /reload；看到 MCP 状态后再发送下一条消息
```

然后要求模型调用 `custom__count_nonempty_lines`。在 OpenCode 里它会显示为由
Nonoka provider 执行的原生动态工具卡片；不会由 OpenCode 再执行一次。

从零创建同样的骨架：

```bash
nonoka plugin init --name feed-summary
mkdir -p .nonoka/tools
# 将使用 @tool 装饰器的 Python 文件放入 .nonoka/tools/
```
