"""Behavior tests for the SWE-bench bridge verifier command."""

from __future__ import annotations

import argparse
import json
import stat
from pathlib import Path
from types import SimpleNamespace

from nonoka_cli.benchmark import swe_bench
from nonoka_cli.cli import _build_parser
from nonoka_cli.commands import benchmark_cmd


def _args(tmp_path: Path, **values: object) -> argparse.Namespace:
  defaults: dict[str, object] = {
    "artifact_dir": str(tmp_path / "artifacts"),
    "cwd": str(tmp_path),
    "model": "provider/model",
    "instance_ids": ["django__django-1"],
    "predictions": str(tmp_path / "predictions.jsonl"),
    "skip_verify": False,
    "swebench_python": None,
    "dataset_path": None,
    "max_workers": 1,
  }
  defaults.update(values)
  return argparse.Namespace(**defaults)


def test_prediction_uses_official_wire_shape():
  assert swe_bench.prediction("instance", "model", "diff") == {
    "instance_id": "instance",
    "model_name_or_path": "model",
    "model_patch": "diff",
  }


def test_swe_bench_parser_accepts_single_instance_and_predictions():
  args = _build_parser().parse_args(
    [
      "benchmark",
      "swe-bench",
      "--instance-id",
      "django__django-1",
      "--predictions",
      "predictions.jsonl",
    ]
  )
  assert args.instance_ids == ["django__django-1"]
  assert args.predictions == "predictions.jsonl"


def test_constrained_single_instance_writes_manifest_without_verification(tmp_path, monkeypatch):
  predictions = tmp_path / "predictions.jsonl"
  prediction = swe_bench.prediction("django__django-1", "provider/model", "")
  predictions.write_text(json.dumps(prediction) + "\n")
  monkeypatch.setattr(
    swe_bench,
    "check_preflight",
    lambda *_args, **_kwargs: swe_bench.Preflight(True, True, 1, 1, False),
  )
  args = _args(tmp_path, skip_verify=True)

  assert swe_bench.run(args, common_env={}, redact=lambda value: value) == 0
  manifest = json.loads((tmp_path / "artifacts" / "manifest.json").read_text())
  assert manifest["benchmark"] == "SWE-bench_Lite"
  assert manifest["surface"] == "opencode-nonoka"


def test_full_run_is_rejected_by_resource_preflight(tmp_path, monkeypatch, capsys):
  monkeypatch.setattr(
    swe_bench,
    "check_preflight",
    lambda *_args, **_kwargs: swe_bench.Preflight(False, False, 1, 1, True),
  )
  args = _args(tmp_path, instance_ids=[])

  assert swe_bench.run(args, common_env={}, redact=lambda value: value) == 2
  assert "requires at least 120 GiB" in capsys.readouterr().err


def test_verifier_output_and_diagnosis_are_persisted(tmp_path, monkeypatch):
  predictions = tmp_path / "predictions.jsonl"
  prediction = swe_bench.prediction("django__django-1", "provider/model", "diff")
  predictions.write_text(json.dumps(prediction) + "\n")
  monkeypatch.setattr(
    swe_bench,
    "check_preflight",
    lambda *_args, **_kwargs: swe_bench.Preflight(True, True, 1, 1, False),
  )
  monkeypatch.setattr(
    swe_bench.subprocess,
    "run",
    lambda *_args, **_kwargs: SimpleNamespace(
      returncode=1, stdout="failed", stderr="verifier rejected"
    ),
  )

  assert swe_bench.run(_args(tmp_path), common_env={}, redact=lambda value: value) == 1
  artifact = tmp_path / "artifacts"
  assert (artifact / "verifier-command.json").is_file()
  diagnosis = json.loads((artifact / "diagnosis.json").read_text())
  assert diagnosis["instances"]["django__django-1"]["category"] == "official_verifier"


def test_protocol_log_takes_precedence_over_verifier_failure(tmp_path):
  (tmp_path / "provider.log").write_text("bridge server protocol error")
  result = swe_bench.classify_instance(tmp_path, 1)
  assert result["category"] == "cli_provider_protocol"


def test_successful_harness_exit_requires_resolved_official_report(tmp_path):
  unresolved = swe_bench.classify_instance(
    tmp_path,
    0,
    official_report={
      "resolved": False,
      "patch_exists": True,
      "patch_successfully_applied": True,
    },
    model_patch="diff",
  )
  missing = swe_bench.classify_instance(tmp_path, 0, model_patch="diff")
  empty = swe_bench.classify_instance(tmp_path, 0, model_patch="")

  assert unresolved["category"] == "unresolved_patch"
  assert missing["category"] == "missing_official_report"
  assert empty["category"] == "empty_patch"


def test_official_test_failure_takes_precedence_over_incidental_protocol_text(tmp_path):
  (tmp_path / "provider.log").write_text("problem statement mentions protocol error")

  result = swe_bench.classify_instance(
    tmp_path,
    0,
    official_report={"resolved": False, "patch_successfully_applied": True},
    model_patch="diff",
  )

  assert result["category"] == "unresolved_patch"


def test_resolved_official_report_is_verified_pass(tmp_path):
  result = swe_bench.classify_instance(
    tmp_path,
    0,
    official_report={
      "resolved": True,
      "patch_exists": True,
      "patch_successfully_applied": True,
    },
    model_patch="diff",
  )

  assert result["category"] == "verified_pass"
  assert result["resolved"] is True


def test_stage_search_binary_copies_executable_rg(tmp_path, monkeypatch):
  source = tmp_path / "host-rg"
  source.write_bytes(b"static ripgrep")
  source.chmod(0o755)
  monkeypatch.setattr(
    benchmark_cmd.shutil, "which", lambda name: str(source) if name == "rg" else None
  )

  target = benchmark_cmd._stage_search_binary(tmp_path / "artifacts")

  assert target.read_bytes() == b"static ripgrep"
  assert target.stat().st_mode & stat.S_IXUSR
