from __future__ import annotations

import pytest

from nonoka_cli.config.models import CLIConfig
from nonoka_cli.safety.preflight import require_sandbox


@pytest.mark.asyncio
async def test_required_disabled_sandbox_is_a_hard_preflight_failure(tmp_path):
  config = CLIConfig()
  config.safety.sandbox = "disabled"
  with pytest.raises(RuntimeError, match="sandbox is disabled"):
    await require_sandbox(config.safety, tmp_path)
