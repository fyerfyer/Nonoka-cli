from __future__ import annotations

import pytest

from nonoka_cli.config.models import CLIConfig
from nonoka_cli.safety.preflight import PROCESS_SANDBOX_ENV, inspect_sandbox, require_sandbox


@pytest.mark.asyncio
async def test_required_disabled_sandbox_is_a_hard_preflight_failure(tmp_path):
  config = CLIConfig()
  config.safety.sandbox = "disabled"
  with pytest.raises(RuntimeError, match="sandbox is disabled"):
    await require_sandbox(config.safety, tmp_path)


@pytest.mark.asyncio
async def test_outer_process_sandbox_satisfies_bridge_preflight(tmp_path, monkeypatch):
  config = CLIConfig()
  config.safety.sandbox = "auto"
  monkeypatch.setenv(PROCESS_SANDBOX_ENV, "srt")

  result = await inspect_sandbox(config.safety, tmp_path)
  await require_sandbox(config.safety, tmp_path)

  assert result.status == "ok"
  assert "already owns this process tree" in result.message
