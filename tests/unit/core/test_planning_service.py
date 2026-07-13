"""Tests for PlanningService."""

from __future__ import annotations

from pathlib import Path

import pytest

from nonoka_cli.config.models import AgentsConfig, AgentRoleConfig, CLIConfig
from nonoka_cli.core.planning_service import PlanningService, build_planning_service


def test_planner_model_prefers_configured() -> None:
  config = AgentsConfig(
    planner=AgentRoleConfig(model="gpt-4o-mini"),
    executor=AgentRoleConfig(model="gpt-4o"),
  )
  service = PlanningService(working_dir=Path("."), config=config, default_model="fallback")
  assert service.planner_model == "gpt-4o-mini"
  assert service.executor_model == "gpt-4o"


def test_planner_model_falls_back_to_default() -> None:
  config = AgentsConfig()
  service = PlanningService(working_dir=Path("."), config=config, default_model="fallback")
  assert service.planner_model == "fallback"


def test_disabled_when_no_model() -> None:
  service = PlanningService(working_dir=Path("."), config=AgentsConfig())
  assert not service.enabled


def test_build_from_config() -> None:
  config = CLIConfig(model="base-model", agents=AgentsConfig(
    planner=AgentRoleConfig(model="planner-model"),
  ))
  service = build_planning_service(working_dir=Path("."), config=config)
  assert service.planner_model == "planner-model"
  assert service.executor_model == "base-model"
