#!/usr/bin/env bash
set -euo pipefail

GRADLE_PREWARM_USER_HOME="${GRADLE_PREWARM_USER_HOME:-/opt/gradle-prewarm}"
mkdir -p "${GRADLE_PREWARM_USER_HOME}"

run_gradle() {
  local version="$1"
  local install_dir="/opt/gradle-${version}"

  if [ ! -x "${install_dir}/bin/gradle" ]; then
    return 0
  fi

  "${install_dir}/bin/gradle" \
    --gradle-user-home "${GRADLE_PREWARM_USER_HOME}" \
    --no-daemon \
    -v >/dev/null 2>&1 || true
}

run_gradle "8.5"
