"""Fresh-vault smoke: sanitized initialization is useful and non-destructive."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from knowledge.bootstrap import (  # noqa: E402
    VaultBootstrapConflictError,
    initialize_vault,
)
from knowledge.rag import VaultRagIndex  # noqa: E402


BANNED_CORPUS_MARKERS = (
    "ry" + "an",
    "living" + "ston",
    "stel" + "la",
    "morning " + "paiper",
)


async def prove_lexical_rag(vault: Path, state: Path) -> None:
    index = VaultRagIndex(str(vault), index_path=str(state / "vault_index.db"))

    # Prove the exact documented first-light path before the owner adds a note.
    stats = await index.rebuild()
    assert stats["files"] == 1 and stats["chunks"] >= 1, stats
    bootstrap_hits = await index.search("bootstrap verification")
    assert bootstrap_hits and bootstrap_hits[0]["page"] == "bootstrap-verification", bootstrap_hits

    note = vault / "wiki" / "company-handbook.md"
    note.write_text(
        "# Company handbook\n\nThe aurora-lantern protocol is the verified clone bootstrap fact.\n",
        encoding="utf-8",
    )
    stats = await index.rebuild()
    assert stats["files"] == 2 and stats["chunks"] >= 2, stats
    hits = await index.search("aurora lantern protocol")
    assert hits and hits[0]["page"] == "company-handbook", hits


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="prepende_clone_bootstrap_") as raw_tmp:
        tmp = Path(raw_tmp)
        vault = tmp / "private-company-vault"
        receipt = initialize_vault(vault)
        assert receipt["ok"] is True and receipt["sourceDataCopied"] is False, receipt
        assert receipt["destinationCreated"] is True and receipt["overwritten"] == [], receipt
        assert (vault / "SCHEMA.md").is_file()
        assert (vault / "raw").is_dir() and (vault / "wiki").is_dir()
        assert (vault / ".obsidian" / "app.json").is_file()
        assert (vault / "_TEMPLATES" / "source-review-note.md").is_file()
        assert (vault / "wiki" / "bootstrap-verification.md").is_file()

        template_text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in vault.rglob("*")
            if path.is_file()
        ).lower()
        for marker in BANNED_CORPUS_MARKERS:
            assert marker not in template_text, f"private corpus marker in clone template: {marker}"

        state = tmp / "state"
        state.mkdir()
        asyncio.run(prove_lexical_rag(vault, state))

        # Re-running initialization accepts identical scaffold files and leaves
        # the caller's newly-added company note untouched.
        second = initialize_vault(vault)
        assert second["created"] == [] and second["overwritten"] == [], second
        assert (vault / "wiki" / "company-handbook.md").is_file()

        # Atomic refusal: one conflicting template file prevents even a missing
        # template file from being recreated, and existing content is preserved.
        private_index = "# Existing private index\n"
        (vault / "index.md").write_text(private_index, encoding="utf-8")
        (vault / "log.md").unlink()
        try:
            initialize_vault(vault)
            raise AssertionError("conflicting vault initialization should be refused")
        except VaultBootstrapConflictError as exc:
            assert "index.md" in exc.conflicts, exc.conflicts
        assert (vault / "index.md").read_text(encoding="utf-8") == private_index
        assert not (vault / "log.md").exists(), "bootstrap wrote partially before conflict refusal"
        assert (vault / "wiki" / "company-handbook.md").is_file()

    print("CLONE BOOTSTRAP SMOKE: OK — sanitized, lexical-ready, idempotent, no overwrite")


if __name__ == "__main__":
    main()
