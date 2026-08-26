#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$ROOT_DIR"
# shellcheck source=../scripts/common.sh
. scripts/common.sh

output=$(mktemp)
cleanup() {
  rm -f -- "$output"
}
trap cleanup EXIT

set +e
timeout --signal=TERM 8s docker compose run --rm --no-deps -T \
  -v "$ROOT_DIR/tests:/tests:ro" \
  --entrypoint /usr/sbin/syslog-ng \
  syslog-ng -F -f /tests/parser-harness.conf >"$output" 2>&1
rc=$?
set -e
if [ "$rc" -ne 0 ] && [ "$rc" -ne 124 ] && [ "$rc" -ne 143 ]; then
  cat "$output" >&2
  exit "$rc"
fi

assert_field() {
  local pattern=$1
  if ! grep -Eq -- "$pattern" "$output"; then
    echo "Missing parsed field matching: $pattern" >&2
    cat "$output" >&2
    exit 1
  fi
}

assert_field 'JUNIPER .*"event":"RPD_BGP_NEIGHBOR_STATE_CHANGED"'
assert_field 'JUNIPER .*"interface":"xe-0/0/1.0"'
assert_field 'JUNIPER .*"username":"netops"'
assert_field 'JUNIPER .*"source_address":"10.0.0.10"'
assert_field 'JUNIPER .*"destination_address":"203.0.113.20"'
assert_field 'JUNIPER .*"policy":"users-web"'
assert_field 'JUNIPER .*"session_id":"123456"'
assert_field 'PROXMOX .*"authentication_result":"authentication failure"'
assert_field 'PROXMOX .*"source_ip":"10.0.0.20"'
assert_field 'PROXMOX .*"upid":"UPID:pve01:'
assert_field 'PROXMOX .*"task":"qmstart"'
assert_field 'PROXMOX .*"vmid":"101"'
assert_field 'PMG .*"filter_id":"1A2B3C4D5E"'
assert_field 'PMG .*"message_id":"original@example.com"'
assert_field 'PMG .*"envelope_sender":"bounces\+SRS=abc=example.org=user@example.net"'
assert_field 'PMG .*"header_from":"Original Sender <user@example.org>"'
assert_field 'PMG .*"header_sender":"user@example.org"'
assert_field 'PMG .*"header_sender_domain":"example.org"'
assert_field 'PMG .*"header_recipient_domain":"example.net"'
assert_field 'PMG .*"envelope_recipient_domain":"example.net"'
assert_field 'PMG .*"subject":"Quarterly invoice 12345"'
assert_field 'PMG .*"linked_queue_id":"4F6A912345"'
assert_field 'PMG .*"rule":"default-accept"'
assert_field 'PMG .*"filter_action":"spam quarantine"'
assert_field 'PMG .*"rule":"Quarantine/Mark Spam \(Level 3\)"'
assert_field 'PMG .*"spam_score":"6"'
assert_field 'PMG .*"spam_threshold":"5"'
assert_field 'PMG .*"spf_result":"SPF_PASS"'
assert_field 'PMG .*"dkim_result":"DKIM_VALID"'
assert_field 'PMG .*"dmarc_result":"DMARC_REJECT"'
assert_field 'PMG .*"arc_result":"ARC_VALID"'
assert_field 'PMG .*"dkim_result":"DKIM_VALID_AU"'
assert_field 'PMG .*"dmarc_result":"DMARC_PASS"'
if grep -E 'PROXMOX .*"PROGRAM":"systemd"' "$output" | grep -q 'authentication_result'; then
  echo 'A systemd service failure was incorrectly classified as an authentication result.' >&2
  cat "$output" >&2
  exit 1
fi

echo 'Juniper, Proxmox VE, and PMG parser samples passed.'
