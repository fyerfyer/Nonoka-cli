"""Behavior tests for the framework evaluation command facade."""

from __future__ import annotations

import argparse
import sys
import types
from unittest.mock import MagicMock

from nonoka_cli.cli import _build_parser
from nonoka_cli.commands import eval_cmd


def test_eval_delegates_exact_remaining_arguments(monkeypatch):
  args = argparse.Namespace(eval_args=["run", "--dataset", "tool_use", "--model", "fake"])
  module = types.ModuleType("nonoka.ext.eval.__main__")
  main = MagicMock(return_value=0)
  module.main = main
  monkeypatch.setitem(sys.modules, "nonoka.ext.eval.__main__", module)
  assert eval_cmd.cmd_eval(args) == 0
  main.assert_called_once_with(["run", "--dataset", "tool_use", "--model", "fake"])


def test_eval_parser_keeps_framework_flags():
  parser = argparse.ArgumentParser()
  subparsers = parser.add_subparsers(dest="command")
  eval_cmd.add_subparser(subparsers)
  args = parser.parse_args(["eval", "run", "--dataset", "tool_use", "--model", "fake"])
  assert args.eval_args == ["run", "--dataset", "tool_use", "--model", "fake"]


def test_eval_parser_forwards_help_to_framework():
  parser = argparse.ArgumentParser()
  subparsers = parser.add_subparsers(dest="command")
  eval_cmd.add_subparser(subparsers)
  args = parser.parse_args(["eval", "--help"])
  assert args.eval_help is True


def test_application_parser_registers_eval_command():
  args = _build_parser().parse_args(["eval", "list"])
  assert args.command == "eval"
  assert args.eval_args == ["list"]
