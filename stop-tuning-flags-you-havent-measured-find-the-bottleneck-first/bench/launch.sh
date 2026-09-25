#!/bin/bash
# Usage: launch.sh <name> <image_tag> <model_dir> <util> <max_seqs> [spec_json] [extra args...]
set -e
cd "$(dirname "$0")/.."; mkdir -p logs cache
MODELS_DIR=${MODELS_DIR:-/models}   # host dir holding model checkpoints
NAME=$1; TAG=$2; MODEL=$3; UTIL=$4; SEQS=$5; SPEC=${6:-}; shift 6 || shift $#
for i in $(seq 1 60); do docker rm -f vllm-test >/dev/null 2>&1 || true; docker ps -a -q -f name=^vllm-test$ | grep -q . || break; sleep 2; done; sleep 3
ARGS=(--model /models/$MODEL --served-model-name qwen3.8-27b
  --quantization modelopt --kv-cache-dtype fp8 --trust-remote-code
  --max-model-len 262144 --max-num-seqs $SEQS --gpu-memory-utilization $UTIL
  --enable-prefix-caching --enable-prompt-tokens-details --reasoning-parser qwen3
  --enable-auto-tool-choice --tool-call-parser qwen3_xml
  --host 0.0.0.0 --port 8000)
[ -n "$SPEC" ] && ARGS+=(--speculative-config "$SPEC")
docker run -d --name vllm-test --gpus all --ipc=host -p 8000:8000 \
  -v "$MODELS_DIR":/models:ro -v "$PWD/cache":/root/.cache \
  -e VLLM_LOGGING_LEVEL=INFO $DOCKER_ENV vllm/vllm-openai:$TAG "${ARGS[@]}" "$@" >/dev/null
echo "launched $NAME"; LOG=logs/vllm_$NAME.log
for i in $(seq 1 180); do
  docker logs vllm-test > $LOG 2>&1
  if curl -sf localhost:8000/v1/models >/dev/null; then echo "READY after $((i*10))s"; break; fi
  if ! docker ps -q -f name=vllm-test | grep -q .; then echo "CONTAINER DIED"; tail -40 $LOG; exit 1; fi
  sleep 10
done
docker logs vllm-test > $LOG 2>&1
grep -E "GPU KV cache size|Maximum concurrency|Model loading took|model weights took|CUDA graph|Available KV|memory profiling|init engine" $LOG | tail -8
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
