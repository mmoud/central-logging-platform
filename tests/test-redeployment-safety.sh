#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
temporary=$(mktemp -d)
trap 'rm -rf -- "$temporary"' EXIT

# install.sh is deliberately source-safe so its package synchronization can be
# regression-tested without installing packages or touching the host.
# shellcheck source=../install.sh
. "$ROOT/install.sh"

source_tree="$temporary/source"
target_tree="$temporary/target"
mkdir -p "$source_tree/config" "$target_tree/config"
printf 'devices:\n  - name: public-example\n' >"$source_tree/config/sources.yml"
printf 'devices:\n  - name: private-live-device\n' >"$target_tree/config/sources.yml"
printf 'new repository content\n' >"$source_tree/README.md"
printf 'old installed content\n' >"$target_tree/README.md"

SOURCE_DIR=$source_tree
PLATFORM_DIR=$target_tree
sync_package

grep -F 'private-live-device' "$target_tree/config/sources.yml" >/dev/null
grep -F 'new repository content' "$target_tree/README.md" >/dev/null

fresh_target="$temporary/fresh-target"
mkdir -p "$fresh_target"
PLATFORM_DIR=$fresh_target
sync_package
cmp "$source_tree/config/sources.yml" "$fresh_target/config/sources.yml"

# Metadata must be restored to DATA_DIR, never below the application checkout.
grep -F 'tar -xzf "$archive" -C "$DATA_DIR" --no-same-owner openobserve/db' \
  "$ROOT/scripts/restore.sh" >/dev/null
if grep -F 'tar -xzf "$archive" -C "$ROOT_DIR" --no-same-owner openobserve/db' \
   "$ROOT/scripts/restore.sh" >/dev/null; then
  echo 'restore.sh extracts OpenObserve metadata below ROOT_DIR' >&2
  exit 1
fi

echo 'Redeployment safety checks passed.'
