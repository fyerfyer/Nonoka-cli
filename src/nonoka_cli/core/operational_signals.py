"""Derive bounded operational signals from redacted framework execution traces."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Literal

from pydantic import BaseModel, Field

_MUTATION_TOOLS = {"write", "write_file", "edit", "edit_file", "apply_patch", "delete_file"}
_VERIFIER_WORDS = ("pytest", "test", "verify", "lint", "check")
VerificationQuality = Literal["not_observed", "runner", "unverified_script", "ambiguous"]


class TraceSignals(BaseModel):
  output_timing_source: str = "unavailable"
  time_to_first_output_seconds: float | None = None
  time_to_first_mutation_seconds: float | None = None
  tool_calls_before_first_mutation: int | None = None
  verifier_ran_after_last_mutation: bool | None = None
  verification_quality: VerificationQuality = "not_observed"
  repeated_no_progress_calls: int = 0
  partial_observations_without_completion: int = 0
  terminal_reason: str = "unknown"
  wall_time_seconds: float | None = None
  # ``None`` means the model backend did not report token usage.  Keeping
  # unknown distinct from zero prevents logs/scorecards from claiming a
  # zero-token run when a provider omits streaming usage metadata.
  total_tokens: int | None = None
  estimated_cost_usd: float | None = None
  tool_calls: int = 0


class SignalsReport(BaseModel):
  traces: int = 0
  terminal_reasons: dict[str, int] = Field(default_factory=dict)
  p50: dict[str, float] = Field(default_factory=dict)
  p95: dict[str, float] = Field(default_factory=dict)
  individual: list[TraceSignals] = Field(default_factory=list)


def load_traces(path: Path) -> list[dict[str, Any]]:
  """Load a framework JSON trace or grouped bridge NDJSON stream events."""
  text = path.read_text(encoding="utf-8")
  try:
    value = json.loads(text)
  except json.JSONDecodeError:
    records = [json.loads(line) for line in text.splitlines() if line.strip()]
    return _bridge_execution_traces(records)
  if isinstance(value, dict):
    return [value]
  if isinstance(value, list):
    return [item for item in value if isinstance(item, dict)]
  raise ValueError(f"Unsupported trace payload in {path}")


def analyze_trace(trace: dict[str, Any]) -> TraceSignals:
  started = _timestamp(trace.get("started_at"))
  turns = list(trace.get("turns") or [])
  tools = list(trace.get("tool_calls") or [])
  mutations = [index for index, tool in enumerate(tools) if _is_mutation(tool)]
  first_mutation = mutations[0] if mutations else None
  first_mutation_time = _tool_time(tools[first_mutation]) if first_mutation is not None else None
  last_mutation_at = _tool_time(tools[mutations[-1]]) if mutations else None
  first_response = next(
    (
      _timestamp(turn.get("responded_at")) for turn in turns if _timestamp(turn.get("responded_at"))
    ),
    None,
  )
  termination = trace.get("termination") if isinstance(trace.get("termination"), dict) else {}
  termination_at = _timestamp(termination.get("at"))
  verification_quality = _verification_quality_after(trace, tools, last_mutation_at)
  return TraceSignals(
    output_timing_source=str(trace.get("output_timing_source") or "model_response"),
    time_to_first_output_seconds=_elapsed(started, first_response),
    time_to_first_mutation_seconds=_elapsed(started, first_mutation_time),
    tool_calls_before_first_mutation=first_mutation,
    verifier_ran_after_last_mutation=(
      verification_quality in {"runner", "ambiguous"} if mutations else None
    ),
    verification_quality=verification_quality if mutations else "not_observed",
    repeated_no_progress_calls=_repeated_no_progress(tools),
    partial_observations_without_completion=_unresolved_partial_observations(tools),
    terminal_reason=str(termination.get("reason") or termination.get("error_type") or "stop"),
    wall_time_seconds=_elapsed(started, termination_at),
    total_tokens=_total_tokens(turns),
    estimated_cost_usd=_estimated_cost(turns),
    tool_calls=len(tools),
  )


def summarize_traces(traces: list[dict[str, Any]]) -> SignalsReport:
  individual = [analyze_trace(trace) for trace in traces]
  reasons: dict[str, int] = {}
  for signal in individual:
    reasons[signal.terminal_reason] = reasons.get(signal.terminal_reason, 0) + 1
  keys = (
    "time_to_first_output_seconds",
    "wall_time_seconds",
    "total_tokens",
    "tool_calls",
    "estimated_cost_usd",
  )
  values = {
    key: [float(getattr(signal, key)) for signal in individual if getattr(signal, key) is not None]
    for key in keys
  }
  return SignalsReport(
    traces=len(individual),
    terminal_reasons=reasons,
    p50={key: median(items) for key, items in values.items() if items},
    p95={key: _percentile(items, 0.95) for key, items in values.items() if items},
    individual=individual,
  )


def _timestamp(value: Any) -> datetime | None:
  if not isinstance(value, str):
    return None
  try:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
  except ValueError:
    return None


def _elapsed(start: datetime | None, end: datetime | None) -> float | None:
  return (end - start).total_seconds() if start is not None and end is not None else None


def _tool_time(tool: dict[str, Any]) -> datetime | None:
  return _timestamp(tool.get("started_at")) or _timestamp(tool.get("ended_at"))


def _is_mutation(tool: dict[str, Any]) -> bool:
  if str(tool.get("name", "")).lower() in _MUTATION_TOOLS:
    return True
  receipt = tool.get("external_receipt")
  workspace = receipt.get("workspace") if isinstance(receipt, dict) else None
  if not isinstance(workspace, dict):
    return False
  return any(workspace.get(key) for key in ("created", "modified", "deleted"))


def _verification_quality_after(
  trace: dict[str, Any], tools: list[dict[str, Any]], last_mutation: datetime | None
) -> VerificationQuality:
  """Classify evidence after the last mutation without treating names as proof."""
  if last_mutation is None:
    return "not_observed"
  verifications = trace.get("verifications") or []
  saw_typed_verification = any(
    (_timestamp(item.get("at")) or last_mutation) >= last_mutation
    for item in verifications
    if isinstance(item, dict)
  )
  saw_ambiguous_command = False
  for tool in tools:
    arguments = tool.get("arguments")
    command = arguments.get("command", "") if isinstance(arguments, dict) else ""
    if isinstance(command, str) and _tool_time(tool) and _tool_time(tool) >= last_mutation:
      if _is_direct_test_script(command):
        return "unverified_script"
      if _is_test_runner(command):
        return "runner"
      if any(word in command.lower() for word in _VERIFIER_WORDS):
        saw_ambiguous_command = True
  return "ambiguous" if saw_ambiguous_command or saw_typed_verification else "not_observed"


def _is_test_runner(command: str) -> bool:
  """Recognize common test-runner invocations without parsing shell syntax."""
  import re

  return bool(
    re.search(
      r"(?:^|[;&|]\s*)(?:[A-Za-z_][A-Za-z0-9_]*=[^\s;&|]+\s+)*(?:"
      r"(?:[^\s;&|]*/)?pytest(?:-[\w.-]+)?|"
      r"(?:python|python\d+(?:\.\d+)?|pypy\d*)\s+(?:-[A-Za-z]+\s+)*-m\s+pytest|"
      r"(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test\b|"
      r"(?:npx\s+)?(?:jest|vitest)\b|"
      r"go\s+test\b|cargo\s+test\b|dotnet\s+test\b|"
      r"(?:bundle\s+exec\s+)?rspec\b|phpunit\b|(?:mvn|gradle|\.\/gradlew)\s+test\b"
      r")",
      command,
      flags=re.IGNORECASE,
    )
  )


def _is_direct_test_script(command: str) -> bool:
  """Identify the false-positive pattern: executing a test source as a script."""
  import re

  return bool(
    re.search(
      r"\b(?:python|python\d+(?:\.\d+)?|pypy\d*)\s+"
      r"(?:-[A-Za-z]+\s+)*[^\s;|&]*(?:test|tests|spec)[^\s/]*\.py\b",
      command,
      flags=re.IGNORECASE,
    )
  )


def _repeated_no_progress(tools: list[dict[str, Any]]) -> int:
  repeated = 0
  previous: str | None = None
  for tool in tools:
    if _is_mutation(tool):
      previous = None
      continue
    signature = json.dumps([tool.get("name"), tool.get("arguments")], sort_keys=True, default=str)
    if signature == previous:
      repeated += 1
    previous = signature
  return repeated


def _unresolved_partial_observations(tools: list[dict[str, Any]]) -> int:
  unresolved = 0
  for index, tool in enumerate(tools):
    receipt = tool.get("external_receipt")
    if not isinstance(receipt, dict) or receipt.get("completeness") != "partial":
      continue
    name = tool.get("name")
    later_complete = any(
      item.get("name") == name
      and isinstance(item.get("external_receipt"), dict)
      and item["external_receipt"].get("completeness") == "complete"
      for item in tools[index + 1 :]
    )
    if not later_complete:
      unresolved += 1
  return unresolved


def _total_tokens(turns: list[dict[str, Any]]) -> int | None:
  total = 0
  observed = False
  for turn in turns:
    response = turn.get("response")
    usage = response.get("usage", {}) if isinstance(response, dict) else {}
    if isinstance(usage, dict):
      if usage.get("total_tokens") is not None:
        observed = True
        total += int(usage["total_tokens"])
      else:
        input_value = usage.get("prompt_tokens", usage.get("input_tokens"))
        output_value = usage.get("completion_tokens", usage.get("output_tokens"))
        if input_value is not None or output_value is not None:
          observed = True
          total += int(input_value or 0)
          total += int(output_value or 0)
  return total if observed else None


def _estimated_cost(turns: list[dict[str, Any]]) -> float | None:
  values: list[float] = []
  for turn in turns:
    response = turn.get("response")
    usage = response.get("usage", {}) if isinstance(response, dict) else {}
    cost = usage.get("estimated_cost_usd") if isinstance(usage, dict) else None
    if cost is not None:
      values.append(float(cost))
  return sum(values) if values else None


def _bridge_execution_traces(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
  """Convert bridge trace records into the common execution-trace shape."""
  visible_event_types = {"content_delta", "tool_call_start", "approval_request"}
  session_first_output: dict[str, dict[str, Any]] = {}
  for record in records:
    session_id = record.get("session_id")
    if (
      session_id
      and record.get("event") == "stream_event"
      and record.get("event_type") in visible_event_types
    ):
      session_first_output.setdefault(str(session_id), record)

  grouped: dict[str, list[dict[str, Any]]] = {}
  for record in records:
    request_id = str(record.get("request_id") or "unknown")
    grouped.setdefault(request_id, []).append(record)

  traces: list[dict[str, Any]] = []
  for request_id, items in grouped.items():
    items.sort(key=lambda item: str(item.get("ts", "")))
    request = next((item for item in items if item.get("event") == "request_entry"), items[0])
    stream = [item for item in items if item.get("event") == "stream_event"]
    first_output = next(
      (
        item
        for item in stream
        if item.get("event_type") in visible_event_types
      ),
      None,
    )
    tool_calls: list[dict[str, Any]] = []
    for item in stream:
      if item.get("event_type") != "tool_call_start":
        continue
      for tool in item.get("data", {}).get("tool_calls", []):
        tool_calls.append(
          {
            "id": tool.get("id"),
            "name": tool.get("name"),
            "started_at": item.get("ts"),
          }
        )
    terminal = next(
      (item for item in reversed(stream) if item.get("event_type") in {"final", "error"}),
      None,
    )
    terminal_data = terminal.get("data", {}) if terminal else {}
    complete_record = next(
      (
        item
        for item in reversed(items)
        if item.get("event") == "execution_trace" and isinstance(item.get("trace"), dict)
      ),
      None,
    )
    complete_trace = complete_record.get("trace") if complete_record is not None else None
    if isinstance(complete_trace, dict):
      trace = dict(complete_trace)
      trace["request_id"] = request_id
      trace["output_timing_source"] = "bridge_stream_event"
      if not trace.get("started_at"):
        trace["started_at"] = request.get("ts")
      session_id = str(
        complete_record.get("session_id")
        or request.get("session_id")
        or ""
      )
      effective_first_output = session_first_output.get(session_id) or first_output
      if effective_first_output:
        turns = list(trace.get("turns") or [])
        if turns:
          turns[0] = {**turns[0], "responded_at": effective_first_output.get("ts")}
        else:
          turns = [{"responded_at": effective_first_output.get("ts"), "response": {}}]
        trace["turns"] = turns
      if terminal:
        termination = trace.get("termination")
        if not isinstance(termination, dict):
          termination = {}
        termination.setdefault("at", terminal.get("ts"))
        termination.setdefault(
          "reason",
          terminal_data.get("termination", {}).get("reason")
          if isinstance(terminal_data.get("termination"), dict)
          else terminal_data.get("error_type") or "stop",
        )
        trace["termination"] = termination
      traces.append(trace)
      continue
    traces.append(
      {
        "request_id": request_id,
        "started_at": request.get("ts"),
        "output_timing_source": "bridge_stream_event",
        "turns": (
          [{"responded_at": first_output.get("ts"), "response": {}}] if first_output else []
        ),
        "tool_calls": tool_calls,
        "termination": {
          "at": terminal.get("ts") if terminal else None,
          "reason": (
            terminal_data.get("termination", {}).get("reason")
            if isinstance(terminal_data.get("termination"), dict)
            else terminal_data.get("error_type")
          )
          or ("stop" if terminal and terminal.get("event_type") == "final" else "unknown"),
        },
      }
    )
  return traces


def _percentile(values: list[float], quantile: float) -> float:
  ordered = sorted(values)
  index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * quantile))))
  return ordered[index]
