"""Named, least-privilege network policies for the SRT process tree."""

from __future__ import annotations

from typing import Any


NETWORK_PROFILE_DOMAINS: dict[str, frozenset[str]] = {
  "strict": frozenset(),
  # These hosts are package distribution endpoints only. A package's own API
  # must remain an explicit project allowlist entry.
  "package-registries": frozenset({
    "registry.npmjs.org",
    "pypi.org",
    "files.pythonhosted.org",
  }),
}


def resolved_srt_allowed_domains(safety: Any) -> list[str]:
  """Return the effective SRT domain allowlist for a safety configuration."""
  profile = str(getattr(safety, "network_profile", "strict"))
  profile_domains = NETWORK_PROFILE_DOMAINS.get(profile, frozenset())
  explicit_domains = getattr(safety, "allowed_domains", ()) or ()
  return sorted({*profile_domains, *(str(domain) for domain in explicit_domains if domain)})
