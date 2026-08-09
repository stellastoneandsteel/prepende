"""Private-by-default filesystem helpers for local Prepende state.

Prepende's local SQLite databases, vaults, locks, and receipts can contain
customer material.  The CLI establishes a process-wide ``077`` umask before
any runtime import, while these helpers also repair existing state paths that
may have been created by an older, permissive release.

Only paths explicitly passed by a Prepende state component are modified.  We
never walk upward and chmod an operator-owned parent such as ``$HOME``.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Iterable


PRIVATE_UMASK = 0o077
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def enforce_private_umask() -> None:
    """Make subsequently created local state owner-only."""

    if os.name == "posix":
        os.umask(PRIVATE_UMASK)


def secure_directory(
    path: str | os.PathLike[str], *, repair_existing: bool = False,
) -> Path:
    """Create one state directory privately without changing shared parents.

    Existing directories are left at their current mode unless the caller
    explicitly identifies the directory as Prepende-owned.  Repairs refuse
    obvious shared roots so a configuration mistake cannot chmod ``.``, a
    home directory, or the filesystem root.
    """

    enforce_private_umask()
    target = Path(path)
    try:
        info = target.lstat()
    except FileNotFoundError:
        info = None
    if info is not None and (
        stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)
    ):
        raise RuntimeError(f"refusing non-directory Prepende state path: {target}")
    existed = info is not None
    target.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)
    if repair_existing:
        resolved = target.resolve()
        forbidden = {
            Path(resolved.anchor),
            Path.cwd().resolve(),
            Path.home().resolve(),
        }
        if resolved in forbidden or (resolved / ".git").exists():
            raise RuntimeError(f"refusing to chmod shared directory as Prepende state: {target}")
    if os.name == "posix" and (not existed or repair_existing):
        target.chmod(PRIVATE_DIR_MODE)
    return target


def secure_file(path: str | os.PathLike[str], *, required: bool = False) -> Path:
    """Tighten one regular private file to ``0600`` when it exists."""

    enforce_private_umask()
    target = Path(path)
    try:
        info = target.lstat()
    except FileNotFoundError:
        if required:
            raise
        return target
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"refusing non-regular Prepende state file: {target}")
    if os.name == "posix":
        target.chmod(PRIVATE_FILE_MODE)
    return target


def sqlite_artifacts(path: str | os.PathLike[str]) -> tuple[Path, ...]:
    database = Path(path)
    return (
        database,
        Path(f"{database}-wal"),
        Path(f"{database}-shm"),
        Path(f"{database}-journal"),
    )


def prepare_private_sqlite(path: str | os.PathLike[str]) -> Path:
    """Prepare a SQLite location and tighten any database/sidecar artifacts.

    Calling this before opening a connection establishes ``077`` for sidecars
    that SQLite creates later.  Calling it after PRAGMA setup repairs artifacts
    created during connection initialization.
    """

    database = Path(path)
    enforce_private_umask()
    parent = database.parent
    # The database path can be just ``memory.db``. In that case its parent is
    # the caller's current working directory, which is never Prepende-owned.
    # Likewise, an existing absolute parent may be a shared directory such as
    # /tmp. Only a missing parent is unambiguously being created for this
    # database; explicit state owners (vault/workspace/etc.) tighten their own
    # directories with secure_directory().
    if not parent.exists():
        secure_directory(parent)
    for artifact in sqlite_artifacts(database):
        secure_file(artifact)
    return database


def secure_private_files(paths: Iterable[str | os.PathLike[str]]) -> None:
    for path in paths:
        secure_file(path)


def prepare_private_file(
    path: str | os.PathLike[str], *, repair_parent: bool = False,
) -> Path:
    """Prepare one local state file without following a final symlink."""

    enforce_private_umask()
    target = Path(path)
    secure_directory(target.parent, repair_existing=repair_parent)
    secure_file(target)
    return target


def append_private_text(
    path: str | os.PathLike[str],
    value: str,
    *,
    repair_parent: bool = False,
) -> Path:
    """Append UTF-8 text through an owner-only, no-follow descriptor."""

    target = prepare_private_file(path, repair_parent=repair_parent)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(target, flags, PRIVATE_FILE_MODE)
    try:
        if os.name == "posix":
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    secure_file(target, required=True)
    return target


def write_private_text(
    path: str | os.PathLike[str],
    value: str,
    *,
    repair_parent: bool = False,
) -> Path:
    """Atomically replace one UTF-8 state file with mode ``0600``."""

    target = prepare_private_file(path, repair_parent=repair_parent)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    temporary_path = Path(temporary)
    try:
        if os.name == "posix":
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
        secure_file(target, required=True)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
    return target


def secure_private_tree(path: str | os.PathLike[str]) -> Path:
    """Repair an explicitly owned state tree to 0700 directories/0600 files."""

    root = secure_directory(path, repair_existing=True)
    for item in root.rglob("*"):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise RuntimeError(f"refusing symlink inside Prepende state tree: {item}")
        if stat.S_ISDIR(info.st_mode):
            secure_directory(item, repair_existing=True)
        elif stat.S_ISREG(info.st_mode):
            secure_file(item, required=True)
        else:
            raise RuntimeError(f"refusing special file inside Prepende state tree: {item}")
    return root
