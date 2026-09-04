"""Shared candidate-profile loader with gitignored local override.

Loading contract (PII-sections-only override):

1. ``config/profile.yml`` (tracked template/defaults) is always the base.
   It owns non-personal configuration: evaluation thresholds, model chains,
   queue_export settings.
2. ``config/profile.local.yml`` (gitignored, never committed) may override
   ONLY personal-data sections: ``candidate``, ``target_roles``, ``skills``,
   ``preferences``. Any other section present in the local file (e.g. an
   ``evaluation`` block copied from another project) is ignored, so a local
   file can never silently change thresholds or model chains.

The local override is applied ONLY when the requested base path is the
default tracked profile. Explicit custom paths (hermetic tests, ``--profile``
overrides) load exactly the given file with no merging — this keeps tests
hermetic and prevents real PII from leaking into synthetic fixtures.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_PROFILE_PATH = Path("config/profile.yml")
DEFAULT_LOCAL_PATH = Path("config/profile.local.yml")

# Personal-data sections a local profile may override. Everything else
# (evaluation, queue_export, ...) always comes from the tracked base.
LOCAL_OVERRIDE_SECTIONS: tuple[str, ...] = (
    "candidate",
    "target_roles",
    "skills",
    "preferences",
)


def _load_yaml_file(path: Path) -> dict[str, Any]:
    """Load a YAML mapping, returning {} when missing/unparseable."""
    try:
        if not path.exists():
            return {}
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_profile_with_local(
    profile_path: str | Path = DEFAULT_PROFILE_PATH,
    local_path: str | Path = DEFAULT_LOCAL_PATH,
) -> dict[str, Any]:
    """Load the candidate profile with gitignored local PII override.

    Args:
        profile_path: Base tracked profile (default ``config/profile.yml``).
        local_path: Gitignored local override (default
            ``config/profile.local.yml``).

    Returns:
        Merged profile dict. Local personal-data sections win; all other
        sections come from the base file. Custom (non-default) base paths
        never merge the local file.
    """
    base_path = Path(profile_path)
    profile = _load_yaml_file(base_path)

    # Only the default tracked profile participates in local override.
    # Custom paths (tests, --profile) load exactly what was requested.
    if base_path != DEFAULT_PROFILE_PATH:
        return profile

    local_file = Path(local_path)
    if local_file == base_path or not local_file.exists():
        return profile

    local_data = _load_yaml_file(local_file)
    for section in LOCAL_OVERRIDE_SECTIONS:
        if section in local_data:
            profile[section] = local_data[section]
    return profile


__all__ = [
    "DEFAULT_LOCAL_PATH",
    "DEFAULT_PROFILE_PATH",
    "LOCAL_OVERRIDE_SECTIONS",
    "load_profile_with_local",
]
