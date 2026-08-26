#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
. "$SCRIPT_DIR/common.sh"
printf 'Logging Platform Status\n\n'
for service in openobserve syslog-ng report-server; do
  state=$(dc ps --format json "$service" 2>/dev/null | jq -r '.Health // .State // "NOT RUNNING"' 2>/dev/null || true)
  [ -n "$state" ] || state='NOT ENABLED'
  printf '%-18s %s\n' "$service" "$state"
done
printf '\nDisk (%s)\n' "$DATA_DIR"
df -h "$DATA_DIR" | awk 'NR==2 {printf "Used %-12s Free %s\\n", $3, $4}'
printf '\nOpenObserve retention  %s days\n' "$ZO_COMPACT_DATA_RETENTION_DAYS"
for item in 'UDP/514 udp' 'TCP/514 tcp' 'TLS/6514 tls'; do
  set -- $item
  if ss -H -ln"$2" "( sport = :${1#*/} )" 2>/dev/null | grep -q .; then printf '%-18s LISTENING\n' "$1"; else printf '%-18s NOT LISTENING\n' "$1"; fi
done
