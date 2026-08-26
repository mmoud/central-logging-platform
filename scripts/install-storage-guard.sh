#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
ROOT_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd -P)
[ "${EUID}" -eq 0 ] || { echo 'Run with sudo/root.' >&2; exit 2; }
[ -f "$ROOT_DIR/.env" ] || { echo 'Missing .env.' >&2; exit 2; }

ensure_setting() {
  local key=$1 value=$2
  if ! grep -q "^${key}=" "$ROOT_DIR/.env"; then
    printf '%s=%s\n' "$key" "$value" >>"$ROOT_DIR/.env"
  fi
}
ensure_setting STORAGE_WARNING_PERCENT 75
ensure_setting STORAGE_CRITICAL_PERCENT 85
chmod 0600 "$ROOT_DIR/.env"

# shellcheck disable=SC1091
set -a; . "$ROOT_DIR/.env"; set +a
: "${DATA_DIR:?DATA_DIR must be configured in .env}"

escape_sed_replacement() { printf '%s' "$1" | sed 's/[&|]/\\&/g'; }
platform_escaped=$(escape_sed_replacement "$ROOT_DIR")
data_escaped=$(escape_sed_replacement "$DATA_DIR")
sed -e "s|@PLATFORM_DIR@|$platform_escaped|g" \
    -e "s|@DATA_DIR@|$data_escaped|g" \
    "$ROOT_DIR/systemd/logging-platform-storage-check.service.in" \
    >/etc/systemd/system/logging-platform-storage-check.service
chmod 0644 /etc/systemd/system/logging-platform-storage-check.service
install -m 0644 "$ROOT_DIR/systemd/logging-platform-storage-check.timer" \
  /etc/systemd/system/logging-platform-storage-check.timer

systemctl daemon-reload
systemctl enable --now logging-platform-storage-check.timer
systemctl start logging-platform-storage-check.service
printf 'Storage guard installed: checks every five minutes; warning %s%%, critical %s%%.\n' \
  "${STORAGE_WARNING_PERCENT:-75}" "${STORAGE_CRITICAL_PERCENT:-85}"
