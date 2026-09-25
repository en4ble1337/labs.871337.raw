#!/bin/bash
# run_decode_cfg.sh <label> <launch args...>  -> launch + decode bench + acceptance stats
L=$1; shift; cd "$(dirname "$0")/.."; mkdir -p logs results; P=${PY:-python3}
bench/launch.sh "$L" "$@" 2>&1 | grep -E "READY|DIED|KV cache size|MiB|ValueError"
curl -sf localhost:8000/v1/models >/dev/null || exit 1
$P bench/bench.py decode --model qwen3.8-27b --label "$L" --reps 2 2>&1 | tail -2
docker logs vllm-test 2>&1 | grep "Mean acceptance" | tail -3
