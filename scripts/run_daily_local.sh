#!/usr/bin/env bash
# Runs the pipeline and the report from the project's virtualenv, without
# Docker, appending to a monthly log. Used by the launchd job installed with
# scripts/install_schedule.sh, but it can also be run by hand.
#
# The database comes from .env (or DATABASE_URL in the environment). To keep
# using a local SQLite file, add this line to .env:
#   DATABASE_URL=sqlite:///energy.db
set -uo pipefail

cd "$(dirname "$0")/.."
mkdir -p logs
log="logs/pipeline_$(date +%Y-%m).log"
python="venv/bin/python"

if [[ ! -x "$python" ]]; then
    echo "$(date '+%F %T') venv not found at $python — create it first (see README)" >> "$log"
    exit 1
fi

{
    echo "===== $(date '+%F %T') daily run ====="
    "$python" -m src.pipeline
    status=$?
    # The report runs even if one metric failed: the other metrics are fresh.
    "$python" -m src.report
    echo "===== $(date '+%F %T') finished, pipeline exit code $status ====="
    exit $status
} >> "$log" 2>&1
