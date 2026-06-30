"""Shared pytest fixtures for nonoka-cli."""

from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend():
  return "asyncio"
