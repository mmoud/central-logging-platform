#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
. "$SCRIPT_DIR/common.sh"
target=${1:-"$ROOT_DIR/backups/logging-platform-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"}
mkdir -p "$(dirname "$target")"
umask 077
# This intentionally excludes OpenObserve's raw stream data and WAL. It captures
# deployment configuration plus local metadata needed to recover dashboards.
tar -C "$ROOT_DIR" -czf "$target" .env docker-compose.yml config syslog-ng scripts docs README.md \
  -C "$DATA_DIR" openobserve/db 2>/dev/null || tar -C "$ROOT_DIR" -czf "$target" .env docker-compose.yml config syslog-ng scripts docs README.md
chmod 0600 "$target"
echo "Configuration/metadata backup created: $target"
echo 'It does not include raw OpenObserve stream data, WAL, or syslog-ng queued messages.'
