#!/bin/sh
# Establish owner-only runtime state before the MCP module imports the brain.
set -eu

umask 077

app_root="${PREPENDE_APP_ROOT:-/app}"

private_tree() {
    path="$1"
    if [ -L "$path" ]; then
        echo "Prepende startup refused: symlinked private state root" >&2
        exit 2
    fi
    if [ -e "$path" ] && [ ! -d "$path" ]; then
        echo "Prepende startup refused: private state root is not a directory" >&2
        exit 2
    fi
    mkdir -p "$path"
    if [ -n "$(find "$path" -xdev -type l -print -quit)" ]; then
        echo "Prepende startup refused: symlink inside private state" >&2
        exit 2
    fi
    if [ -n "$(find "$path" -xdev ! -type d ! -type f -print -quit)" ]; then
        echo "Prepende startup refused: special file inside private state" >&2
        exit 2
    fi
    find "$path" -xdev -type d -exec chmod 700 {} +
    find "$path" -xdev -type f -exec chmod 600 {} +
}

# The image contains a pre-populated /app/vault; mounted state may also have
# been created by an older release. Repair both it and every default runtime
# root before starting either HTTP or stdio MCP.
private_tree "$app_root/vault"
private_tree "$app_root/prepende-data"
private_tree "$app_root/.engram"
private_tree "$app_root/.workspaces"
private_tree "$app_root/prompts/store"

env_file="$app_root/.env"
if [ -L "$env_file" ]; then
    echo "Prepende startup refused: symlinked environment file" >&2
    exit 2
fi
if [ -e "$env_file" ]; then
    if [ ! -f "$env_file" ]; then
        echo "Prepende startup refused: environment path is not a file" >&2
        exit 2
    fi
    chmod 600 "$env_file"
fi

exec "$@"
