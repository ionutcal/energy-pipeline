#!/usr/bin/env bash
# Ruleaza pipeline-ul si scrie logul. De pus in cron:
#   0 6 * * *  /cale/catre/proiect/scripts/run_daily.sh
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p logs
docker compose run --rm pipeline >> "logs/pipeline_$(date +%Y-%m).log" 2>&1
