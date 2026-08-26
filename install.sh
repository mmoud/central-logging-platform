#!/usr/bin/env bash
# Installs this package without changing disks, network configuration, firewall,
# SSH, or unrelated Docker resources.
set -Eeuo pipefail
IFS=$'\n\t'

SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PLATFORM_DIR=${PLATFORM_DIR:-/opt/logging-platform}
DATA_DIR=${DATA_DIR:-/opt/logging-data}
MIN_RAM_KIB=$((12 * 1024 * 1024))
MIN_DATA_KIB=$((20 * 1024 * 1024))

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
note() { printf '==> %s\n' "$*"; }
require_root() { [ "${EUID}" -eq 0 ] || die 'Run with sudo: sudo ./install.sh'; }

check_host() {
  [ -r /etc/os-release ] || die 'This installer supports Ubuntu Server 24.04 only.'
  # shellcheck disable=SC1091
  . /etc/os-release
  [ "${ID:-}" = ubuntu ] && [ "${VERSION_ID:-}" = 24.04 ] || die "Ubuntu 24.04 is required (found ${PRETTY_NAME:-unknown})."
  case "$(dpkg --print-architecture)" in amd64|arm64) ;; *) die 'Supported architectures are amd64 and arm64.';; esac
  [ "$(nproc)" -ge 4 ] || die 'At least 4 vCPU are required.'
  [ "$(awk '/MemTotal/ {print $2}' /proc/meminfo)" -ge "$MIN_RAM_KIB" ] || die 'At least 16 GiB RAM is required.'
  for port in 5080 514 6514; do
    if ss -H -ltnu "( sport = :$port )" 2>/dev/null | grep -q .; then
      die "Required port $port is already listening. Resolve it before installation."
    fi
  done
}

install_docker() {
  # These utilities are used by the validation and operational scripts even if
  # Docker was preinstalled by the administrator.
  apt-get update
  apt-get install -y ca-certificates curl gnupg jq openssl python3 python3-yaml rsync netcat-openbsd
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    note 'Docker Engine and Compose plugin already available.'
    return
  fi
  note 'Installing Docker Engine from Docker’s official apt repository.'
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
}

prepare_dirs() {
  install -d -m 0750 "$PLATFORM_DIR" "$DATA_DIR" \
    "$DATA_DIR/openobserve" "$DATA_DIR/syslog-ng/buffer" "$DATA_DIR/syslog-ng/state"
  local available
  available=$(df -Pk "$DATA_DIR" | awk 'NR==2 {print $4}')
  [ "$available" -ge "$MIN_DATA_KIB" ] || die "Less than 20 GiB free at $DATA_DIR. Attach/mount the log-data disk first; no disks were changed."
}

copy_package() {
  # Do not delete an administrator's local files and never overwrite a live .env.
  rsync -a --exclude '.env' --exclude 'logging-data/' --exclude 'backups/' "$SOURCE_DIR/" "$PLATFORM_DIR/"
  chown -R root:root "$PLATFORM_DIR"
  find "$PLATFORM_DIR" -type d -exec chmod 0750 {} +
  find "$PLATFORM_DIR" -type f -exec chmod 0640 {} +
  find "$PLATFORM_DIR/scripts" -type f -name '*.sh' -exec chmod 0750 {} +
  chmod 0750 "$PLATFORM_DIR/install.sh" "$PLATFORM_DIR/scripts/render-syslog-config.py"
}

create_env() {
  if [ ! -f "$PLATFORM_DIR/.env" ]; then
    cp "$PLATFORM_DIR/.env.example" "$PLATFORM_DIR/.env"
    local email password host_ip
    read -r -p 'OpenObserve administrator email: ' email
    [ -n "$email" ] || die 'An administrator email is required.'
    password=$(openssl rand -base64 32 | tr -d '\n')
    host_ip=$(hostname -I | awk '{print $1}')
    sed -i "s|^ZO_ROOT_USER_EMAIL=.*|ZO_ROOT_USER_EMAIL=$email|" "$PLATFORM_DIR/.env"
    sed -i "s|^ZO_ROOT_USER_PASSWORD=.*|ZO_ROOT_USER_PASSWORD=$password|" "$PLATFORM_DIR/.env"
    sed -i "s|^DATA_DIR=.*|DATA_DIR=$DATA_DIR|" "$PLATFORM_DIR/.env"
    sed -i "s|^PLATFORM_DIR=.*|PLATFORM_DIR=$PLATFORM_DIR|" "$PLATFORM_DIR/.env"
    sed -i "s|^ZO_WEB_URL=.*|ZO_WEB_URL=http://${host_ip:-localhost}:5080|" "$PLATFORM_DIR/.env"
    chmod 0600 "$PLATFORM_DIR/.env"
    INITIAL_PASSWORD=$password
  fi
  chmod 0600 "$PLATFORM_DIR/.env"
}

configure_firewall_note() {
  if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
    note 'UFW is active. No rules were changed. Allow TCP/5080, UDP+TCP/514, and TCP/6514 only from trusted networks as needed.'
  fi
}

main() {
  require_root; check_host; install_docker; prepare_dirs; copy_package; create_env; configure_firewall_note
  cd "$PLATFORM_DIR"
  ./scripts/validate.sh
  docker compose pull
  docker compose up -d
  ./scripts/healthcheck.sh --wait 180
  local endpoint
  # shellcheck disable=SC1091
  . ./.env
  endpoint="${ZO_WEB_URL}"
  cat <<EOF

==================================================
Logging Platform Installation Complete
==================================================
OpenObserve: ${endpoint}
Syslog UDP:  $(hostname -I | awk '{print $1}'):514
Syslog TCP:  $(hostname -I | awk '{print $1}'):514
Syslog TLS:  $(hostname -I | awk '{print $1}'):6514 (enable after generating/configuring a certificate)
Retention:   ${ZO_COMPACT_DATA_RETENTION_DAYS} days
Configuration: ${PLATFORM_DIR}
Data:          ${DATA_DIR}

Next steps:
  1. Log in to OpenObserve.
  2. Edit config/sources.yml and run ./scripts/validate.sh.
  3. Configure devices using docs/.
  4. Run ./scripts/test-syslog.sh.
EOF
  if [ -n "${INITIAL_PASSWORD:-}" ]; then
    printf '\nGenerated password (shown once; saved in %s/.env): %s\n' "$PLATFORM_DIR" "$INITIAL_PASSWORD"
  fi
}
main "$@"
