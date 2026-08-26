#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)

usage() {
  cat <<'EOF'
Usage: ./scripts/update.sh [--check | --apply] [--no-backup] [--docker-engine]

  --check          Discover official stable releases and show pending digest changes.
  --apply          Back up, pin current stable images, validate, and verify health.
  --no-backup      Skip the configuration backup made by --apply.
  --docker-engine  Also update Docker packages from Docker's official apt repo.
EOF
}

mode=check
backup=yes
update_docker=no
while [ "$#" -gt 0 ]; do
  case "$1" in
    --check) mode=check ;;
    --apply) mode=apply ;;
    --no-backup) backup=no ;;
    --docker-engine) update_docker=yes ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

. "$SCRIPT_DIR/common.sh"

require_command() {
  command -v "$1" >/dev/null || { echo "Required command missing: $1" >&2; exit 2; }
}
require_command docker
require_command jq
require_command curl

latest_openobserve_source() {
  local tag
  tag=$(curl -fsSL --retry 3 https://api.github.com/repos/openobserve/openobserve/releases/latest | jq -er '.tag_name')
  [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Unexpected OpenObserve release tag: $tag" >&2; return 1; }
  printf 'public.ecr.aws/zinclabs/openobserve:%s\n' "$tag"
}

latest_syslog_ng_source() {
  local tag version
  tag=$(curl -fsSL --retry 3 https://api.github.com/repos/syslog-ng/syslog-ng/releases/latest | jq -er '.tag_name')
  version=${tag#syslog-ng-}
  [[ "$tag" = syslog-ng-* ]] && [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Unexpected syslog-ng release tag: $tag" >&2; return 1; }
  printf 'balabit/syslog-ng:%s\n' "$version"
}

latest_report_server_source() {
  local tag
  # The official Helm chart is OpenObserve's published compatibility matrix for
  # the separately-versioned Report Server image.
  tag=$(curl -fsSL --retry 3 https://raw.githubusercontent.com/openobserve/openobserve-helm-chart/main/charts/openobserve/values.yaml | awk '
    /^  reportserver:/ { in_report_server=1; next }
    in_report_server && /^  [[:alnum:]_]+:/ { in_report_server=0 }
    in_report_server && /^    tag:/ { gsub(/"/, "", $2); tag=$2 }
    END { if (tag != "") print tag }
  ')
  [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+-[0-9A-Za-z]+$ ]] || { echo "Unexpected Report Server tag: $tag" >&2; return 1; }
  printf 'public.ecr.aws/zinclabs/report-server:%s\n' "$tag"
}

stable_source() {
  local component=$1 configured=$2
  if [ "$configured" != auto ]; then
    printf '%s\n' "$configured"
    return
  fi
  case "$component" in
    openobserve) latest_openobserve_source ;;
    syslog-ng) latest_syslog_ng_source ;;
    report-server) latest_report_server_source ;;
  esac
}

resolve_digest() {
  # Resolve the manifest-list/index digest, preserving both amd64 and arm64
  # support. Actual layers are pulled only by `dc pull` during --apply.
  local source=$1 repository digest
  # Remove the image tag at the final colon; registry host:port remains valid.
  repository=${source%:*}
  digest=$(docker buildx imagetools inspect "$source" | awk '/^Digest:/ { print $2; exit }')
  [ -n "$digest" ] && [ "$digest" != '<no value>' ] || {
    echo "Could not resolve an immutable digest for $source" >&2
    return 1
  }
  printf '%s@%s\n' "$repository" "$digest"
}

update_env_value() {
  local key=$1 value=$2 temporary
  temporary=$(mktemp)
  awk -v key="$key" -v value="$value" '
    BEGIN { replaced=0 }
    $0 ~ "^" key "=" { print key "=" value; replaced=1; next }
    { print }
    END { if (!replaced) print key "=" value }
  ' .env >"$temporary"
  chmod 0600 "$temporary"
  mv "$temporary" .env
}

echo 'Configured immutable image pins:'
grep -E '^(OPENOBSERVE_IMAGE|SYSLOG_NG_IMAGE|REPORT_SERVER_IMAGE)=' .env

component_image_variable() {
  case "$1" in
    openobserve) printf '%s\n' OPENOBSERVE_IMAGE ;;
    syslog-ng) printf '%s\n' SYSLOG_NG_IMAGE ;;
    report-server) printf '%s\n' REPORT_SERVER_IMAGE ;;
  esac
}

component_stable_setting() {
  case "$1" in
    openobserve) printf '%s\n' "${OPENOBSERVE_STABLE_SOURCE:-auto}" ;;
    syslog-ng) printf '%s\n' "${SYSLOG_NG_STABLE_SOURCE:-auto}" ;;
    report-server) printf '%s\n' "${REPORT_SERVER_STABLE_SOURCE:-auto}" ;;
  esac
}

changes=0
for component in openobserve syslog-ng report-server; do
  variable=$(component_image_variable "$component")
  current=${!variable}
  source=$(stable_source "$component" "$(component_stable_setting "$component")")
  resolved=$(resolve_digest "$source")
  case "$component" in
    openobserve) resolved_openobserve=$resolved ;;
    syslog-ng) resolved_syslog_ng=$resolved ;;
    report-server) resolved_report_server=$resolved ;;
  esac
  if [ "$current" = "$resolved" ]; then
    printf '%-16s current (%s)\n' "$component" "${resolved##*@}"
  else
    printf '%-16s UPDATE %s -> %s\n' "$component" "${current##*@}" "${resolved##*@}"
    changes=1
  fi
done

if [ "$mode" = check ]; then
  if [ "$changes" -eq 0 ]; then
    echo 'All component images are at their configured stable digest.'
  else
    echo 'Review upstream release notes, then run: ./scripts/update.sh --apply'
  fi
  exit 0
fi

if [ "$backup" = yes ]; then ./scripts/backup.sh; fi
backup_file=".env.backup.$(date -u +%Y%m%dT%H%M%SZ)"
cp -p .env "$backup_file"
chmod 0600 "$backup_file"

for component in openobserve syslog-ng report-server; do
  variable=$(component_image_variable "$component")
  current=${!variable}
  case "$component" in
    openobserve) resolved=$resolved_openobserve ;;
    syslog-ng) resolved=$resolved_syslog_ng ;;
    report-server) resolved=$resolved_report_server ;;
  esac
  if [ "$current" != "$resolved" ]; then
    update_env_value "$variable" "$resolved"
  fi
done

if [ "$update_docker" = yes ]; then
  [ "${EUID}" -eq 0 ] || { echo '--docker-engine requires sudo/root.' >&2; exit 2; }
  apt-get update
  apt-get install -y --only-upgrade docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

./scripts/validate.sh
if [ "${EUID}" -eq 0 ]; then
  ./scripts/install-storage-guard.sh
else
  echo 'Run sudo ./scripts/install-storage-guard.sh to refresh the storage timer.'
fi
dc pull
dc up -d --remove-orphans=false
./scripts/healthcheck.sh --wait 180
echo "Update complete. Roll back with pins in $backup_file."
