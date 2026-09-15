#!/usr/bin/env bash
# Runs the pipeline and writes the log. Add to cron:
#   0 6 * * *  /path/to/project/scripts/run_daily.sh
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p logs
docker compose run --rm pipeline >> "logs/pipeline_$(date +%Y-%m).log" 2>&1
