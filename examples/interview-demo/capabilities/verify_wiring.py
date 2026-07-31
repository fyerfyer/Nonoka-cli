from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from nonoka_cli.config.loader import ConfigLoader
from nonoka_cli.core.agent_factory import AgentFactory
from nonoka_cli.mcp.manager import MCPManager
from nonoka_cli.tools.loader import ToolLoader


async def main(config_path: Path, project_root: Path) -> None:
  config_path = config_path.resolve()
  project_root = project_root.resolve()
  # Skill discovery intentionally follows the task working directory, just
  # like the OpenCode bridge. Make the verifier independent of the shell cwd.
  os.chdir(project_root)
  config = ConfigLoader.load(config_path)
  manager = MCPManager()
  # MCP's stdio context must be exited by the same asyncio task that entered
  # it. ``start_all`` intentionally starts servers in child tasks for normal
  # runtime latency, but this small standalone verifier values deterministic
  # teardown over parallel startup.
  for name, server_config in config.mcp_servers.items():
    await manager.start_server(name, server_config)
  try:
    factory = AgentFactory(
      config,
      mcp_manager=manager,
      tool_loader=ToolLoader(config.tool_paths),
    )
    agent = factory.build_with_external_tools([], cwd=project_root)
    names = {tool.name for tool in agent.tools}
    expected = {
      "custom__profile_feed",
      "load_skill",
      "skill__reconciliation-workflow__check_transition",
      "mcp__product_contract__get_reconciliation_contract",
    }
    missing = expected - names
    if missing:
      raise SystemExit(f"missing demo capabilities: {sorted(missing)}")
    print("Capability wiring OK:")
    for name in sorted(expected):
      print(f"  - {name}")

    by_name = {tool.name: tool for tool in agent.tools}
    contract = await by_name[
      "mcp__product_contract__get_reconciliation_contract"
    ].invoke(None, {"component": "carrier-feed"})
    if "first valid input observation wins" not in str(contract):
      raise SystemExit("MCP contract invocation returned unexpected content")
    transition = await by_name[
      "skill__reconciliation-workflow__check_transition"
    ].invoke(None, {"previous": "CREATED", "proposed": "IN_TRANSIT"})
    if "True" not in str(transition):
      raise SystemExit("Skill tool invocation returned unexpected content")
    profile = await by_name["custom__profile_feed"].invoke(
      None,
      {"path": str(project_root / "fixtures" / "carrier_feed.jsonl")},
    )
    if "evt-102" not in str(profile):
      raise SystemExit("Custom tool invocation returned unexpected content")
    print("Capability invocation OK: MCP contract, Skill transition, custom profiler")
  finally:
    await manager.stop_all()


if __name__ == "__main__":
  asyncio.run(main(Path(sys.argv[1]), Path(sys.argv[2])))
