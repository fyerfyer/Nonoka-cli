"""Docker-backed shell runner with a minimal host exposure surface."""

from __future__ import annotations

import asyncio
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
