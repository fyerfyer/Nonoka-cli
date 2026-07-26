"""Docker-backed shell runner with a minimal host exposure surface."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path


class DockerSandbox:
  def __init__(self, image: str = "alpine:3.20") -> None:
    self.image = image

  async def run(self, command: str, workspace: Path, timeout: int) -> tuple[int, str]:
    """Execute in an isolated container; the host Docker socket is never mounted."""
    root = workspace.resolve()
    args = [
      "docker", "run", "--rm", "--network", "none", "--read-only",
      "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
      "--pids-limit", "128", "--memory", "512m", "--cpus", "1",
      "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
      "--user", "65534:65534", "-v", f"{root}:/workspace:rw",
      "-w", "/workspace", self.image, "sh", "-lc", command,
    ]
    proc = await asyncio.create_subprocess_exec(
      *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
      output, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
      proc.kill()
      await proc.wait()
      return 124, f"Error: sandbox command timed out after {timeout}s"
    return proc.returncode, output.decode("utf-8", errors="replace")


class SrtSandbox:
  """Adapter for Anthropic Sandbox Runtime's process-tree sandbox."""

  def __init__(self, allowed_domains: list[str] | None = None) -> None:
    self.allowed_domains = allowed_domains or []

  @staticmethod
  def executable() -> str | None:
    return shutil.which("srt") or shutil.which("node_modules/.bin/srt")

  def settings(self, workspace: Path) -> Path:
    fd, raw = tempfile.mkstemp(prefix="nonoka-srt-", suffix=".json")
    os.close(fd)
    path = Path(raw)
    credential_names = [
      name for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY")
      if os.environ.get(name)
    ]
    network: dict[str, object] = {"allowedDomains": self.allowed_domains, "deniedDomains": []}
    opencode_runtime = Path.home() / ".local" / "share" / "opencode"
    nonoka_runtime = Path.home() / ".local" / "share" / "nonoka"
    payload: dict[str, object] = {
      "filesystem": {"denyRead": [str(Path.home() / ".ssh")], "allowRead": [str(workspace)], "allowWrite": [str(workspace), "/tmp", str(opencode_runtime), str(nonoka_runtime)], "denyWrite": [str(workspace / ".env"), str(workspace / ".git/hooks")]},
      "network": network,
    }
    if credential_names and self.allowed_domains:
      # SRT replaces these env values with sentinels inside the process tree
      # and substitutes the real value only on the TLS-terminated allowlist.
      network["tlsTerminate"] = {}
      payload["credentials"] = {
        "envVars": [
          {"name": name, "mode": "mask", "injectHosts": self.allowed_domains}
          for name in credential_names
        ],
      }
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, 0o600)
    return path

  async def run(self, command: str, workspace: Path, timeout: int) -> tuple[int, str]:
    srt = self.executable()
    if not srt:
      raise RuntimeError("SRT is not installed; install @anthropic-ai/sandbox-runtime")
    settings = self.settings(workspace.resolve())
    try:
      proc = await asyncio.create_subprocess_exec(
        srt, "--settings", str(settings), "sh", "-lc", command,
        cwd=workspace, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
      )
      try:
        output, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
      except asyncio.TimeoutError:
        proc.kill(); await proc.wait()
        return 124, f"Error: sandbox command timed out after {timeout}s"
      return proc.returncode, output.decode("utf-8", errors="replace")
    finally:
      settings.unlink(missing_ok=True)
