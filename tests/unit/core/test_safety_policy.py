import pytest
from nonoka.core.errors import SafetyError
from nonoka.safety import SafetyPolicy


def test_dangerous_command_requires_approval():
  assert SafetyPolicy().check_command("rm file.txt") == "approval"


def test_root_delete_is_denied():
  with pytest.raises(SafetyError):
    SafetyPolicy().check_command("rm -rf /")


def test_denied_path_is_rejected(tmp_path):
  with pytest.raises(SafetyError):
    SafetyPolicy(allowed_roots=[tmp_path]).check_path(tmp_path / ".env")
