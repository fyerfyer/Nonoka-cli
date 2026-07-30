"""Tests for SWE-bench task environment propagation."""

from nonoka_cli.benchmark.swe_environment import (
  TESTBED_SHELL_BOOTSTRAP,
  build_testbed_exec_command,
  protected_test_paths,
  swe_profile,
)


def test_build_testbed_exec_command_activates_environment_before_agent():
  command = build_testbed_exec_command(
    container_id="container-123",
    environment={"OPENAI_API_KEY": "secret", "NONOKA_LOG_FILE": "/logs/nonoka.log"},
    command=["/opt/nonoka-runtime/venv/bin/python", "-Es", "-m", "watchdog", "task text"],
  )

  assert command[:2] == ["docker", "exec"]
  assert command[2:6] == [
    "--env",
    "OPENAI_API_KEY=secret",
    "--env",
    "NONOKA_LOG_FILE=/logs/nonoka.log",
  ]
  assert command[6:11] == [
    "container-123",
    "bash",
    "-lc",
    TESTBED_SHELL_BOOTSTRAP,
    "nonoka-testbed",
  ]
  assert "conda activate testbed" in TESTBED_SHELL_BOOTSTRAP
  assert command[11:] == [
    "/opt/nonoka-runtime/venv/bin/python",
    "-Es",
    "-m",
    "watchdog",
    "task text",
  ]


def test_swe_profile_requires_strict_project_verification():
  options = swe_profile("provider/model", 0.0, 3600)["provider"]["nonoka"]["options"]

  assert options["verificationEnforcement"] == "strict"
  assert options["maxCompletionCorrections"] == 3
  assert options["allowedVerificationKinds"] == ["test", "build", "lint", "typecheck"]
  assert options["env"] == {"NONOKA_DISABLE_PROJECT_AGENTS": "1"}


def test_protected_test_paths_include_instance_declared_tests():
  paths = protected_test_paths(
    {
      "test_patch": (
        "diff --git a/sympy/printing/tests/test_ccode.py b/sympy/printing/tests/test_ccode.py\n"
      ),
      "FAIL_TO_PASS": '["sympy/printing/tests/test_ccode.py::test_sinc"]',
      "PASS_TO_PASS": ["tests/test_regression.py::test_existing"],
    }
  )

  assert paths == [
    "/testbed/sympy/printing/tests/test_ccode.py",
    "/testbed/tests",
    "/testbed/tests/test_regression.py",
  ]
