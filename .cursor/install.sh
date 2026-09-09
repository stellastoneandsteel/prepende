#!/usr/bin/env bash
# Idempotent Cloud Agent / local install for Prepende Protocol.
# Stock Cursor Cloud images ship Python 3.12 without ensurepip/python3-venv,
# so `python3 -m venv` fails until those packages are installed.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

log() {
  printf 'prepende-install: %s\n' "$*"
}

can_create_venv() {
  local tmp
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/prepende-venv-check.XXXXXX")"
  if python3 -m venv "$tmp/v" >/dev/null 2>&1 && [[ -x "$tmp/v/bin/python" ]]; then
    rm -rf "$tmp"
    return 0
  fi
  rm -rf "$tmp"
  return 1
}

install_venv_packages() {
  local -a pkgs=(python3-venv python3-pip)
  if ! command -v apt-get >/dev/null 2>&1; then
    log "python3 -m venv is unusable and apt-get is not available"
    return 1
  fi
  log "installing ${pkgs[*]} (stock image is missing ensurepip/python3-venv)"
  if command -v sudo >/dev/null 2>&1; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get update
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${pkgs[@]}"
  else
    DEBIAN_FRONTEND=noninteractive apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${pkgs[@]}"
  fi
}

if ! can_create_venv; then
  install_venv_packages
  if ! can_create_venv; then
    log "refusing to continue: python3 -m venv is still broken after installing python3-venv/python3-pip"
    exit 1
  fi
fi

if [[ ! -x "$root/.venv/bin/python" ]]; then
  log "creating $root/.venv"
  python3 -m venv "$root/.venv"
fi

if ! "$root/.venv/bin/python" -c 'import pip' >/dev/null 2>&1; then
  log "recreating broken $root/.venv"
  python3 -m venv --clear "$root/.venv"
fi

log "editable install with signatures+test extras and numpy"
"$root/.venv/bin/python" -m pip install -U pip
"$root/.venv/bin/python" -m pip install -e '.[signatures,test]' numpy

# Debian/Ubuntu Cloud images mark the system interpreter as externally managed.
# Installing there as well keeps `python3 -m prepende` working after snapshot,
# because Builds do not persist shell PATH exports.
if [[ -f /etc/debian_version ]]; then
  log "also installing into the system interpreter so python3 -m prepende works without PATH hooks"
  python3 -m pip install --break-system-packages -U pip
  python3 -m pip install --break-system-packages -e '.[signatures,test]' numpy
fi

log "ok python=$("$root/.venv/bin/python" -c 'import sys; print(sys.version.split()[0])')"
"$root/.venv/bin/python" -c 'import prepende, cryptography, jsonschema, numpy; print("prepende", prepende.__version__)'
