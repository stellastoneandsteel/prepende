"""Safe initialization for a fresh, cloneable Prepende knowledge vault.

An operator's local ``vault/`` is a private corpus, not a customer template.
New installations must start from ``vault-template/`` so distributing the
software never distributes somebody else's brain. This module deliberately performs
no model calls, database writes, or environment mutation.

Initialization is idempotent and non-destructive: existing identical template
files are accepted, unrelated files are preserved, and any colliding file with
different contents aborts the entire operation before a single write occurs.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from prepende_brain.private_fs import enforce_private_umask


class VaultBootstrapError(RuntimeError):
    """Base class for an initialization refusal."""


class VaultBootstrapConflictError(VaultBootstrapError):
    """Raised when initialization would overwrite existing vault content."""

    def __init__(self, destination: Path, conflicts: list[str]) -> None:
        self.destination = destination
        self.conflicts = tuple(conflicts)
        joined = ", ".join(conflicts[:5])
        if len(conflicts) > 5:
            joined += f" (+{len(conflicts) - 5} more)"
        super().__init__(
            f"vault initialization refused at {destination}: existing content "
            f"would be overwritten ({joined})"
        )


def default_template_path() -> Path:
    """Return the sanitized template shipped with the source checkout.

    ``PREPENDE_VAULT_TEMPLATE`` is an explicit packaging/container override;
    ordinary repository clones use the top-level ``vault-template`` directory.
    """

    configured = (os.environ.get("PREPENDE_VAULT_TEMPLATE") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[1] / "vault-template"


def _template_entries(template: Path) -> tuple[list[Path], list[Path]]:
    directories: list[Path] = []
    files: list[Path] = []
    for source in sorted(template.rglob("*")):
        if source.is_symlink():
            raise VaultBootstrapError(
                f"vault template contains a symbolic link and was refused: {source}"
            )
        if source.is_dir():
            directories.append(source)
        elif source.is_file():
            files.append(source)
        else:
            raise VaultBootstrapError(f"unsupported vault template entry: {source}")
    return directories, files


def _chmod_private(path: Path, mode: int) -> None:
    # A caller may deliberately place a vault at the current directory. Never
    # chmod that shared/operator-owned directory as a side effect.
    if path.is_dir() and path.resolve(strict=False) == Path.cwd().resolve(strict=False):
        return
    try:
        path.chmod(mode)
    except OSError:
        # Some mounted/container filesystems do not implement POSIX modes. The
        # privacy boundary still comes from using a caller-owned destination.
        pass


def initialize_vault(
    destination: str | os.PathLike[str],
    *,
    template_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Initialize ``destination`` from the sanitized template without overwrite.

    Existing files not owned by the template are left alone. Existing template
    files must be byte-identical; otherwise a :class:`VaultBootstrapConflictError`
    is raised before any directories or files are created.
    """

    enforce_private_umask()
    raw_destination = Path(destination).expanduser()
    if raw_destination.exists() and raw_destination.is_symlink():
        raise VaultBootstrapError(
            f"vault destination may not be a symbolic link: {raw_destination}"
        )
    destination_path = raw_destination.resolve(strict=False)
    if destination_path.exists() and not destination_path.is_dir():
        raise VaultBootstrapError(
            f"vault destination exists and is not a directory: {destination_path}"
        )

    template = Path(template_path).expanduser() if template_path else default_template_path()
    if template.is_symlink():
        raise VaultBootstrapError(f"vault template may not be a symbolic link: {template}")
    template = template.resolve(strict=False)
    if not template.is_dir():
        raise VaultBootstrapError(f"sanitized vault template not found: {template}")

    source_dirs, source_files = _template_entries(template)
    conflicts: list[str] = []
    unchanged: list[str] = []
    pending: list[tuple[Path, Path, str]] = []

    for source_dir in source_dirs:
        relative = source_dir.relative_to(template)
        target_dir = destination_path / relative
        if target_dir.exists() and (target_dir.is_symlink() or not target_dir.is_dir()):
            conflicts.append(relative.as_posix() + "/")

    for source in source_files:
        relative = source.relative_to(template)
        relative_name = relative.as_posix()
        target = destination_path / relative
        if target.exists() or target.is_symlink():
            if (
                target.is_symlink()
                or not target.is_file()
                or target.read_bytes() != source.read_bytes()
            ):
                conflicts.append(relative_name)
            else:
                unchanged.append(relative_name)
        else:
            pending.append((source, target, relative_name))

    if conflicts:
        raise VaultBootstrapConflictError(destination_path, sorted(set(conflicts)))

    destination_created = not destination_path.exists()
    destination_path.mkdir(parents=True, exist_ok=True)
    _chmod_private(destination_path, 0o700)

    for source_dir in sorted(source_dirs, key=lambda path: len(path.parts)):
        target_dir = destination_path / source_dir.relative_to(template)
        if not target_dir.exists():
            target_dir.mkdir(parents=True, exist_ok=True)
        _chmod_private(target_dir, 0o700)

    created: list[str] = []
    for source, target, relative_name in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        _chmod_private(target, 0o600)
        created.append(relative_name)

    # A byte-identical pre-existing template file is still private customer
    # state; repair permissions left by an older release.
    for relative_name in unchanged:
        _chmod_private(destination_path / relative_name, 0o600)

    return {
        "ok": True,
        "destination": str(destination_path),
        "template": str(template),
        "created": created,
        "unchanged": sorted(unchanged),
        "overwritten": [],
        "sourceDataCopied": False,
        "destinationCreated": destination_created,
    }


__all__ = [
    "VaultBootstrapConflictError",
    "VaultBootstrapError",
    "default_template_path",
    "initialize_vault",
]
