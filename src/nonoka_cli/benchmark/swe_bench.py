"""SWE-bench Lite orchestration and result diagnosis.

The official SWE-bench verifier remains the scoring authority.  This module
owns only the bridge-run artifact contract and deliberately keeps Docker and
model work behind an explicit preflight check.
"""
# ruff: noqa: E501

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SWE_BENCH_LITE = "SWE-bench/SWE-bench_Lite"
MIN_FREE_BYTES = 120 * 1024**3
MIN_MEMORY_BYTES = 16 * 1024**3


@dataclass(frozen=True)
class Preflight:
  swebench: bool
  docker: bool
  free_bytes: int
  memory_bytes: int
  full_run: bool

  @property
  def ready(self) -> bool:
    return (
      self.swebench
      and self.docker
      and (
        not self.full_run
        or (self.free_bytes >= MIN_FREE_BYTES and self.memory_bytes >= MIN_MEMORY_BYTES)
      )
    )

  def problems(self) -> list[str]:
    issues: list[str] = []
    if not self.swebench:
      issues.append(
        "SWE-bench is not installed; install the official swebench package in a dedicated environment."
      )
    if not self.docker:
      issues.append("Docker daemon is unavailable.")
    if self.full_run and self.free_bytes < MIN_FREE_BYTES:
      issues.append(
        f"SWE-bench Lite requires at least 120 GiB free disk; found {self.free_bytes // 1024**3} GiB."
      )
    if self.full_run and self.memory_bytes < MIN_MEMORY_BYTES:
      issues.append(
        f"SWE-bench Lite requires at least 16 GiB RAM; found {self.memory_bytes // 1024**3} GiB."
      )
    return issues


def _memory_bytes() -> int:
  try:
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
  except (AttributeError, OSError, ValueError):
    return 0


def check_preflight(workspace: Path, *, full_run: bool, python: str | None = None) -> Preflight:
  interpreter = python or os.environ.get("NONOKA_SWEBENCH_PYTHON") or sys.executable
  try:
    swebench_ready = (
      subprocess.run(
        [interpreter, "-c", "import swebench"],
        capture_output=True,
        check=False,
        timeout=15,
      ).returncode
      == 0
    )
  except (OSError, subprocess.TimeoutExpired):
    swebench_ready = False
  docker = shutil.which("docker")
  docker_ready = False
  if docker:
    try:
      docker_ready = (
        subprocess.run([docker, "info"], capture_output=True, check=False, timeout=15).returncode
        == 0
      )
    except (OSError, subprocess.TimeoutExpired):
      pass
  return Preflight(
    swebench=swebench_ready,
    docker=docker_ready,
    free_bytes=shutil.disk_usage(workspace).free,
    memory_bytes=_memory_bytes(),
    full_run=full_run,
  )


def prediction(instance_id: str, model: str, patch: str) -> dict[str, str]:
  """Return the official prediction wire shape without vendor metadata."""
  return {"instance_id": instance_id, "model_name_or_path": model, "model_patch": patch}


def write_prediction(path: Path, value: dict[str, str]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  with path.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(value, sort_keys=True) + "\n")


def _official_reports(directory: Path) -> dict[str, dict[str, Any]]:
  reports: dict[str, dict[str, Any]] = {}
  for path in directory.rglob("report.json"):
    try:
      payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
      continue
    if not isinstance(payload, dict):
      continue
    for instance_id, report in payload.items():
      if isinstance(instance_id, str) and isinstance(report, dict):
        reports[instance_id] = report
  return reports


def _prediction_patches(path: Path) -> dict[str, str]:
  patches: dict[str, str] = {}
  for line in path.read_text(encoding="utf-8").splitlines():
    try:
      value = json.loads(line)
    except json.JSONDecodeError:
      continue
    if isinstance(value, dict) and isinstance(value.get("instance_id"), str):
      patch = value.get("model_patch")
      patches[value["instance_id"]] = patch if isinstance(patch, str) else ""
  return patches


def classify_instance(
  instance_dir: Path,
  verifier_returncode: int | None,
  *,
  instance_id: str | None = None,
  official_report: dict[str, Any] | None = None,
  model_patch: str | None = None,
) -> dict[str, Any]:
  """Classify an observed failure without inferring benchmark-specific causes."""

  def contains(path: Path, terms: tuple[str, ...]) -> bool:
    if not path.is_file():
      return False
    text = path.read_text(encoding="utf-8", errors="replace").lower()
    return any(term in text for term in terms)

  logs = list(instance_dir.rglob("*.log")) + list(instance_dir.rglob("*.ndjson"))
  if instance_id:
    logs = [path for path in logs if instance_id in str(path)]
  resolved = official_report.get("resolved") if official_report else None
  if resolved is True:
    category = "verified_pass"
  elif official_report is not None and official_report.get("patch_successfully_applied") is True:
    category = "unresolved_patch"
  elif official_report is not None:
    category = "official_verifier"
  elif any(
    contains(
      path,
      ("protocol error", "tool_call_id", "bridge server failed", "provider failed"),
    )
    for path in logs
  ):
    category = "cli_provider_protocol"
  elif any(
    contains(path, ("docker", "pull access", "no space left", "network is unreachable"))
    for path in logs
  ):
    category = "infrastructure"
  elif any(
    contains(path, ("watchdog_timeout", "turn_budget", "tool_budget", "no progress"))
    for path in logs
  ):
    category = "agent_loop"
  elif model_patch is not None and not model_patch.strip():
    category = "empty_patch"
  elif verifier_returncode is not None:
    category = "official_verifier" if verifier_returncode else "missing_official_report"
  else:
    category = "completed"
  result = {
    "category": category,
    "verifier_returncode": verifier_returncode,
    "evidence": [str(path.relative_to(instance_dir)) for path in logs[:20]],
  }
  if official_report is not None:
    result.update(
      resolved=resolved,
      patch_exists=official_report.get("patch_exists"),
      patch_successfully_applied=official_report.get("patch_successfully_applied"),
    )
  return result


def write_diagnosis(directory: Path, instances: dict[str, dict[str, Any]]) -> None:
  payload = {"schema_version": 1, "instances": instances}
  (directory / "diagnosis.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
  )
  rows = [
    "# SWE-bench Diagnosis",
    "",
    "| Instance | Classification | Verifier |",
    "| --- | --- | --- |",
  ]
  for instance_id, result in sorted(instances.items()):
    rows.append(f"| {instance_id} | {result['category']} | {result['verifier_returncode']} |")
  rows.extend(
    [
      "",
      "For a rejected but healthy bridge run, reproduce the same instance explicitly with Aider or native OpenCode before attributing the result to Nonoka.",
      "",
    ]
  )
  (directory / "diagnosis.md").write_text("\n".join(rows), encoding="utf-8")


def official_verify(
  *,
  predictions: Path,
  artifact_dir: Path,
  instance_ids: list[str],
  python: str,
  dataset: str,
  max_workers: int,
) -> int:
  """Invoke the installed official verifier and retain its unredacted local output."""
  command = [
    python,
    "-m",
    "swebench.harness.run_evaluation",
    "--dataset_name",
    dataset,
    "--predictions_path",
    str(predictions),
    "--run_id",
    artifact_dir.name,
    "--max_workers",
    str(max_workers),
  ]
  if instance_ids:
    command.extend(["--instance_ids", *instance_ids])
  result = subprocess.run(command, cwd=artifact_dir, capture_output=True, text=True, check=False)
  (artifact_dir / "verifier.stdout.log").write_text(result.stdout, encoding="utf-8")
  (artifact_dir / "verifier.stderr.log").write_text(result.stderr, encoding="utf-8")
  (artifact_dir / "verifier-command.json").write_text(
    json.dumps(command, indent=2) + "\n", encoding="utf-8"
  )
  return result.returncode


def run(args: Any, *, common_env: dict[str, str], redact: Any) -> int:
  if args.artifact_dir:
    directory = Path(args.artifact_dir).expanduser().resolve()
  else:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = Path(args.cwd).resolve() / ".nonoka" / "eval" / "swe-bench" / stamp
  directory.mkdir(parents=True, exist_ok=True)
  instance_ids = list(args.instance_ids or [])
  swebench_python = (
    getattr(args, "swebench_python", None)
    or os.environ.get("NONOKA_SWEBENCH_PYTHON")
    or sys.executable
  )
  interpreter = Path(swebench_python).expanduser()
  if not interpreter.is_absolute():
    interpreter = Path.cwd() / interpreter
  swebench_python = os.path.abspath(interpreter)
  preflight = check_preflight(
    Path(args.cwd).resolve(), full_run=not instance_ids, python=swebench_python
  )
  (directory / "preflight.json").write_text(
    json.dumps(asdict(preflight), indent=2) + "\n", encoding="utf-8"
  )
  if not preflight.ready:
    for problem in preflight.problems():
      print(f"Error: {problem}", file=sys.stderr)
    return 2
  if not args.predictions:
    print(
      "Error: --predictions is required for official verification. Generate patches with the container bridge runner before scoring.",
      file=sys.stderr,
    )
    return 2
  predictions = Path(args.predictions).expanduser().resolve()
  if not predictions.is_file():
    print(f"Error: predictions file does not exist: {predictions}", file=sys.stderr)
    return 2
  manifest = {
    "schema_version": 1,
    "benchmark": "SWE-bench_Lite",
    "surface": "opencode-nonoka",
    "model": args.model,
    "instance_ids": instance_ids,
    "predictions": str(predictions),
    "dataset": args.dataset_path or SWE_BENCH_LITE,
    "preflight": asdict(preflight),
    "credential_policy": "Credentials are supplied only through environment variables.",
  }
  (directory / "manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
  )
  if args.skip_verify:
    return 0
  code = official_verify(
    predictions=predictions,
    artifact_dir=directory,
    instance_ids=instance_ids,
    python=swebench_python,
    dataset=args.dataset_path or SWE_BENCH_LITE,
    max_workers=args.max_workers,
  )
  reports = _official_reports(directory)
  patches = _prediction_patches(predictions)
  diagnosis = {
    instance_id or "all": classify_instance(
      directory,
      code,
      instance_id=instance_id or None,
      official_report=reports.get(instance_id),
      model_patch=patches.get(instance_id),
    )
    for instance_id in instance_ids or [""]
  }
  write_diagnosis(directory, diagnosis)
  resolved = sum(result["category"] == "verified_pass" for result in diagnosis.values())
  (directory / "result.json").write_text(
    json.dumps(
      {
        "returncode": code,
        "official_verifier": True,
        "resolved": resolved,
        "evaluated": len(diagnosis),
      },
      indent=2,
    )
    + "\n",
    encoding="utf-8",
  )
  print(f"Artifacts: {directory}")
  return code
