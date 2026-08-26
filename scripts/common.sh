#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$ROOT_DIR"
[ -f .env ] || { printf 'ERROR: .env is absent. Run sudo ./install.sh first.\n' >&2; exit 2; }
# The file is created root-only by install.sh and intentionally contains Compose
# environment assignments, not untrusted input.
# shellcheck disable=SC1091
set -a; . ./.env; set +a
dc() { docker compose "$@"; }
o2_url() { printf 'http://127.0.0.1:%s' "${OPENOBSERVE_HOST_PORT:-5080}"; }
