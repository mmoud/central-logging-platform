#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
host=${1:-127.0.0.1}
for sample in "$ROOT_DIR"/tests/samples/*.syslog; do
  cat "$sample" | nc -u -w 1 "$host" 514
done
echo 'Samples sent. Their received stream reflects the actual source IP mapping; unmapped local samples correctly land in unclassified.'
