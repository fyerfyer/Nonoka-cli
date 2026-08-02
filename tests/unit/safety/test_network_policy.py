from types import SimpleNamespace

from nonoka_cli.config.models import SafetyConfig
from nonoka_cli.safety.network_policy import resolved_srt_allowed_domains


def test_strict_profile_uses_only_explicit_domains():
  safety = SafetyConfig(allowed_domains=["api.deepseek.com"])

  assert resolved_srt_allowed_domains(safety) == ["api.deepseek.com"]


def test_package_registry_profile_adds_only_distribution_hosts():
  safety = SafetyConfig(
    network_profile="package-registries",
    allowed_domains=["api.deepseek.com", "registry.npmjs.org"],
  )

  assert resolved_srt_allowed_domains(safety) == [
    "api.deepseek.com",
    "files.pythonhosted.org",
    "pypi.org",
    "registry.npmjs.org",
  ]


def test_missing_profile_stays_strict_for_legacy_configuration():
  safety = SimpleNamespace(allowed_domains=["api.deepseek.com"])

  assert resolved_srt_allowed_domains(safety) == ["api.deepseek.com"]
