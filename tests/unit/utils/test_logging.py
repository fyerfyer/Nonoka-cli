"""Tests for resilient CLI logging setup."""

from __future__ import annotations

import logging

from nonoka_cli.utils.logging import setup_logging


def test_setup_logging_degrades_when_log_file_is_unwritable(monkeypatch, tmp_path):
  def fail(*args, **kwargs):
    raise OSError("read-only filesystem")

  monkeypatch.setattr(logging, "FileHandler", fail)
  logger = setup_logging(log_file=tmp_path / "blocked" / "nonoka.log")
  assert logger is not None


def test_setup_logging_honors_explicit_log_file_env(monkeypatch, tmp_path):
  target = tmp_path / "logs" / "cli.log"
  monkeypatch.setenv("NONOKA_LOG_FILE", str(target))
  setup_logging()
  assert target.exists()
