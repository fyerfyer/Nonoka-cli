from types import SimpleNamespace

from nonoka_cli.config.models import CLIConfig
from nonoka_cli.core.orchestrator import Orchestrator
from nonoka_cli.core.plugin_manifest import PluginManifest


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
