#!/usr/bin/env bash
# Exercises the durable queue without deleting data. It temporarily stops only
# this Compose project's OpenObserve container.
set -Eeuo pipefail
IFS=$'\n\t'
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
. "$SCRIPT_DIR/common.sh"
marker="buffer-test-$(date -u +%s)-$RANDOM"
./scripts/test-syslog.sh
dc stop openobserve
trap 'dc start openobserve >/dev/null 2>&1 || true' EXIT
for n in $(seq 1 20); do
  printf '<134>1 %s buffer-test logging-platform - - - %s-%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$marker" "$n" | nc -u -w 1 127.0.0.1 "${SYSLOG_UDP_PORT:-514}"
done
sleep 3
find "$DATA_DIR/syslog-ng/buffer" -type f -size +0c | grep -q . || { echo 'No syslog-ng buffer files found.' >&2; exit 1; }
dc start openobserve
./scripts/healthcheck.sh --wait 180
sleep 10
echo "Queue test complete. Search unclassified for raw_message containing: $marker"
trap - EXIT
