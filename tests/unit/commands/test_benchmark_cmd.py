"""Tests for reproducible OpenCode bridge benchmark commands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest import mock

from nonoka_cli.commands.benchmark_cmd import (
    TERMINAL_BENCH_TASKS,
    _harbor_env,
    _redact_text,
    cmd_smoke,
    cmd_terminal_bench,
)


def _args(tmp_path: Path, **values: object) -> argparse.Namespace:
    defaults: dict[str, object] = {
        "artifact_dir": str(tmp_path / "artifacts"),
        "cwd": str(tmp_path),
        "config": None,
        "provider_source": None,
        "model": "deepseek-chat",
        "temperature": 0.0,
        "max_turns": 24,
        "timeout": 180.0,
        "run_timeout": 900.0,
        "tool_budget": 64,
        "mode": "opencode-nonoka",
        "message": "create a file",
        "tasks": None,
        "install_only": False,
    }
    defaults.update(values)
    return argparse.Namespace(**defaults)


def test_smoke_writes_manifest_and_captures_opencode_output(tmp_path: Path, monkeypatch):
    args = _args(tmp_path)
    monkeypatch.setattr("nonoka_cli.commands.benchmark_cmd.shutil.which", lambda _: "/bin/opencode")
    completed = mock.MagicMock(returncode=0, stdout='{"type":"text"}\n', stderr="")
    monkeypatch.setattr(
        "nonoka_cli.commands.benchmark_cmd.subprocess.run", lambda *a, **k: completed
    )
    assert cmd_smoke(args) == 0
    artifact = Path(args.artifact_dir)
    manifest = json.loads((artifact / "manifest.json").read_text())
    assert manifest["mode"] == "opencode-nonoka"
    assert "opencode" in manifest["command"]
    assert (artifact / "opencode.stdout.ndjson").read_text() == '{"type":"text"}\n'
    assert manifest["config"] == str(artifact / "nonoka.benchmark.yaml")
    profile = json.loads((artifact / "opencode.profile.json").read_text())
    options = profile["provider"]["nonoka"]["options"]
    assert profile["provider"]["nonoka"]["npm"].startswith("file:")
    assert options["maxTurns"] == 24
    assert options["toolBudget"] == 64
    assert not (tmp_path / "opencode.json").exists()


def test_terminal_bench_requires_harbor(tmp_path: Path, monkeypatch):
    args = _args(tmp_path)
    monkeypatch.setattr("nonoka_cli.commands.benchmark_cmd.shutil.which", lambda _: None)
    assert cmd_terminal_bench(args) == 2


def test_terminal_bench_pins_the_public_task_slice(tmp_path: Path, monkeypatch):
    args = _args(tmp_path)
    monkeypatch.setattr("nonoka_cli.commands.benchmark_cmd.shutil.which", lambda _: "/bin/tool")
    monkeypatch.setattr(
        "nonoka_cli.commands.benchmark_cmd._prepare_harbor_runtime",
        lambda *_: {
            "cli_wheel": "/tmp/nonoka-cli.whl",
            "agent_wheel": "/tmp/nonoka-agent.whl",
            "provider_source": "/tmp/provider",
            "uv_binary": "/tmp/uv",
            "opencode_binary": "/tmp/opencode",
        },
    )
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(command)
        return mock.MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("nonoka_cli.commands.benchmark_cmd.subprocess.run", run)
    assert cmd_terminal_bench(args) == 0
    for task in TERMINAL_BENCH_TASKS:
        assert task in calls[-1]
    assert "run_timeout_seconds=900.0" in calls[-1]
    assert json.loads((Path(args.artifact_dir) / "manifest.json").read_text())["tasks"] == list(
        TERMINAL_BENCH_TASKS
    )
    assert "DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}" in calls[-1]
    assert "cli_wheel=/tmp/nonoka-cli.whl" in calls[-1]
    assert "uv_binary=/tmp/uv" in calls[-1]
    assert "opencode_binary=/tmp/opencode" in calls[-1]


def test_terminal_bench_can_install_one_pinned_task_without_scoring(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "clean-workspace"
    args = _args(
        tmp_path,
        cwd=str(workspace),
        tasks=["regex-log"],
        install_only=True,
    )
    monkeypatch.setattr("nonoka_cli.commands.benchmark_cmd.shutil.which", lambda _: "/bin/tool")
    monkeypatch.setattr(
        "nonoka_cli.commands.benchmark_cmd._prepare_harbor_runtime",
        lambda *_: {
            "cli_wheel": "/tmp/nonoka-cli.whl",
            "agent_wheel": "/tmp/nonoka-agent.whl",
            "provider_source": "/tmp/provider",
            "uv_binary": "/tmp/uv",
            "opencode_binary": "/tmp/opencode",
        },
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "nonoka_cli.commands.benchmark_cmd.subprocess.run",
        lambda command, **_: calls.append(command)
        or mock.MagicMock(returncode=0, stdout="", stderr=""),
    )

    assert cmd_terminal_bench(args) == 0
    command = calls[-1]
    assert "--install-only" in command
    assert command.count("--include-task-name") == 1
    assert "regex-log" in command
    assert workspace.is_dir()


def test_artifact_redaction_removes_common_api_key_forms():
    assert "sk-secret" not in _redact_text("Authorization=sk-secret-token-123456789")


def test_harbor_env_removes_only_an_incompatible_socks_all_proxy(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "socks://127.0.0.1:7890")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8080")
    env = _harbor_env(tmp_path)
    assert "ALL_PROXY" not in env
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:8080"


def test_smoke_links_an_explicit_local_provider_source(tmp_path: Path, monkeypatch):
    source = tmp_path / "provider"
    (source / "dist").mkdir(parents=True)
    (source / "package.json").write_text("{}")
    args = _args(tmp_path, provider_source=str(source))
    monkeypatch.setattr("nonoka_cli.commands.benchmark_cmd.shutil.which", lambda _: "/bin/opencode")
    monkeypatch.setattr(
        "nonoka_cli.commands.benchmark_cmd.subprocess.run",
        lambda *a, **k: mock.MagicMock(returncode=0, stdout="", stderr=""),
    )
    assert cmd_smoke(args) == 0
    assert (tmp_path / "node_modules" / "nonoka-opencode-provider").resolve() == source


def test_smoke_refuses_to_replace_an_existing_opencode_config(tmp_path: Path, monkeypatch):
    (tmp_path / "opencode.json").write_text('{"model":"other/default"}\n')
    args = _args(tmp_path)
    monkeypatch.setattr("nonoka_cli.commands.benchmark_cmd.shutil.which", lambda _: "/bin/opencode")
    assert cmd_smoke(args) == 2
    assert (tmp_path / "opencode.json").read_text() == '{"model":"other/default"}\n'
