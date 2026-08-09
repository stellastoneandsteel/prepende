"""Smoke: per-tenant model routing (BYO-brain Phase 2).

Asserts the routing decision (shared vs byo), the hosted refusal of local/cli, the
fail-loud on a missing/unreadable key (no shared fallback), brain_source labels,
the DB-backed resolver via injected fake stores, and no key leak. Pure (no DB).
Run: MODEL_PROVIDER=echo python3 tests/smoke_model_route.py
"""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ENGRAM_KEY_VAULT_MASTER_KEY"] = "test-master-key-" + "x" * 40

from kernel.core import keyvault
from kernel.core.tenant_runtime import (
    ModelChoice, gateway_for_choice, brain_source, resolve_tenant_gateway,
    TenantBrainError, BYO_SECRET_PURPOSE,
)


class SharedGW:
    name = "shared-echo"


shared = SharedGW()

# --- gateway_for_choice (pure) ---
assert gateway_for_choice(ModelChoice("shared"), None, shared) is shared
gw = gateway_for_choice(ModelChoice("byo_model", "openai", "gpt-4"), "sk-fake-test", shared)
assert getattr(gw, "name", "") == "openai" and gw is not shared
print("✓ shared->shared; byo openai->a distinct tenant gateway")

for bad in (ModelChoice("byo_model", "local"), ModelChoice("byo_model", "cli-claude")):
    try:
        gateway_for_choice(bad, "k", shared)
        raise AssertionError("hosted must refuse " + bad.provider)
    except TenantBrainError as e:
        assert e.reason == "provider_not_allowed"
print("✓ hosted refuses local / cli-* providers")

try:
    gateway_for_choice(ModelChoice("byo_model", "anthropic"), None, shared)
    raise AssertionError("missing key must fail loud")
except TenantBrainError as e:
    assert e.reason == "byo_key_missing"
print("✓ byo with no key -> fail loud (byo_key_missing)")

try:
    gateway_for_choice(ModelChoice("external_brain"), None, shared)
    raise AssertionError("external_brain not in this phase")
except TenantBrainError:
    pass
print("✓ external_brain -> error (Phase 4)")

assert brain_source(ModelChoice("shared")) == "shared"
assert brain_source(ModelChoice("byo_model", "openai")) == "byo:openai"
print("✓ brain_source labels")


# --- resolve_tenant_gateway with injected fake stores (no DB) ---
class FakeStore:
    def __init__(self, row): self.row = row
    async def get(self, scope): return self.row


class FakeSecrets:
    def __init__(self, cipher): self.cipher = cipher
    async def get_cipher(self, scope, purpose): return self.cipher


FAKEKEY = "sk-ant-RESOLVEFAKE" + "Z" * 30


async def run():
    gw0, src0 = await resolve_tenant_gateway(
        "t-shared", shared, store=FakeStore({"mode": "shared"}), secrets=FakeSecrets(None))
    assert gw0 is shared and src0 == "shared"

    ct = keyvault.seal("t-byo", BYO_SECRET_PURPOSE, FAKEKEY)
    gw1, src1 = await resolve_tenant_gateway(
        "t-byo", shared,
        store=FakeStore({"mode": "byo_model", "provider": "openai", "model_id": "gpt-4", "secret_purpose": BYO_SECRET_PURPOSE}),
        secrets=FakeSecrets(ct))
    assert getattr(gw1, "name", "") == "openai" and src1 == "byo:openai" and gw1 is not shared

    try:
        await resolve_tenant_gateway(
            "t-missing", shared,
            store=FakeStore({"mode": "byo_model", "provider": "openai"}), secrets=FakeSecrets(None))
        raise AssertionError("missing key must fail loud, never shared fallback")
    except TenantBrainError as e:
        assert e.reason == "byo_key_missing"


asyncio.run(run())
print("✓ resolve_tenant_gateway: shared / byo-valid / byo-missing fails loud (no shared fallback)")

# --- no-leak: a describe-style routing record carries no key ---
rec = {"mode": "byo_model", "provider": "openai", "model": "gpt-4",
       "fingerprint": keyvault.fingerprint(FAKEKEY, "openai")}
blob = json.dumps(rec)
assert FAKEKEY not in blob and not keyvault.is_key_shaped(blob)
print("✓ no-leak: routing/describe record carries no key")

# --- end-to-end loop binding: the selected tactic must call the BYO gateway ---
from interface import prepende_runtime as v1_api  # noqa: E402
from kernel.core.strategist import RulesStrategist  # noqa: E402
from kernel.core.types import Goal  # noqa: E402


class TenantGW:
    name = "tenant-byo"


tenant_gateway = TenantGW()
base_loop = SimpleNamespace(
    gateway=shared,
    strategist=RulesStrategist(shared),
    workspace=object(),
    memory=None,
    connectors=None,
    runs=None,
    knowledge=None,
    verifier=None,
)
v1_api._loop = base_loop
tenant_loop = v1_api._tenant_loop("t-byo", tenant_gateway)
choice = asyncio.run(tenant_loop.strategist.choose(
    Goal(text="Build a detailed multi-step launch roadmap with research, verification, and implementation milestones"),
    {},
))
assert tenant_loop.gateway is tenant_gateway
assert getattr(choice.tactic, "gateway", None) is tenant_gateway, (
    "BYO loop named the tenant gateway but tactic retained the shared gateway"
)
assert tenant_loop.graphify is None, "repository Graphify projection must not enter tenant loops"
print("✓ BYO loop binds the tenant gateway into the actual selected tactic")

print("\nsmoke_model_route OK")
