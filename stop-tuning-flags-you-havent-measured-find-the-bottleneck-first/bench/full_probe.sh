#!/bin/bash
# full_probe.sh <label> <launch args...>: launch, decode bench, 261K probe w/ watchdog
L=$1; shift; cd "$(dirname "$0")/.."; mkdir -p logs results; P=${PY:-python3}
bench/run_decode_cfg.sh "$L" "$@" | grep -v "CUDA graph memory profiling"
curl -sf localhost:8000/v1/models >/dev/null || exit 1
timeout 420 $P bench/bench.py ctx --model qwen3.8-27b --label "$L" --reps 1 --ctx 261500 --max-tokens 400 --tok-per-sentence 38.64 2>&1 | grep -E "ctx~|Error" || echo "HANG/TIMEOUT at 420s"
docker logs vllm-test > logs/vllm_$L.log 2>&1
