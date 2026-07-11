"""Tool output pruning / spill policy for managing context bloat.

Long tool outputs (shell logs, file reads, command output) are the primary
cause of context-window exhaustion in coding agents.  This module applies
configurable truncation or spill-to-file strategies before a result is written
to the agent's working memory.

Strategies (inspired by pydantic-harness tool-output management):
- head_tail: keep first N + last M lines/tokens, mark the middle as truncated.
- tail_only: keep only the last N lines/tokens (best for logs/build output).
- head_only: keep only the first N lines/tokens (best for file reads).
- spill: write the full output to disk, return a path + summary.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import structlog

logger = structlog.get_logger("nonoka_cli.core.tool_output")


TruncationStrategy = Literal["head_tail", "tail_only", "head_only", "spill"]


@dataclass
class ToolOutputRule:
  """Per-tool output policy rule."""

  max_tokens: int = 4000
  max_lines: int | None = 200
  strategy: TruncationStrategy = "tail_only"
  spill_dir: str | Path = field(
    default_factory=lambda: os.environ.get("NONOKA_TRACE_DIR", "/tmp/nonoka-trace")
  )


class ToolOutputPolicy:
  """Apply truncation/spill rules to tool results before they enter memory."""

  _DEFAULT_RULES: dict[str, ToolOutputRule] = {
    "bash": ToolOutputRule(max_tokens=4000, max_lines=200, strategy="tail_only"),
    "execute_command": ToolOutputRule(max_tokens=4000, max_lines=200, strategy="tail_only"),
    "read": ToolOutputRule(max_tokens=4000, max_lines=200, strategy="head_tail"),
    "read_file": ToolOutputRule(max_tokens=4000, max_lines=200, strategy="head_tail"),
    "write": ToolOutputRule(max_tokens=500, max_lines=10, strategy="head_only"),
    "write_file": ToolOutputRule(max_tokens=500, max_lines=10, strategy="head_only"),
    "edit": ToolOutputRule(max_tokens=500, max_lines=10, strategy="head_only"),
    "edit_file": ToolOutputRule(max_tokens=500, max_lines=10, strategy="head_only"),
    "delete_file": ToolOutputRule(max_tokens=500, max_lines=10, strategy="head_only"),
  }

  def __init__(
    self,
    rules: dict[str, ToolOutputRule] | None = None,
    default_rule: ToolOutputRule | None = None,
    enabled: bool = True,
  ):
    self._rules = dict(self._DEFAULT_RULES)
    if rules:
      self._rules.update(rules)
    self._default_rule = default_rule or ToolOutputRule(
      max_tokens=4000, max_lines=200, strategy="tail_only"
    )
    self.enabled = enabled

  @classmethod
  def from_config(cls, data: dict[str, Any] | "ToolOutputPolicy" | None) -> "ToolOutputPolicy":
    """Build a policy from a config dict or return an existing policy."""
    if data is None:
      return cls()
    if isinstance(data, ToolOutputPolicy):
      return data

    enabled = data.get("enabled", True)
    default_cfg = data.get("default_rule") or {}
    default_rule = cls._rule_from_dict(default_cfg)

    rules: dict[str, ToolOutputRule] = {}
    for name, cfg in (data.get("rules") or {}).items():
      rules[name] = cls._rule_from_dict(cfg)

    return cls(rules=rules, default_rule=default_rule, enabled=enabled)

  @staticmethod
  def _rule_from_dict(cfg: dict[str, Any]) -> ToolOutputRule:
    """Convert a plain dict into a ``ToolOutputRule``."""
    if not cfg:
      return ToolOutputRule()
    strategy = cfg.get("strategy", "tail_only")
    if strategy not in ("head_tail", "tail_only", "head_only", "spill"):
      strategy = "tail_only"
    spill_dir = cfg.get("spill_dir")
    if spill_dir is None:
      spill_dir = os.environ.get("NONOKA_TRACE_DIR", "/tmp/nonoka-trace")
    return ToolOutputRule(
      max_tokens=int(cfg.get("max_tokens", 4000)),
      max_lines=int(cfg.get("max_lines", 200)) if cfg.get("max_lines") is not None else None,
      strategy=strategy,  # type: ignore[arg-type]
      spill_dir=spill_dir,
    )

  def apply(self, tool_name: str, result: Any, tool_call_id: str | None = None) -> Any:
    """Apply the matching rule to *result* and return the pruned value.

    If *result* is not a string or is already small enough, it is returned
    unchanged.  For structured results (dict/list), the result is stringified
    for size checking but the original structure is preserved when no pruning
    is needed.
    """
    rule = self._rules.get(tool_name, self._default_rule)

    text, is_json = self._to_text(result)
    if not text:
      return result

    lines = text.splitlines()
    exceeds_tokens = self._estimate_tokens(text) > rule.max_tokens
    exceeds_lines = rule.max_lines is not None and len(lines) > rule.max_lines

    if not exceeds_tokens and not exceeds_lines:
      return result

    logger.info(
      "tool_output_pruned",
      tool=tool_name,
      strategy=rule.strategy,
      original_lines=len(lines),
      original_tokens=self._estimate_tokens(text),
      max_lines=rule.max_lines,
      max_tokens=rule.max_tokens,
    )

    if rule.strategy == "spill":
      return self._spill(text, tool_name, tool_call_id, rule)

    pruned_text = self._truncate(text, lines, rule)
    return self._from_text(pruned_text, is_json, result)

  def _to_text(self, result: Any) -> tuple[str, bool]:
    """Convert a result to text for measurement."""
    if isinstance(result, str):
      return result, False
    try:
      return json.dumps(result, ensure_ascii=False, default=str), True
    except (TypeError, ValueError):
      return str(result), False

  def _from_text(self, text: str, is_json: bool, original: Any) -> Any:
    """Convert pruned text back to the original type when possible."""
    if isinstance(original, str):
      return text
    if is_json:
      try:
        return json.loads(text)
      except json.JSONDecodeError:
        return text
    return text

  @staticmethod
  def _estimate_tokens(text: str) -> int:
    """Fast token estimate: ~4 chars per token for English/code."""
    return max(1, len(text) // 4)

  @staticmethod
  def _truncate(text: str, lines: list[str], rule: ToolOutputRule) -> str:
    """Apply the configured truncation strategy."""
    max_lines = rule.max_lines or len(lines)

    if rule.strategy == "tail_only":
      kept = lines[-max_lines:]
      if len(lines) > len(kept):
        truncated = len(lines) - len(kept)
        kept.insert(0, f"[... {truncated} lines truncated; showing last {len(kept)} lines]")
      return "\n".join(kept)

    if rule.strategy == "head_only":
      kept = lines[:max_lines]
      if len(lines) > len(kept):
        truncated = len(lines) - len(kept)
        kept.append(f"[... {truncated} lines truncated; showing first {len(kept)} lines]")
      return "\n".join(kept)

    # head_tail: keep first half and last half of the budget.
    half = max_lines // 2
    head = lines[:half]
    tail = lines[-half:] if max_lines > 1 else []
    omitted = len(lines) - len(head) - len(tail)
    out = list(head)
    if omitted > 0:
      out.append(f"[... {omitted} lines truncated]")
    out.extend(tail)
    return "\n".join(out)

  def _spill(
    self,
    text: str,
    tool_name: str,
    tool_call_id: str | None,
    rule: ToolOutputRule,
  ) -> dict[str, Any]:
    """Write the full output to disk and return a compact reference."""
    spill_dir = Path(rule.spill_dir) / "tool-output"
    spill_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{tool_name}-{tool_call_id or datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
    # Sanitize filename.
    filename = "".join(c if c.isalnum() or c in "-_" else "_" for c in filename)
    path = spill_dir / f"{filename}.txt"

    try:
      path.write_text(text, encoding="utf-8")
    except OSError as exc:
      logger.error("tool_output_spill_failed", error=str(exc))
      return {
        "result_preview": text[:500],
        "full_output_path": None,
        "error": f"Failed to spill output: {exc}",
      }

    preview_lines = text.splitlines()[:10]
    preview = "\n".join(preview_lines)
    return {
      "result_preview": preview,
      "full_output_path": str(path),
      "note": f"Full output ({len(text)} chars, {len(text.splitlines())} lines) written to {path}",
    }
