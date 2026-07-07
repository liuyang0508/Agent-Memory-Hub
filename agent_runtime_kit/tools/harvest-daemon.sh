#!/usr/bin/env bash
# harvest-daemon.sh — periodic enrich pass: raw (L0) -> distilled when a model
# is reachable. The mechanical harvest already persisted raw records offline, so
# this loop is non-blocking gravy; if `memory` is missing or a model is
# unreachable, `memory harvest --enrich` is a clean no-op (it never blocks).
#
# Usage:   ./harvest-daemon.sh        (interval via $HARVEST_INTERVAL, default 1800s)
# Stop:    Ctrl-C
set -uo pipefail
INTERVAL="${HARVEST_INTERVAL:-1800}"   # 30 min
while true; do
    command -v memory >/dev/null 2>&1 && memory harvest --enrich >/dev/null 2>&1 || true
    sleep "$INTERVAL"
done
