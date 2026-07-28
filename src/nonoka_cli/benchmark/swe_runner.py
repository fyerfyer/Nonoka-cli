"""Run OpenCode/Nonoka inside official SWE-bench instance images."""
# ruff: noqa: E501

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import os
import shlex
import subprocess
from pathlib import Path

import docker
from swebench.harness.docker_build import build_container
from swebench.harness.docker_utils import cleanup_container
from swebench.harness.test_spec.test_spec import make_test_spec
from swebench.harness.utils import load_swebench_dataset

from nonoka_cli.benchmark.harbor import _BENCHMARK_SYSTEM_PROMPT
from nonoka_cli.benchmark.swe_bench import prediction, write_prediction


def _encoded(value: object) -> str:
  raw = json.dumps(value, sort_keys=True).encode()
  return base64.b64encode(raw).decode()


def _docker_cp(source: Path | str, target: str) -> None:
  result = subprocess.run(["docker", "cp", str(source), target], capture_output=True, text=True)
  if result.returncode:
    raise RuntimeError(result.stderr.strip() or f"docker cp failed: {source}")


def _profile(model: str, temperature: float, run_timeout: float) -> dict:
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


def _setup(
  container, runtime_root: Path, model: str, temperature: float, run_timeout: float
) -> None:
  container.exec_run(
    "mkdir -p /opt/nonoka-host /opt/nonoka-runtime /opt/nonoka-provider /root/.config/opencode /logs/agent",
    user="root",
  )
  _docker_cp(f"{runtime_root}/.", f"{container.id}:/opt/nonoka-host")
  config = {
    "model": model,
    "system_prompt": _BENCHMARK_SYSTEM_PROMPT,
    "cli": {"auto_approve": True},
  }
  command = (
    "set -euo pipefail; "
    "cp /opt/nonoka-host/runtime-uv/uv /opt/nonoka-runtime/uv; "
    "cp /opt/nonoka-host/runtime-opencode/opencode /opt/nonoka-runtime/opencode; "
    "cp /opt/nonoka-host/runtime-tools/rg /usr/local/bin/rg; "
    "cp -a /opt/nonoka-host/runtime-provider/. /opt/nonoka-provider/; "
    "chmod +x /opt/nonoka-runtime/uv /opt/nonoka-runtime/opencode /usr/local/bin/rg; "
    "mkdir -p /opt/nonoka-runtime/python-host; "
    "tar -xzf /opt/nonoka-host/runtime-python/python-3.13.tar.gz -C /opt/nonoka-runtime/python-host --strip-components=1; "
    "/opt/nonoka-runtime/uv venv /opt/nonoka-runtime/venv --python /opt/nonoka-runtime/python-host/bin/python3.13; "
    "tar -xzf /opt/nonoka-host/runtime-site-packages/site-packages.tar.gz -C /opt/nonoka-runtime/venv; "
    f"printf '%s' {_encoded(config)} | base64 -d > /opt/nonoka-runtime/nonoka-benchmark.yaml; "
    f"printf '%s' {_encoded(_profile(model, temperature, run_timeout))} | base64 -d > /root/.config/opencode/opencode.json; "
    "/opt/nonoka-runtime/venv/bin/python -Es -c 'import nonoka, nonoka_cli'; "
    "rg --version; "
    "/opt/nonoka-runtime/opencode --version"
  )
  result = container.exec_run(["bash", "-lc", command], user="root")
  if result.exit_code:
    raise RuntimeError(result.output.decode(errors="replace")[-4000:])


def _run_one(instance: dict, args: argparse.Namespace, runtime_root: Path, output: Path) -> dict:
  client = docker.from_env()
  spec = make_test_spec(instance, namespace="swebench")
  instance_dir = output / "instances" / spec.instance_id
  instance_dir.mkdir(parents=True, exist_ok=True)
  logger = __import__("logging").getLogger(f"swebench.{spec.instance_id}")
  container = build_container(spec, client, args.run_id, logger, False)
  status = 1
  patch = ""
  try:
    container.start()
    _setup(container, runtime_root, args.model, args.temperature, args.run_timeout)
    instruction = instance["problem_statement"]
    env = {
      key: value
      for key, value in os.environ.items()
      if key.endswith("API_KEY") or key in {"OPENAI_BASE_URL"}
    }
    env.update(
      {
        "OPENCODE_FAKE_VCS": "git",
        "NONOKA_PROVIDER_LOG_PATH": "/logs/agent/provider.log",
        "NONOKA_LOG_FILE": "/logs/agent/bridge-server.log",
        "NONOKA_TRACE_DIR": "/logs/agent/bridge-events",
        "NONOKA_TIMELINE_PATH": "/logs/agent/bridge-timeline.ndjson",
        "NONOKA_RUN_EVIDENCE_PATH": "/logs/agent/run-evidence.ndjson",
      }
    )
    env_args = " ".join(f"--env {shlex.quote(k + '=' + v)}" for k, v in env.items())
    command = (
      f"docker exec {env_args} {container.id} /opt/nonoka-runtime/venv/bin/python -Es "
      f"-m nonoka_cli.benchmark.watchdog --timeout {args.run_timeout} --grace 5 "
      "--log /logs/agent/opencode.txt --evidence-log /logs/agent/run-evidence.ndjson "
      "--artifact-dir /logs/artifacts/agent --allow-scorable-budget-exit -- "
      "/opt/nonoka-runtime/opencode --model=nonoka/default run --format=json --thinking "
      f"-- {shlex.quote(instruction)}"
    )
    result = subprocess.run(["bash", "-lc", command], capture_output=True, text=True)
    (instance_dir / "launcher.stdout.log").write_text(result.stdout)
    (instance_dir / "launcher.stderr.log").write_text(result.stderr)
    status = result.returncode
    diff = container.exec_run("git -c core.fileMode=false diff --binary", workdir="/testbed")
    patch = diff.output.decode(errors="replace")
    (instance_dir / "model.patch").write_text(patch)
    _docker_cp(f"{container.id}:/logs/.", instance_dir)
  except Exception as exc:
    (instance_dir / "runner-error.log").write_text(f"{type(exc).__name__}: {exc}\n")
  finally:
    cleanup_container(client, container, logger)
  return {"prediction": prediction(spec.instance_id, args.model, patch), "returncode": status}


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--runtime-root", type=Path, required=True)
  parser.add_argument("--output", type=Path, required=True)
  parser.add_argument("--model", required=True)
  parser.add_argument("--temperature", type=float, default=0.0)
  parser.add_argument("--run-timeout", type=float, default=3600)
  parser.add_argument("--max-workers", type=int, default=1)
  parser.add_argument("--run-id", required=True)
  parser.add_argument("--instance-id", action="append", required=True)
  parser.add_argument("--dataset", required=True)
  args = parser.parse_args(argv)
  dataset = load_swebench_dataset(args.dataset, "test", args.instance_id)
  predictions = args.output / "predictions.jsonl"
  predictions.unlink(missing_ok=True)
  with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as pool:
    futures = [
      pool.submit(_run_one, item, args, args.runtime_root.resolve(), args.output)
      for item in dataset
    ]
    for future in concurrent.futures.as_completed(futures):
      result = future.result()
      write_prediction(predictions, result["prediction"])
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
