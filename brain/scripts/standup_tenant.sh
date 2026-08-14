#!/usr/bin/env bash
# Stand up one tenant/workspace on an isolated Prepende brain.
#
# Generic by design: identity is runtime configuration, never baked into the
# reusable source. The two inherently manual infra steps (create the isolated
# Postgres and deploy the private MCP-HTTP cockpit) remain explicit approval
# gates; this script handles only repeatable local preparation.
#
# Customer prerequisites (see docs/TENANT-STANDUP.md):
#   - .env with DATABASE_URL pointing at THIS installation's own Postgres;
#   - migrations 019/020/021 (+026/027 for BYO) applied and the least-privilege
#     role created by a database owner. This script validates source presence
#     and forces the Postgres backend; it does not hold admin credentials.
#
# Usage:
#   scripts/standup_tenant.sh --scope <physical-scope> [--tenant <tenant>] [--workspace <workspace>] [--pack packs/small-business.json] [--backfill] [--capabilities <spec>]
#   scripts/standup_tenant.sh --scope <local-scope> [--tenant <tenant>] [--workspace <workspace>] --local-dev-sqlite
set -euo pipefail

SCOPE=""
TENANT=""
WORKSPACE=""
PACK="packs/small-business.json"
BACKFILL=0
CAPS=""
LOCAL_DEV_SQLITE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --scope) SCOPE="$2"; shift 2 ;;
    --tenant) TENANT="$2"; shift 2 ;;
    --workspace) WORKSPACE="$2"; shift 2 ;;
    --pack) PACK="$2"; shift 2 ;;
    --backfill) BACKFILL=1; shift ;;
    --capabilities) CAPS="$2"; shift 2 ;;
    --local-dev-sqlite) LOCAL_DEV_SQLITE=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
if [ -z "$SCOPE" ]; then
  echo "usage: $0 --scope <slug> [--tenant <slug>] [--workspace <slug>] [--pack <file>] [--backfill] [--capabilities <spec>] [--local-dev-sqlite]" >&2
  exit 2
fi
if [ -z "$TENANT" ]; then TENANT="$SCOPE"; fi
if [ -z "$WORKSPACE" ]; then WORKSPACE="$SCOPE"; fi

valid_slug() {
  local value="$1"
  [ -n "$value" ] && [ "${#value}" -le 64 ] && [[ "$value" =~ ^[a-z0-9][a-z0-9_-]*$ ]]
}

# Identity checks must happen before configuration is sourced or any seed,
# backfill, or token process can create partial state.
if ! valid_slug "$SCOPE"; then
  echo "INVALID --scope '$SCOPE': expected lowercase slug [a-z0-9_-], 1-64 chars; no state was written." >&2
  exit 2
fi
if ! valid_slug "$TENANT"; then
  echo "INVALID --tenant '$TENANT': expected lowercase slug [a-z0-9_-], 1-64 chars; no state was written." >&2
  exit 2
fi
if ! valid_slug "$WORKSPACE"; then
  echo "INVALID --workspace '$WORKSPACE': expected lowercase slug [a-z0-9_-], 1-64 chars; no state was written." >&2
  exit 2
fi
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
EXPECTED_SCOPE="$(
  PYTHONPATH="$ROOT_DIR" "$PY" -c \
    'from prepende_brain.identity import namespace_for_identity; import sys; print(namespace_for_identity(sys.argv[1], sys.argv[2]))' \
    "$TENANT" "$WORKSPACE"
)"
if [ "$SCOPE" != "$EXPECTED_SCOPE" ]; then
  echo "IDENTITY REFUSED: --scope must be the canonical tenant/workspace namespace '$EXPECTED_SCOPE'; no state was written." >&2
  exit 2
fi
if [ "$LOCAL_DEV_SQLITE" -eq 1 ] && [ "$BACKFILL" -eq 1 ]; then
  echo "LOCAL DEV REFUSED: --backfill requires the customer Postgres lane and cannot be combined with --local-dev-sqlite." >&2
  exit 2
fi

cd "$ROOT_DIR"
MODE="customer-postgres"
if [ "$LOCAL_DEV_SQLITE" -eq 1 ]; then MODE="local-dev-sqlite"; fi
echo "== Prepende tenant standup: tenant=$TENANT workspace=$WORKSPACE scope=$SCOPE pack=$PACK mode=$MODE =="

# All remaining identity/config checks still precede the first state write.
if [ ! -f ".env" ]; then
  echo "MISSING .env (installation configuration is required). See docs/TENANT-STANDUP.md" >&2
  exit 1
fi
if [ ! -f "$PACK" ]; then
  echo "MISSING pack $PACK (available: packs/*.json)" >&2
  exit 1
fi
# shellcheck disable=SC1091
set +u; . ./.env 2>/dev/null || true; set -u

if [ -n "${WORKSPACE_SCOPE:-}" ] && [ "$WORKSPACE_SCOPE" != "$WORKSPACE" ]; then
  echo "CUSTOMER STANDUP REFUSED: WORKSPACE_SCOPE '$WORKSPACE_SCOPE' disagrees with --workspace '$WORKSPACE'; no state was written." >&2
  exit 2
fi
export WORKSPACE_SCOPE="$WORKSPACE"

if [ "$LOCAL_DEV_SQLITE" -eq 1 ]; then
  export MEMORY_BACKEND="sqlite"
  echo "LOCAL DEVELOPMENT ONLY: forcing SQLite for tenant=$TENANT workspace=$WORKSPACE scope=$SCOPE." >&2
  echo "NOT CUSTOMER-READY: this mode proves local behavior only and must not be deployed or handed off." >&2
else
  if [ -z "${DATABASE_URL:-}" ]; then
    echo "CUSTOMER STANDUP REFUSED: DATABASE_URL is required for this installation's own Postgres; SQLite fallback is not customer-ready." >&2
    echo "Use --local-dev-sqlite only for an explicitly non-customer local fixture." >&2
    exit 1
  fi
  case "$DATABASE_URL" in
    postgres://*|postgresql://*) ;;
    *)
      echo "CUSTOMER STANDUP REFUSED: DATABASE_URL must be a Postgres URL; no state was written." >&2
      exit 1
      ;;
  esac
  case "${MEMORY_BACKEND:-auto}" in
    sqlite|SQLite|SQLITE)
      echo "CUSTOMER STANDUP REFUSED: MEMORY_BACKEND=sqlite is local-only. Configure this installation's own Postgres or use --local-dev-sqlite." >&2
      exit 1
      ;;
  esac
  # Customer provisioning must fail hard on driver, schema, or connection
  # errors instead of taking the normal resilient SQLite fallback.
  export DATABASE_URL
  export MEMORY_BACKEND="postgres"
fi

echo "-- 1/5 required migration files (source-presence check only; apply/verify the target DB as owner):"
for m in 019_engram_kernel_memory 020_engram_kernel_queues 021_kernel_scope_guards; do
  if [ -f "supabase/migrations/${m}.sql" ]; then
    echo "     present: supabase/migrations/${m}.sql"
  else
    echo "     CUSTOMER STANDUP REFUSED: missing source migration ${m}.sql; no state was written." >&2
    exit 1
  fi
done
echo "     (apply with: supabase db push OR psql \"\$DATABASE_URL\" -f supabase/migrations/<file>.sql, as the DB owner)"

echo "-- 2/5 seed tenant discipline from pack:"
"$PY" scripts/seed_tenant.py --pack "$PACK" --scope "$SCOPE"

if [ "$BACKFILL" -eq 1 ]; then
  echo "-- 3/5 backfill existing SQLite memories -> Postgres:"
  "$PY" scripts/backfill_memory_to_postgres.py
else
  echo "-- 3/5 backfill skipped (pass --backfill to migrate existing SQLite memories)"
fi

echo "-- 4/5 mint connector token:"
MINT_ARGS=(--scope "$SCOPE" --tenant "$TENANT" --workspace "$WORKSPACE")
if [ -n "$CAPS" ]; then MINT_ARGS+=(--capabilities "$CAPS"); fi
"$PY" scripts/mint_tenant_token.py "${MINT_ARGS[@]}"

if [ "$LOCAL_DEV_SQLITE" -eq 1 ]; then
  echo "-- 5/5 LOCAL DEVELOPMENT ONLY: deployment and customer handoff are intentionally not authorized."
  echo "== local SQLite fixture complete; NOT CUSTOMER-READY =="
else
  echo "-- 5/5 MANUAL: deploy the MCP-HTTP cockpit only after database-owner verification:"
  echo "     PREPENDE_MCP_TRANSPORT=http PREPENDE_MCP_HOST=0.0.0.0 with the token above in PREPENDE_TENANT_TOKENS."
  echo "     HTTP derives identity per request; PREPENDE_MCP_SCOPE is for a one-scope stdio process only."
  echo "== customer Postgres preflight passed; deployment and handoff remain manual approval gates =="
fi
