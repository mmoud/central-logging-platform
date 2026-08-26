#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
ROOT_DIR=$(cd "$SCRIPT_DIR/.." && pwd -P)
cd "$ROOT_DIR"
mkdir -p syslog-ng/tls/ca
if [ -e syslog-ng/tls/server.key ] && [ "${1:-}" != --replace ]; then
  echo 'Refusing to replace an existing key. Use --replace only after backing it up.' >&2; exit 2
fi
collector_name=${SYSLOG_CERT_CN:-$(hostname -f 2>/dev/null || hostname)}
openssl req -x509 -newkey rsa:4096 -nodes -days 825 -sha256 \
  -keyout syslog-ng/tls/ca/ca.key -out syslog-ng/tls/ca/ca.crt -subj "/CN=Logging Platform Test CA"
openssl req -newkey rsa:4096 -nodes -keyout syslog-ng/tls/server.key -out syslog-ng/tls/server.csr -subj "/CN=$collector_name"
openssl x509 -req -in syslog-ng/tls/server.csr -CA syslog-ng/tls/ca/ca.crt -CAkey syslog-ng/tls/ca/ca.key -CAcreateserial \
  -out syslog-ng/tls/server.crt -days 825 -sha256
rm -f syslog-ng/tls/server.csr syslog-ng/tls/ca/ca.srl
chmod 0600 syslog-ng/tls/server.key syslog-ng/tls/ca/ca.key
chmod 0644 syslog-ng/tls/server.crt syslog-ng/tls/ca/ca.crt
echo 'Test CA and collector certificate generated. Set ENABLE_SYSLOG_TLS=true in .env, then run ./scripts/validate.sh and docker compose up -d syslog-ng.'
