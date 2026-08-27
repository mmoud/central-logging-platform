#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
. "$SCRIPT_DIR/common.sh"
archive=${1:?Usage: restore.sh /path/to/backup.tar.gz}
[ -f "$archive" ] || { echo 'Backup archive not found.' >&2; exit 2; }
tar -tzf "$archive" >/dev/null || { echo 'Backup archive is not a readable gzip tar archive.' >&2; exit 2; }
for required in .env config/sources.yml; do
  tar -tzf "$archive" | grep -Fx "$required" >/dev/null || {
    echo "Backup archive is missing required member: $required" >&2
    exit 2
  }
done

echo "This will stop this logging platform and restore private configuration from $archive. Raw log data is not restored."
read -r -p 'Type RESTORE to continue: ' confirm
[ "$confirm" = RESTORE ] || { echo 'Cancelled.'; exit 0; }

stamp=$(date -u +%Y%m%dT%H%M%SZ)
safety_archive="$ROOT_DIR/backups/pre-restore-$stamp.tar.gz"
./scripts/backup.sh "$safety_archive"

dc down

# Extract only supported site-state members. Code, Compose, parser definitions,
# tests, documentation and dashboards always come from the current Git checkout.
platform_members=(.env config)
if tar -tzf "$archive" | grep -E '^syslog-ng/tls(/|$)' >/dev/null; then
  platform_members+=(syslog-ng/tls)
fi
tar -xzf "$archive" -C "$ROOT_DIR" --no-same-owner "${platform_members[@]}"
chmod 0600 "$ROOT_DIR/.env"

metadata_previous=''
if tar -tzf "$archive" | grep -E '^openobserve/db(/|$)' >/dev/null; then
  install -d -m 0750 "$DATA_DIR/openobserve"
  if [ -e "$DATA_DIR/openobserve/db" ]; then
    metadata_previous="$DATA_DIR/openobserve/db.pre-restore-$stamp"
    mv "$DATA_DIR/openobserve/db" "$metadata_previous"
  fi
  if ! tar -xzf "$archive" -C "$DATA_DIR" --no-same-owner openobserve/db; then
    if [ -n "$metadata_previous" ] && [ -e "$metadata_previous" ]; then
      mv "$metadata_previous" "$DATA_DIR/openobserve/db"
    fi
    echo 'OpenObserve metadata extraction failed; the previous metadata was restored.' >&2
    exit 2
  fi
fi

./scripts/validate.sh
if [ "${EUID}" -eq 0 ]; then
  ./scripts/install-storage-guard.sh
fi
dc up -d
./scripts/healthcheck.sh --wait 180
./scripts/provision-dashboards.py
./scripts/provision-gui.py --skip-query-validation

echo "Restore complete. Pre-restore safety backup: $safety_archive"
if [ -n "$metadata_previous" ]; then
  echo "Previous OpenObserve metadata retained at: $metadata_previous"
fi
