#!/bin/bash
# Wait for vLLM, then warm up batch shapes (first 3/4/8-way batches otherwise pay ~2s one-time TTFT).
for i in $(seq 1 180); do curl -sf http://127.0.0.1:11434/health >/dev/null && break; sleep 5; done
curl -sf http://127.0.0.1:11434/health >/dev/null || exit 1
req() { curl -s -o /dev/null http://127.0.0.1:11434/v1/chat/completions -H 'Content-Type: application/json' \
  -d "{\"model\":\"qwen3.8-27b\",\"messages\":[{\"role\":\"user\",\"content\":\"warmup $1: count to 50\"}],\"max_tokens\":64,\"chat_template_kwargs\":{\"enable_thinking\":false}}"; }
for n in 1 2 3 4 6 8; do for j in $(seq 1 $n); do req $n-$j & done; wait; done
echo "warmup done"
