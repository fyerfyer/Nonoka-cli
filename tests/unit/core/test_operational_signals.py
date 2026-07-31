from __future__ import annotations

import json

from nonoka_cli.core.operational_signals import analyze_trace, load_traces, summarize_traces


def _trace() -> dict:
  return {
    "started_at": "2026-07-27T00:00:00+00:00",
    "turns": [
      {
        "responded_at": "2026-07-27T00:00:02+00:00",
        "response": {"usage": {"prompt_tokens": 10, "completion_tokens": 5}},
      }
    ],
    "tool_calls": [
      {
        "name": "read",
        "arguments": {"path": "a.py"},
        "started_at": "2026-07-27T00:00:03+00:00",
      },
      {
        "name": "read",
        "arguments": {"path": "a.py"},
        "started_at": "2026-07-27T00:00:04+00:00",
      },
      {
        "name": "write_file",
        "arguments": {"path": "a.py"},
        "started_at": "2026-07-27T00:00:05+00:00",
        "ended_at": "2026-07-27T00:00:06+00:00",
      },
      {
        "name": "bash",
        "arguments": {"command": "pytest -q"},
        "started_at": "2026-07-27T00:00:07+00:00",
        "external_receipt": {"completeness": "partial"},
      },
    ],
    "termination": {"at": "2026-07-27T00:00:10+00:00", "reason": "stop"},
  }


def test_analyze_trace_reports_progress_and_verification():
  signal = analyze_trace(_trace())
  assert signal.time_to_first_output_seconds == 2
  assert signal.time_to_first_mutation_seconds == 5
  assert signal.tool_calls_before_first_mutation == 2
  assert signal.verifier_ran_after_last_mutation is True
  assert signal.verification_quality == "runner"
  assert signal.repeated_no_progress_calls == 1
  assert signal.partial_observations_without_completion == 1
  assert signal.total_tokens == 15


def test_analyze_trace_does_not_treat_direct_test_script_as_runner_verification():
  trace = _trace()
  trace["tool_calls"][-1]["arguments"] = {"command": "timeout 30 python3 -u /app/test_outputs.py"}

  signal = analyze_trace(trace)

  assert signal.verifier_ran_after_last_mutation is False
  assert signal.verification_quality == "unverified_script"


def test_typed_verification_does_not_hide_test_runner_quality():
  trace = _trace()
  trace["verifications"] = [
    {
      "at": "2026-07-27T00:00:08+00:00",
      "status": "passed",
      "kind": "test",
      "command": "NONOKA_VERIFY=focused ../.venv/bin/pytest -q",
    }
  ]
  trace["tool_calls"][-1]["arguments"] = {
    "command": "NONOKA_VERIFY=focused ../.venv/bin/pytest -q"
  }

  signal = analyze_trace(trace)

  assert signal.verifier_ran_after_last_mutation is True
  assert signal.verification_quality == "runner"


def test_summarize_traces_keeps_terminal_distribution_and_percentiles():
  first = _trace()
  second = _trace()
  second["termination"] = {"at": "2026-07-27T00:00:20+00:00", "reason": "deadline"}
  report = summarize_traces([first, second])
  assert report.terminal_reasons == {"stop": 1, "deadline": 1}
  assert report.p50["tool_calls"] == 4
  assert report.p95["wall_time_seconds"] == 20


def test_analyze_trace_reports_missing_usage_as_unknown_not_zero():
  trace = _trace()
  trace["turns"][0]["response"]["usage"] = {}

  signal = analyze_trace(trace)
  report = summarize_traces([trace])

  assert signal.total_tokens is None
  assert "total_tokens" not in report.p50


def test_bridge_trace_prefers_terminal_framework_trace(tmp_path):
  path = tmp_path / "bridge.ndjson"
  trace = _trace()
  trace["tool_calls"][2]["external_receipt"] = {
    "workspace": {"created": ["answer.py"]},
    "completeness": "complete",
  }
  records = [
    {"ts": "2026-07-27T00:00:00+00:00", "request_id": "r1", "event": "request_entry"},
    {
      "ts": "2026-07-27T00:00:01+00:00",
      "request_id": "r1",
      "event": "stream_event",
      "event_type": "content_delta",
      "data": {},
    },
    {
      "ts": "2026-07-27T00:00:10+00:00",
      "request_id": "r1",
      "event": "stream_event",
      "event_type": "final",
      "data": {"success": True},
    },
    {
      "ts": "2026-07-27T00:00:10+00:00",
      "request_id": "r1",
      "event": "execution_trace",
      "trace": trace,
    },
  ]
  path.write_text("\n".join(json.dumps(record) for record in records))
  signal = analyze_trace(load_traces(path)[0])
  assert signal.output_timing_source == "bridge_stream_event"
  assert signal.time_to_first_output_seconds == 1
  assert signal.time_to_first_mutation_seconds == 5


def test_bridge_trace_counts_tool_call_as_first_streamed_output(tmp_path):
  path = tmp_path / "tool-first.ndjson"
  records = [
    {"ts": "2026-07-27T00:00:00+00:00", "request_id": "r1", "event": "request_entry"},
    {
      "ts": "2026-07-27T00:00:02+00:00",
      "request_id": "r1",
      "event": "stream_event",
      "event_type": "tool_call_start",
      "data": {"tool_calls": [{"id": "c1", "name": "read"}]},
    },
    {
      "ts": "2026-07-27T00:00:10+00:00",
      "request_id": "r1",
      "event": "stream_event",
      "event_type": "final",
      "data": {"success": True},
    },
  ]
  path.write_text("\n".join(json.dumps(record) for record in records))

  signal = analyze_trace(load_traces(path)[0])

  assert signal.time_to_first_output_seconds == 2


def test_bridge_trace_uses_first_output_across_resumed_request_ids(tmp_path):
  path = tmp_path / "resumed.ndjson"
  trace = _trace()
  records = [
    {
      "ts": "2026-07-27T00:00:00+00:00",
      "request_id": "r1",
      "session_id": "s1",
      "event": "request_entry",
    },
    {
      "ts": "2026-07-27T00:00:02+00:00",
      "request_id": "r1",
      "session_id": "s1",
      "event": "stream_event",
      "event_type": "tool_call_start",
      "data": {"tool_calls": [{"id": "c1", "name": "read"}]},
    },
    {
      "ts": "2026-07-27T00:00:09+00:00",
      "request_id": "r2",
      "session_id": "s1",
      "event": "request_entry",
    },
    {
      "ts": "2026-07-27T00:00:10+00:00",
      "request_id": "r2",
      "session_id": "s1",
      "event": "execution_trace",
      "trace": trace,
    },
  ]
  path.write_text("\n".join(json.dumps(record) for record in records))

  completed = load_traces(path)[-1]
  signal = analyze_trace(completed)

  assert signal.time_to_first_output_seconds == 2
