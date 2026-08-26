#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

command -v python3 >/dev/null || { echo 'python3 is required'; exit 2; }
python3 -c 'import yaml' 2>/dev/null || { echo 'python3-yaml is required'; exit 2; }
python3 scripts/render-syslog-config.py --enable-tls "${ENABLE_SYSLOG_TLS:-false}"
python3 tests/test-dashboard-definitions.py
python3 tests/test-gui-definitions.py
python3 tests/test-alert-definitions.py

for required in ZO_ROOT_USER_EMAIL ZO_ROOT_USER_PASSWORD DATA_DIR PLATFORM_TIMEZONE OPENOBSERVE_IMAGE SYSLOG_NG_IMAGE; do
  [ -n "${!required:-}" ] || { echo "Missing $required in .env" >&2; exit 2; }
done
[ "$(stat -c '%a' .env)" = 600 ] || { echo '.env must have mode 0600' >&2; exit 2; }
for required_dir in "$DATA_DIR/openobserve" "$DATA_DIR/syslog-ng/buffer" "$DATA_DIR/syslog-ng/state"; do
  [ -d "$required_dir" ] || { echo "Missing required directory: $required_dir" >&2; exit 2; }
done
dc config -q
dc run --rm --no-deps --entrypoint /usr/sbin/syslog-ng syslog-ng -s -f /etc/syslog-ng/syslog-ng.conf
if [ "${RUN_VENDOR_PARSER_TESTS:-false}" = true ]; then
  tests/test-vendor-parsers.sh
fi
echo 'Validation passed.'
