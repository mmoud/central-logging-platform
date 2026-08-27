#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
. "$SCRIPT_DIR/common.sh"
target=${1:-"$ROOT_DIR/backups/logging-platform-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"}
mkdir -p "$(dirname "$target")"
umask 077
# Git remains the source of truth for code, Compose, parsers and dashboard
# definitions. This protected archive carries only private/site-specific state:
# credentials, mappings, TLS material, and OpenObserve metadata when present.
platform_items=(.env config syslog-ng/tls)
openobserve_was_running=false
wait_openobserve_ready() {
  local deadline=$((SECONDS + 180))
  until curl -fsS "$(o2_url)/healthz" 2>/dev/null | jq -e '.status == "ok"' >/dev/null; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo 'OpenObserve did not become healthy after the metadata backup.' >&2
      return 1
    fi
    sleep 3
  done
}
resume_openobserve() {
  if [ "$openobserve_was_running" = true ]; then
    dc start openobserve >/dev/null
    wait_openobserve_ready
  fi
}

# Stop only OpenObserve while copying its SQLite metadata. syslog-ng remains up
# and its reliable disk buffer absorbs the brief ingestion interruption.
if [ -d "$DATA_DIR/openobserve/db" ] && \
   dc ps --status running --services 2>/dev/null | grep -Fx openobserve >/dev/null; then
  openobserve_was_running=true
  trap resume_openobserve EXIT
  dc stop openobserve >/dev/null
fi
if [ -d "$DATA_DIR/openobserve/db" ]; then
  tar -C "$ROOT_DIR" -czf "$target" "${platform_items[@]}" \
    -C "$DATA_DIR" openobserve/db
  metadata_status=included
else
  tar -C "$ROOT_DIR" -czf "$target" "${platform_items[@]}"
  metadata_status='not present'
fi
resume_openobserve
openobserve_was_running=false
trap - EXIT
chmod 0600 "$target"
echo "Configuration/metadata backup created: $target"
echo "OpenObserve metadata: $metadata_status"
echo 'It does not include raw OpenObserve stream data, WAL, or syslog-ng queued messages.'
