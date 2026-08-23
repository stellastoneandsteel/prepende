"""Prepende-first environment compatibility without leaking configured values."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, MutableMapping


_WARNED_CONFLICTS: set[str] = set()


def brand_env(
    suffix: str,
    default: str = "",
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Return PREPENDE_SUFFIX, falling back to the legacy ENGRAM_SUFFIX.

    Presence is authoritative, including an explicit empty canonical value.
    When both names differ, only the variable names are reported and the
    canonical Prepende value wins.
    """

    source = os.environ if env is None else env
    canonical_name = f"PREPENDE_{suffix}"
    legacy_name = f"ENGRAM_{suffix}"
    canonical_present = canonical_name in source
    legacy_present = legacy_name in source
    canonical = str(source.get(canonical_name, "")).strip()
    legacy = str(source.get(legacy_name, "")).strip()
    if canonical_present and legacy_present and canonical != legacy:
        if suffix not in _WARNED_CONFLICTS:
            _WARNED_CONFLICTS.add(suffix)
            print(
                f"prepende config conflict: {canonical_name} overrides deprecated {legacy_name}",
                file=sys.stderr,
            )
    if canonical_present:
        return canonical
    if legacy_present:
        return legacy
    return default


def mirror_brand_environment(env: MutableMapping[str, str] | None = None) -> None:
    """Mirror aliases so unconverted readers still observe Prepende precedence."""

    target = os.environ if env is None else env
    suffixes = {
        key.removeprefix("PREPENDE_")
        for key in target
        if key.startswith("PREPENDE_")
    } | {
        key.removeprefix("ENGRAM_")
        for key in target
        if key.startswith("ENGRAM_")
    }
    for suffix in sorted(suffixes):
        canonical_name = f"PREPENDE_{suffix}"
        legacy_name = f"ENGRAM_{suffix}"
        canonical_present = canonical_name in target
        value = brand_env(suffix, env=target)
        if canonical_present:
            target[legacy_name] = value
        else:
            target[canonical_name] = value
