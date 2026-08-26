#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
. "$SCRIPT_DIR/common.sh"
host=${1:-127.0.0.1}
marker="syslog-test-$(date -u +%s)-$RANDOM"
printf '<134>1 %s test-host logging-platform - - - %s udp\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$marker" | nc -u -w 1 "$host" "${SYSLOG_UDP_PORT:-514}"
printf '<134>1 %s test-host logging-platform - - - %s tcp\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$marker" | nc -w 3 "$host" "${SYSLOG_TCP_PORT:-514}"
if [ "${ENABLE_SYSLOG_TLS:-false}" = true ]; then
  printf '<134>1 %s test-host logging-platform - - - %s tls\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$marker" | \
    openssl s_client -quiet -connect "$host:${SYSLOG_TLS_PORT:-6514}" -CAfile syslog-ng/tls/ca/ca.crt >/dev/null
fi
sleep 3
./scripts/healthcheck.sh
echo "UDP and TCP syslog sent successfully. Search unclassified for raw_message containing: $marker"
