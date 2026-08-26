#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
. "$SCRIPT_DIR/common.sh"
wait_seconds=0
if [ "${1:-}" = --wait ]; then wait_seconds=${2:?missing seconds}; fi
deadline=$((SECONDS + wait_seconds))
until docker info >/dev/null 2>&1 \
  && curl -fsS "$(o2_url)/healthz" | jq -e '.status == "ok"' >/dev/null \
  && [ "$(dc ps -q syslog-ng)" ] \
  && dc ps --format json report-server | jq -e 'select(.Health == "healthy")' >/dev/null; do
  [ "$SECONDS" -lt "$deadline" ] || { echo 'Health check failed.' >&2; dc ps; exit 1; }
  sleep 3
done
dc exec -T syslog-ng syslog-ng -s -f /etc/syslog-ng/syslog-ng.conf
curl -fsS "$(o2_url)/healthz" | jq -e '.status == "ok"' >/dev/null
if ! ./scripts/storage-check.sh --check; then
  echo 'Storage threshold check failed.' >&2
  exit 1
fi
echo 'OpenObserve, syslog-ng, and Report Server are healthy.'
