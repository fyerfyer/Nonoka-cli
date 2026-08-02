from __future__ import annotations

import argparse
import json

from nonoka_cli.commands.plugin_cmd import run_init, run_validate


def test_init_creates_lightweight_plugin_manifest(tmp_path, capsys) -> None:
  manifest = tmp_path / ".nonoka" / "plugin.json"

  exit_code = run_init(
    argparse.Namespace(
      manifest=str(manifest),
      name="demo-tools",
      description="Small demo plugin.",
      force=False,
    )
  )

  assert exit_code == 0
  assert json.loads(manifest.read_text(encoding="utf-8")) == {
    "schema_version": "1.0",
    "name": "demo-tools",
    "description": "Small demo plugin.",
  }
  assert "load automatically after /reload" in capsys.readouterr().out


def test_validate_project_agents_success(tmp_path, capsys) -> None:
  manifest = tmp_path / "plugin.json"
  manifest.write_text(
    json.dumps(
      {
        "agents": [
          {
            "name": "reviewer",
            "model": "child-model",
            "system_prompt": "Review the supplied evidence.",
            "max_turns": 2,
            "max_invocations": 1,
          }
        ]
      }
    ),
    encoding="utf-8",
  )

  exit_code = run_validate(argparse.Namespace(manifest=str(manifest)))

  assert exit_code == 0
  assert "OK [reviewer]: agent__reviewer" in capsys.readouterr().out


def test_validate_project_agents_returns_error_for_invalid_role(tmp_path, capsys) -> None:
  manifest = tmp_path / "plugin.json"
  manifest.write_text(
    json.dumps(
      {
        "agents": [
          {
            "name": "bad role",
            "model": "",
            "system_prompt": "Review the supplied evidence.",
          }
        ]
      }
    ),
    encoding="utf-8",
  )

  exit_code = run_validate(argparse.Namespace(manifest=str(manifest)))

  assert exit_code == 1
  output = capsys.readouterr().out
  assert "ERROR [bad role]" in output
  assert "agent__bad role" not in output
