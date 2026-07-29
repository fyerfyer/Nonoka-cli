"""Shell environment helpers for official SWE-bench instance images."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath


def _safe_testbed_path(value: str) -> str | None:
  candidate = value.split("::", 1)[0].removeprefix("a/").removeprefix("b/")
  path = PurePosixPath(candidate)
  if not candidate or path.is_absolute() or ".." in path.parts:
    return None
  return f"/testbed/{path}"


def protected_test_paths(instance: Mapping[str, object]) -> list[str]:
  """Return benchmark-owned test paths declared by an SWE-bench instance."""
  paths = {"/testbed/tests"}
  test_patch = instance.get("test_patch")
  if isinstance(test_patch, str):
    for match in re.finditer(r"^diff --git a/(.+?) b/(.+?)$", test_patch, re.MULTILINE):
      for value in match.groups():
        resolved = _safe_testbed_path(value)
        if resolved:
          paths.add(resolved)
  for field in ("FAIL_TO_PASS", "PASS_TO_PASS"):
    values = instance.get(field)
    if isinstance(values, str):
      try:
        values = json.loads(values)
      except json.JSONDecodeError:
        values = [values]
    if isinstance(values, list):
      for value in values:
        if isinstance(value, str):
          resolved = _safe_testbed_path(value)
          if resolved:
            paths.add(resolved)
  return sorted(paths)


def swe_profile(model: str, temperature: float, run_timeout: float) -> dict:
  """Return the OpenCode profile used inside an official SWE-bench image."""
  return {
    "$schema": "https://opencode.ai/config.json",
    "autoupdate": False,
    "model": "nonoka/default",
    "provider": {
      "nonoka": {
        "npm": "file:/opt/nonoka-provider",
        "name": "Nonoka SWE-bench bridge",
        "options": {
          "serverCommand": [
            "/opt/nonoka-runtime/venv/bin/python",
            "-Es",
            "-m",
            "nonoka_cli",
            "--server",
          ],
          "configPath": "/opt/nonoka-runtime/nonoka-benchmark.yaml",
          "model": model,
          "temperature": temperature,
          "wallTimeoutSeconds": run_timeout,
          "maxContextBytes": 256 * 1024,
          "maxExternalResultBytes": 64 * 1024,
          "requireObservedEffect": True,
          "requireFocusedVerification": True,
          "verificationEnforcement": "strict",
          "maxCompletionCorrections": 3,
          "allowedVerificationKinds": ["test", "build", "lint", "typecheck"],
        },
        "models": {"default": {"name": f"Nonoka {model}"}},
      }
    },
    "permission": "allow",
    "agent": {
      "build": {
        "permission": {"skill": "deny", "task": "deny"},
        "tools": {"skill": False, "task": False},
      }
    },
  }


TESTBED_SHELL_BOOTSTRAP = (
  'source /opt/miniconda3/etc/profile.d/conda.sh; conda activate testbed; exec "$@"'
)


def build_testbed_exec_command(
  *,
  container_id: str,
  environment: Mapping[str, str],
  command: Sequence[str],
) -> list[str]:
  """Build a Docker command whose child inherits the official testbed env.

  The Nonoka runtime and OpenCode executable use absolute paths, so activating
  the project environment affects hosted tools without replacing the bridge's
  Python runtime.
  """
  result = ["docker", "exec"]
  for name, value in environment.items():
    result.extend(["--env", f"{name}={value}"])
  result.extend(
    [
      container_id,
      "bash",
      "-lc",
      TESTBED_SHELL_BOOTSTRAP,
      "nonoka-testbed",
      *command,
    ]
  )
  return result
