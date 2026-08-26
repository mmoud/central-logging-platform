#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
. "$SCRIPT_DIR/common.sh"
printf 'Logging Platform Status\n\n'
for service in openobserve syslog-ng report-server; do
  state=$(dc ps --format json "$service" 2>/dev/null | jq -r '
    if (.Health // "") != "" then .Health
    elif (.State // "") != "" then .State
    else "NOT RUNNING"
    end
  ' 2>/dev/null || true)
  if [ "$service" = openobserve ] && curl -fsS "$(o2_url)/healthz" | jq -e '.status == "ok"' >/dev/null 2>&1; then
    state=HEALTHY
  fi
  [ -n "$state" ] || state='NOT ENABLED'
  printf '%-18s %s\n' "$service" "$state"
done
printf '\nDisk (%s)\n' "$DATA_DIR"
df -h "$DATA_DIR" | awk 'NR==2 {printf "Used %-12s Free %s\n", $3, $4}'
printf '\nOpenObserve retention  %s days\n' "$ZO_COMPACT_DATA_RETENTION_DAYS"
listener_status() {
  local label=$1 options=$2 port=$3
  if ss -H "$options" "( sport = :$port )" 2>/dev/null | grep -q .; then
    printf '%-18s LISTENING\n' "$label"
  else
    printf '%-18s NOT LISTENING\n' "$label"
  fi
}

listener_status 'UDP/514' '-lnu' '514'
listener_status 'TCP/514' '-lnt' '514'
if [ "${ENABLE_SYSLOG_TLS:-false}" = true ]; then
  listener_status 'TLS/6514' '-lnt' '6514'
else
  printf '%-18s DISABLED\n' 'TLS/6514'
fi
