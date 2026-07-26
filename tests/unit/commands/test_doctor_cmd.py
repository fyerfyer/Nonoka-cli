"""Tests for the nonoka-cli doctor command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from nonoka_cli.commands.doctor_cmd import (
  _api_key_env_for_model,
  check_api_key,
  check_config,
  check_docker,
  check_harbor,
  check_nonoka_cli_version,
  check_opencode,
  check_opencode_config,
  check_provider,
  check_python_version,
  check_sandbox,
  run_doctor,
)
from nonoka_cli.config.loader import ConfigLoader
from nonoka_cli.config.models import CLIConfig


class TestApiKeyEnvForModel:
  def test_openai_models(self):
    assert _api_key_env_for_model("openai/gpt-4o") == "OPENAI_API_KEY"
    assert _api_key_env_for_model("gpt-4o") == "OPENAI_API_KEY"

  def test_anthropic_models(self):
    assert _api_key_env_for_model("anthropic/claude-sonnet") == "ANTHROPIC_API_KEY"

  def test_deepseek_models(self):
    assert _api_key_env_for_model("deepseek-chat") == "DEEPSEEK_API_KEY"

  def test_fallback(self):
    assert _api_key_env_for_model("ollama/llama3") == "OPENAI_API_KEY"


class TestCheckPythonVersion:
  def test_passes_on_supported_python(self):
    result = check_python_version()
    assert result.status == "ok"
    assert "Python" in result.message


class TestCheckNonokaCliVersion:
  def test_returns_version(self):
    result = check_nonoka_cli_version()
    assert result.status == "ok"
    assert "nonoka-cli" in result.message


class TestCheckConfig:
  def test_missing_config(self, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ConfigLoader, "DEFAULT_PATH", tmp_path / "config.yaml")
    monkeypatch.chdir(tmp_path)
    result, cfg = check_config(None)
    assert result.status == "error"
    assert cfg is None

  def test_valid_config(self, tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    ConfigLoader.save(CLIConfig(model="deepseek-chat"), config_path)
    result, cfg = check_config(str(config_path))
    assert result.status == "ok"
    assert cfg is not None
    assert cfg.model == "deepseek-chat"


class TestCheckApiKey:
  def test_key_set(self, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    cfg = CLIConfig(model="deepseek-chat")
    result = check_api_key(cfg)
    assert result.status == "ok"
    assert "DEEPSEEK_API_KEY" in result.message

  def test_key_missing(self, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    cfg = CLIConfig(model="deepseek-chat")
    result = check_api_key(cfg)
    assert result.status == "error"
    assert "DEEPSEEK_API_KEY" in result.message

  def test_no_config(self):
    result = check_api_key(None)
    assert result.status == "warn"


class TestCheckOpencode:
  def test_missing_opencode(self):
    with mock.patch("nonoka_cli.commands.doctor_cmd.shutil.which", return_value=None):
      result = check_opencode()
    assert result.status == "error"

  def test_opencode_present(self):
    with mock.patch("nonoka_cli.commands.doctor_cmd.shutil.which", return_value="/bin/opencode"):
      with mock.patch(
        "nonoka_cli.commands.doctor_cmd._run",
        return_value=mock.MagicMock(returncode=0, stdout="1.2.3\n"),
      ):
        result = check_opencode()
    assert result.status == "ok"
    assert "1.2.3" in result.message


class TestCheckProvider:
  def test_npm_global_provider(self):
    npm_output = json.dumps({
      "dependencies": {
        "nonoka-opencode-provider": {"version": "0.2.0"}
      }
    })
    with mock.patch(
      "nonoka_cli.commands.doctor_cmd._run",
      return_value=mock.MagicMock(returncode=0, stdout=npm_output),
    ):
      result = check_provider()
    assert result.status == "ok"
    assert "0.2.0" in result.message

  def test_provider_missing(self):
    with mock.patch("nonoka_cli.commands.doctor_cmd.shutil.which", return_value=None):
      result = check_provider()
    assert result.status == "warn"


class TestCheckOpencodeConfig:
  def test_missing_config(self, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    result = check_opencode_config()
    assert result.status == "error"


class TestBenchmarkPrerequisites:
  def test_harbor_missing(self):
    with mock.patch("nonoka_cli.commands.doctor_cmd.shutil.which", return_value=None):
      assert check_harbor().status == "warn"

  def test_docker_daemon_ready(self):
    with mock.patch("nonoka_cli.commands.doctor_cmd.shutil.which", return_value="/bin/docker"):
      with mock.patch(
        "nonoka_cli.commands.doctor_cmd._run",
        return_value=mock.MagicMock(returncode=0, stdout="29.0.2\n"),
      ):
        assert check_docker().status == "ok"

  def test_sandbox_runs_smoke_command(self):
    async def run(*args, **kwargs):
      return 0, "sandbox-ok"
    with mock.patch(
      "nonoka_cli.commands.doctor_cmd.check_docker",
      return_value=mock.MagicMock(status="ok"),
    ), mock.patch("nonoka_cli.safety.DockerSandbox.run", side_effect=run):
      assert check_sandbox().status == "ok"

  def test_srt_sandbox_runs_when_configured(self, monkeypatch):
    async def run(*args, **kwargs):
      return 0, "sandbox-ok"
    config = CLIConfig(model="deepseek-chat")
    config.safety.sandbox = "srt"
    with mock.patch("nonoka_cli.safety.SrtSandbox.executable", return_value="/bin/srt"), mock.patch(
      "nonoka_cli.safety.SrtSandbox.run", side_effect=run,
    ):
      result = check_sandbox(config)
    assert result.status == "ok"
    assert result.message.startswith("SRT sandbox")

  def test_valid_global_config(self, tmp_path: Path, monkeypatch):
    config_dir = tmp_path / ".config" / "opencode"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "opencode.json"
    config_path.write_text(json.dumps({
      "model": "nonoka/default",
      "provider": {"nonoka": {"npm": "nonoka-opencode-provider"}}
    }))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Ensure cwd doesn't also have an opencode.json
    monkeypatch.chdir(tmp_path)
    result = check_opencode_config()
    assert result.status == "ok"

  def test_config_without_nonoka_provider(self, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    config_path = tmp_path / "opencode.json"
    config_path.write_text(json.dumps({"model": "gpt-4o"}))
    result = check_opencode_config()
    assert result.status == "error"


class TestRunDoctor:
  def test_all_pass(self, tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    ConfigLoader.save(CLIConfig(model="deepseek-chat"), config_path)
    opencode_path = tmp_path / "opencode.json"
    opencode_path.write_text(json.dumps({
      "provider": {"nonoka": {"npm": "nonoka-opencode-provider"}}
    }))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    monkeypatch.chdir(tmp_path)

    args = mock.MagicMock(config=str(config_path), check_llm=False)
    with mock.patch("nonoka_cli.commands.doctor_cmd.shutil.which") as which_mock:
      which_mock.side_effect = lambda cmd: "/bin/" + cmd if cmd in ("opencode", "npm") else None
      with mock.patch(
        "nonoka_cli.commands.doctor_cmd._run",
        return_value=mock.MagicMock(returncode=0, stdout=json.dumps({
          "dependencies": {"nonoka-opencode-provider": {"version": "0.2.0"}}
        })),
      ):
        with mock.patch(
          "nonoka_cli.commands.doctor_cmd.importlib.metadata.version",
          return_value="0.2.1",
        ):
          code = run_doctor(args)

    assert code == 0

  def test_failure_when_key_missing(self, tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    ConfigLoader.save(CLIConfig(model="deepseek-chat"), config_path)
    opencode_path = tmp_path / "opencode.json"
    opencode_path.write_text(json.dumps({
      "provider": {"nonoka": {"npm": "nonoka-opencode-provider"}}
    }))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    args = mock.MagicMock(config=str(config_path), check_llm=False)
    with mock.patch("nonoka_cli.commands.doctor_cmd.shutil.which") as which_mock:
      which_mock.side_effect = lambda cmd: "/bin/" + cmd if cmd in ("opencode", "npm") else None
      with mock.patch(
        "nonoka_cli.commands.doctor_cmd._run",
        return_value=mock.MagicMock(returncode=0, stdout=json.dumps({
          "dependencies": {"nonoka-opencode-provider": {"version": "0.2.0"}}
        })),
      ):
        with mock.patch(
          "nonoka_cli.commands.doctor_cmd.importlib.metadata.version",
          return_value="0.2.1",
        ):
          code = run_doctor(args)

    assert code == 1
