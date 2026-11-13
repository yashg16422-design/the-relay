#!/usr/bin/env bash
# Run every stand-in load scenario, each in its own process with its own stand-in.
# Machine: Aloo's Mac. Directory: the repo root (the-relay). Zero provider calls.
# Usage: bash load/run_all.sh <run-id>      e.g. bash load/run_all.sh L1-20261113
set -euo pipefail
RUN_ID="${1:?give a run id, e.g. L1-20261113}"
OUT="results/raw/runs/${RUN_ID}"
mkdir -p "$OUT"
cp standin/config.yaml load/scenarios.yaml config/scheduler.yaml config/pipeline.yaml "$OUT"/
for name in steady burst burst_cache_on sustained_overload per_minute_limit_halved \
            daily_budget_exhausted retry_disciplined retry_storm; do
  .venv/bin/python -m load.loadgen --scenario "$name" --run-id "$RUN_ID" \
      --results "$OUT/$name.csv" --traces results/traces > "$OUT/$name.log" 2>&1 &
done
wait
grep -h "requests, statuses" "$OUT"/*.log
