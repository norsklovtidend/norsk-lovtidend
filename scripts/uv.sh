#!/usr/bin/env bash
set -euo pipefail

# Run uv while ignoring any unrelated virtual environment that might be active
# in the current shell session. This prevents uv from warning about
# VIRTUAL_ENV mismatches when switching between multiple projects.

ROOT_DIR=$(cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

if [[ -n "${VIRTUAL_ENV:-}" && "${VIRTUAL_ENV}" != "${ROOT_DIR}/.venv" ]]; then
    echo "info: ignoring previously active virtual environment at ${VIRTUAL_ENV}" >&2
fi

unset VIRTUAL_ENV
unset VIRTUAL_ENV_PROMPT

if [[ -z "${UV_CACHE_DIR:-}" ]]; then
    export UV_CACHE_DIR="${ROOT_DIR}/.uv-cache"
fi

exec uv "$@"
