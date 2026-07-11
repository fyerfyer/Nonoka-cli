"""Tests for the tool output pruning / spill policy."""

from __future__ import annotations

import pytest

from nonoka_cli.core.tool_output_policy import (
  ToolOutputPolicy,
  ToolOutputRule,
)


def test_small_output_preserved():
  policy = ToolOutputPolicy()
  result = "short"
  assert policy.apply("bash", result) == result


def test_tail_only_truncation():
  policy = ToolOutputPolicy(
    rules={"bash": ToolOutputRule(max_lines=3, max_tokens=10000, strategy="tail_only")}
  )
  lines = [f"line {i}" for i in range(10)]
  result = "\n".join(lines)
  pruned = policy.apply("bash", result)
  assert "line 9" in pruned
  assert "line 0" not in pruned
  assert "truncated" in pruned


def test_head_only_truncation():
  policy = ToolOutputPolicy(
    rules={"read": ToolOutputRule(max_lines=2, max_tokens=10000, strategy="head_only")}
  )
  result = "\n".join(f"line {i}" for i in range(5))
  pruned = policy.apply("read", result)
  assert "line 0" in pruned
  assert "line 4" not in pruned


def test_head_tail_truncation():
  policy = ToolOutputPolicy(
    rules={"read": ToolOutputRule(max_lines=4, max_tokens=10000, strategy="head_tail")}
  )
  result = "\n".join(f"line {i}" for i in range(10))
  pruned = policy.apply("read", result)
  assert "line 0" in pruned
  assert "line 9" in pruned
  assert "line 5" not in pruned


def test_default_rule_applies_to_unknown_tools():
  policy = ToolOutputPolicy(
    default_rule=ToolOutputRule(max_lines=2, max_tokens=10000, strategy="tail_only")
  )
  result = "\n".join(f"line {i}" for i in range(5))
  pruned = policy.apply("unknown_tool", result)
  assert "line 4" in pruned
  assert "line 0" not in pruned


def test_structured_result_preserved_when_small():
  policy = ToolOutputPolicy()
  result = {"key": "value"}
  assert policy.apply("bash", result) == result


def test_structured_result_truncated_as_json():
  policy = ToolOutputPolicy(
    rules={"bash": ToolOutputRule(max_lines=1, max_tokens=10, strategy="head_tail")}
  )
  result = {"lines": [f"line {i}" for i in range(20)]}
  pruned = policy.apply("bash", result)
  assert isinstance(pruned, str)
  assert "truncated" in pruned


def test_spill_writes_to_disk(tmp_path):
  policy = ToolOutputPolicy(
    rules={
      "bash": ToolOutputRule(
        max_lines=2, max_tokens=1, strategy="spill", spill_dir=str(tmp_path)
      )
    }
  )
  result = "\n".join(f"line {i}" for i in range(5))
  pruned = policy.apply("bash", result, tool_call_id="tc1")
  assert isinstance(pruned, dict)
  assert pruned["full_output_path"] is not None
  path = pruned["full_output_path"]
  assert path.startswith(str(tmp_path))
  assert "line 4" in path or path.endswith(".txt")
  written = tmp_path / "tool-output" / path.split("/")[-1]
  assert written.exists()
  assert "line 0" in written.read_text()


def test_from_config():
  policy = ToolOutputPolicy.from_config({
    "enabled": True,
    "default_rule": {"max_lines": 5, "strategy": "head_only"},
    "rules": {"bash": {"max_lines": 3, "strategy": "tail_only"}},
  })
  assert policy.enabled is True
  assert policy._default_rule.max_lines == 5
  assert policy._default_rule.strategy == "head_only"
  assert policy._rules["bash"].max_lines == 3


def test_from_config_disabled():
  policy = ToolOutputPolicy.from_config({"enabled": False})
  assert policy.enabled is False


def test_invalid_strategy_defaults_to_tail_only():
  policy = ToolOutputPolicy.from_config({
    "rules": {"bash": {"strategy": "invalid", "max_lines": 2}}
  })
  assert policy._rules["bash"].strategy == "tail_only"


if __name__ == "__main__":
  pytest.main([__file__, "-v"])
