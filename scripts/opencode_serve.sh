#!/usr/bin/env bash
# ==============================================================================
# Script: opencode_serve.sh
# Author: yaroxy
# Created: 2026-05-18
# Last Updated: 2026-05-18
# Description: brief script description
# ==============================================================================
set -euo pipefail

# ==============================================================================
# Load environment variables from .env
# ==============================================================================
# run directory
run_dir="$(pwd)"
if [ -f "$run_dir/.env" ]; then
    echo "Loading environment variables from $run_dir/.env"
    set -a
    source "$run_dir/.env"
    set +a
fi

# script directory
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "$script_dir" != "$run_dir" ]; then
    if [ -f "$script_dir/.env" ]; then
        echo "Loading environment variables from $script_dir/.env"
        set -a
        source "$script_dir/.env"
        set +a
    fi
fi

# ==============================================================================
# opencode serve
# ==============================================================================
OPENCODE_SERVER_PASSWORD=${OPENCODE_SERVER_PASSWORD:-vibe-coding}
OPENCODE_SERVER_USERNAME=${OPENCODE_SERVER_USERNAME:-yaroxy}
OPENCODE_SERVER_PORT=${OPENCODE_SERVER_PORT:-8192}
OPENCODE_SERVER_HOSTNAME=${OPENCODE_SERVER_HOSTNAME:-0.0.0.0}

OUTPUTS_DIR=${OUTPUTS_DIR:-/Users/roxy/Documents/workspace/chatops/outputs}

log_dir="${OUTPUTS_DIR}/logs/opencode"
mkdir -p "$log_dir"

opencode serve \
  --hostname $OPENCODE_SERVER_HOSTNAME \
  --port $OPENCODE_SERVER_PORT \
  --print-logs \
  --log-level DEBUG \
  2>&1 | tee >(rotatelogs -l ${log_dir}/$(date +%Y-%m-%d-%H-%M-%S).log 5M)
