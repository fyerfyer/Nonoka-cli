from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nonoka_cli.config.loader import ConfigLoader
from nonoka_cli.config.manager import ConfigManager
from nonoka_cli.config.models import CLIConfig
from nonoka_cli.core.orchestrator import Orchestrator
from nonoka_cli.core.plugin_manifest import PluginManifest
from nonoka_cli.core.runner_service import RunnerService


def test_plugin_summary_lists_only_executable_project_agent_tools() -> None:
  orchestrator = Orchestrator(config=CLIConfig(model="parent-model"))
  orchestrator._plugin_manifests = [
    PluginManifest(
      agents=[
        {
          "name": "planner",
          "model": "child-model",
          "system_prompt": "Return a plan.",
        }
      ]
    )
  ]
  orchestrator._project_agent_tools = [SimpleNamespace(name="agent__planner")]

  summary = orchestrator._build_plugin_summary()

  assert "agent__planner" in summary
  assert "  - planner:" not in summary


def test_plugin_summary_hides_roles_disabled_by_validation() -> None:
  orchestrator = Orchestrator(config=CLIConfig(model="parent-model"))
  orchestrator._plugin_manifests = [
    PluginManifest(
      agents=[
        {
          "name": "planner",
          "model": "",
          "system_prompt": "",
        }
      ]
    )
  ]
  orchestrator._project_agent_tools = []

  summary = orchestrator._build_plugin_summary()

  assert "Agents:" not in summary
  assert "agent__planner" not in summary


async def test_reload_config_reconfigures_agent_for_the_next_opencode_turn(
  tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  config_path = tmp_path / "nonoka.yaml"
  ConfigLoader.save(CLIConfig(model="before"), config_path)
  manager = ConfigManager.load(config_path)
  orchestrator = Orchestrator(config=manager.get(), config_manager=manager)
  orchestrator._initialized = True
  factory = MagicMock()
  orchestrator._agent_factory = factory
  monkeypatch.chdir(tmp_path)

  ConfigLoader.save(CLIConfig(model="after", skills=["project-skill"]), config_path)

  config = await orchestrator.reload_config()

  assert config.model == "after"
  assert orchestrator.config.model == "after"
  factory.reconfigure.assert_called_once()
  assert factory.reconfigure.call_args.args[0].model == "after"


async def test_reload_config_refreshes_persisted_session_limits(
  tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  config_path = tmp_path / "nonoka.yaml"
  ConfigLoader.save(CLIConfig(model="before", agents={"executor": {"max_turns": 5}}), config_path)
  manager = ConfigManager.load(config_path)
  orchestrator = Orchestrator(config=manager.get(), config_manager=manager)
  orchestrator._initialized = True
  orchestrator._session_service._current_id = "session-1"
  agent = SimpleNamespace(max_turns=100)
  factory = MagicMock()
  factory.get_agent.return_value = agent
  orchestrator._agent_factory = factory
  runner_service = MagicMock(spec=RunnerService)
  runner_service.refresh_persisted_session_limits = AsyncMock(return_value=True)
  orchestrator._runner_service = runner_service
  monkeypatch.chdir(tmp_path)

  ConfigLoader.save(CLIConfig(model="after", agents={"executor": {"max_turns": 100}}), config_path)

  await orchestrator.reload_config()

  runner_service.refresh_persisted_session_limits.assert_awaited_once_with(
    session_id="session-1",
    agent=agent,
  )
