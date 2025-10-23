#!/usr/bin/env bash
set -euo pipefail

WORKERS=${WORKER_COUNT:-4}
DISPLAY_BASE=${PLAYWRIGHT_DISPLAY_BASE:-10}
SCREEN_SPEC=${XVFB_SCREEN:-1920x1080x24}

declare -a XVFB_PIDS=()

cleanup() {
  for pid in "${XVFB_PIDS[@]:-}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
  wait || true
}

trap cleanup EXIT INT TERM

for ((i = 0; i < WORKERS; i++)); do
  DISPLAY_NUM=$((DISPLAY_BASE + i))
  echo "Starting Xvfb on :${DISPLAY_NUM} (screen ${SCREEN_SPEC})"
  Xvfb ":${DISPLAY_NUM}" -screen 0 "${SCREEN_SPEC}" -nolisten tcp >/tmp/xvfb-${DISPLAY_NUM}.log 2>&1 &
  XVFB_PIDS+=("$!")
done

exec python -m src.runner
