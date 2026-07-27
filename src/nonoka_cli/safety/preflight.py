"""Shared sandbox availability checks for doctor and required bridge startup."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nonoka_cli.safety.sandbox import DockerSandbox, SrtSandbox


@dataclass(frozen=True)
class SandboxPreflight:
  status: str
  message: str
  remedy: str = ""


async def inspect_sandbox(safety: Any, workspace: Path) -> SandboxPreflight:
  """Run a harmless command through the configured backend."""
  selected = str(getattr(safety, "sandbox", "docker"))
  if selected == "disabled":
    return SandboxPreflight("warn", "sandbox is disabled by configuration")
  if selected == "auto":
    selected = "srt" if SrtSandbox.executable() else "docker"

  if selected == "docker":
    if not shutil.which("docker"):
      return SandboxPreflight(
        "error", "Docker sandbox unavailable", "Install Docker and start its daemon."
      )
    name = "Docker"
    backend = DockerSandbox()
  elif selected == "srt":
    if not SrtSandbox.executable():
      return SandboxPreflight(
        "error",
        "SRT sandbox unavailable",
        "Install @anthropic-ai/sandbox-runtime and ensure `srt` is on PATH.",
      )
    name = "SRT"
    backend = SrtSandbox(list(getattr(safety, "allowed_domains", ()) or ()))
  else:
    return SandboxPreflight("error", f"Unknown sandbox backend: {selected}")

  try:
    code, output = await backend.run("printf sandbox-ok", workspace, 15)
  except Exception as exc:
    return SandboxPreflight("error", f"{name} sandbox smoke test failed: {exc}")
  if code == 0 and output == "sandbox-ok":
    return SandboxPreflight("ok", f"{name} sandbox executed an isolated smoke test")
  return SandboxPreflight(
    "error", f"{name} sandbox smoke test failed (exit {code})", output.strip()[:300]
  )


async def require_sandbox(safety: Any, workspace: Path) -> None:
  """Raise when a configuration marks its sandbox as a hard requirement."""
  result = await inspect_sandbox(safety, workspace)
  if result.status != "ok":
    raise RuntimeError(result.message + (f": {result.remedy}" if result.remedy else ""))
