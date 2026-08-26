#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
. "$SCRIPT_DIR/common.sh"
archive=${1:?Usage: restore.sh /path/to/backup.tar.gz}
[ -f "$archive" ] || { echo 'Backup archive not found.' >&2; exit 2; }
echo "This will stop this logging platform and overlay configuration from $archive. Raw log data is not restored."
read -r -p 'Type RESTORE to continue: ' confirm
[ "$confirm" = RESTORE ] || { echo 'Cancelled.'; exit 0; }
dc down
tar -xzf "$archive" -C "$ROOT_DIR" --no-same-owner
chmod 0600 .env
./scripts/validate.sh
dc up -d
./scripts/healthcheck.sh --wait 180
