#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
. "$SCRIPT_DIR/common.sh"
stream=${1:-unclassified}
case "$stream" in fortigate|cisco|juniper|proxmox_ve|proxmox_mail_gateway|linux|unclassified) ;; *) echo 'Unknown stream' >&2; exit 2;; esac
marker="direct-ingestion-$(date -u +%s)-$RANDOM"
payload=$(jq -nc --arg m "$marker" '[{"@timestamp": (now | strftime("%Y-%m-%dT%H:%M:%SZ")), "message": $m, "raw_message": $m, "source.ip":"127.0.0.1", "stream":"'$stream'"}]')
curl -fsS -u "$ZO_ROOT_USER_EMAIL:$ZO_ROOT_USER_PASSWORD" -H 'Content-Type: application/json' \
  --data "$payload" "$(o2_url)/api/$ZO_ORG/$stream/_json" | jq -e '.code == 200' >/dev/null
echo "Direct JSON ingestion succeeded for stream $stream ($marker)."
