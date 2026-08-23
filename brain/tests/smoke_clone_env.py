"""Fresh-clone smoke: the documented .env copy boots without keys or services."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def active_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.split(" #", 1)[0].strip()
    return values


def main() -> None:
    example = ROOT / ".env.example"
    configured = active_values(example)
    assert configured["MODEL_PROVIDER"] == "echo", configured["MODEL_PROVIDER"]
    assert configured["EMBEDDING_PROVIDER"] == "", configured["EMBEDDING_PROVIDER"]
    assert configured["EMBEDDING_MODEL"] == "", configured["EMBEDDING_MODEL"]
    assert "ENGRAM_EMBEDDING_MODEL" not in configured, configured.get("ENGRAM_EMBEDDING_MODEL")
    assert configured["VAULT_PATH"] == "./prepende-data/default/vault", configured["VAULT_PATH"]

    text = example.read_text(encoding="utf-8")
    assert "#   EMBEDDING_PROVIDER=local" in text
    assert "#   EMBEDDING_MODEL=nomic-embed-text" in text
    assert "#   EMBEDDING_DIM=768" in text

    with tempfile.TemporaryDirectory(prefix="prepende_clone_env_") as raw_tmp:
        tmp = Path(raw_tmp)
        (tmp / "home").mkdir()
        env_file = tmp / ".env"
        env_file.write_bytes(example.read_bytes())
        env_file.chmod(0o600)
        if os.name == "posix":
            assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
        probe = """
import json
from kernel.core.brain import build_brain
loop, cfg, gateway = build_brain(memory_policy="candidate")
print(json.dumps({
    "provider": cfg.provider,
    "embeddingProvider": cfg.embedding_provider,
    "embeddingModel": cfg.embedding_model,
    "gateway": gateway.name,
    "vaultPath": cfg.vault,
    "vaultExists": loop.knowledge.root.is_dir(),
    "indexExists": loop.knowledge.rag.path,
}))
"""
        env = {
            "HOME": str(tmp / "home"),
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        proc = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=tmp,
            env=env,
            text=True,
            capture_output=True,
            timeout=20,
        )
        assert proc.returncode == 0, proc.stderr or proc.stdout
        receipt = json.loads(proc.stdout)
        assert receipt["provider"] == receipt["gateway"] == "echo", receipt
        assert receipt["embeddingProvider"] == "" and receipt["embeddingModel"] == "", receipt
        assert receipt["vaultPath"] == "./prepende-data/default/vault", receipt
        assert receipt["vaultExists"] is True, receipt
        assert (tmp / "prepende-data" / "default" / "vault" / "wiki").is_dir(), receipt
        assert Path(receipt["indexExists"]).name == "vault_index.db", receipt

    print("CLONE ENV SMOKE: OK — private .env boots echo + lexical RAG with zero credentials")


if __name__ == "__main__":
    main()
