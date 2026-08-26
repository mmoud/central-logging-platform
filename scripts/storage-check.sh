#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
ROOT_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd -P)
ENV_FILE=${STORAGE_CHECK_ENV_FILE:-"$ROOT_DIR/.env"}

usage() {
  cat <<'EOF'
Usage: ./scripts/storage-check.sh [--check | --status | --timer]

  --check   Print status and return 1 at warning or 2 at critical (default).
  --status  Print status and always return 0 when the check could be performed.
  --timer   Record state transitions in syslog; return nonzero only at critical.

This check never deletes data or changes retention.
EOF
}

mode=check
case "${1:---check}" in
  --check) mode=check ;;
  --status) mode=status ;;
  --timer) mode=timer ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || { usage >&2; exit 2; }

[ -f "$ENV_FILE" ] || {
  printf 'ERROR: environment file is absent: %s\n' "$ENV_FILE" >&2
  exit 2
}
# This root-owned file contains deployment assignments rather than untrusted
# shell content. The optional override exists only for the isolated test.
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

: "${DATA_DIR:?DATA_DIR must be configured in .env}"
warning_percent=${STORAGE_WARNING_PERCENT:-75}
critical_percent=${STORAGE_CRITICAL_PERCENT:-85}

[[ "$warning_percent" =~ ^[0-9]+$ ]] || {
  echo 'STORAGE_WARNING_PERCENT must be an integer.' >&2; exit 2;
}
[[ "$critical_percent" =~ ^[0-9]+$ ]] || {
  echo 'STORAGE_CRITICAL_PERCENT must be an integer.' >&2; exit 2;
}
(( warning_percent >= 1 && critical_percent <= 99 && warning_percent < critical_percent )) || {
  echo 'Storage thresholds must satisfy: 1 <= warning < critical <= 99.' >&2
  exit 2
}
[ -d "$DATA_DIR" ] || { printf 'Data directory is absent: %s\n' "$DATA_DIR" >&2; exit 2; }

IFS=' ' read -r used_h free_h used_percent mount_point < <(
  df -hP "$DATA_DIR" | awk 'NR == 2 { value=$5; gsub(/%/, "", value); print $3, $4, value, $6 }'
)

if [ "${STORAGE_CHECK_TEST_MODE:-false}" = true ]; then
  used_percent=${STORAGE_CHECK_PERCENT_OVERRIDE:?test percentage override is required}
fi
[[ "$used_percent" =~ ^[0-9]+$ ]] || { echo 'Could not determine disk usage.' >&2; exit 2; }

state=OK
exit_code=0
priority=daemon.notice
if (( used_percent >= critical_percent )); then
  state=CRITICAL
  exit_code=2
  priority=daemon.crit
elif (( used_percent >= warning_percent )); then
  state=WARNING
  exit_code=1
  priority=daemon.warning
fi

message="Storage $state: ${used_percent}% used at $mount_point ($used_h used, $free_h free); warning=${warning_percent}%, critical=${critical_percent}%; automatic deletion is disabled"
printf '%s\n' "$message"

if [ "$mode" = timer ]; then
  state_dir="$DATA_DIR/.logging-platform-state"
  state_file="$state_dir/storage-status"
  install -d -m 0750 "$state_dir"
  previous_state=UNKNOWN
  if [ -r "$state_file" ]; then
    read -r previous_state <"$state_file" || previous_state=UNKNOWN
  fi
  if [ "$previous_state" != "$state" ]; then
    if command -v logger >/dev/null 2>&1; then
      if ! logger -p "$priority" -t logging-platform-storage -- "$message"; then
        echo 'WARNING: could not write the storage transition to syslog.' >&2
      fi
    fi
    temporary_state=$(mktemp "$state_dir/storage-status.XXXXXX")
    printf '%s\n' "$state" >"$temporary_state"
    chmod 0640 "$temporary_state"
    mv "$temporary_state" "$state_file"
  fi
  # A warning is visible in status/syslog without putting the recurring unit
  # into a failed state. Critical usage deliberately fails the oneshot unit.
  if [ "$state" = CRITICAL ]; then exit 2; fi
  exit 0
fi

if [ "$mode" = status ]; then exit 0; fi
exit "$exit_code"
