#!/bin/bash
# "Serving" = /v1/models answers AND a short completion returns the expected text.
# NOT RUN in this session (GPU was in use by production).
# Usage: wait_serving.sh http://<ct-ip>:<port> <served-model-name> [timeout_s]
URL=$1; MODEL=$2; TIMEOUT=${3:-900}; T0=$(date +%s.%N)
echo "start $T0"
while :; do
  if curl -sf -m 5 "$URL/v1/models" >/dev/null; then
    out=$(curl -sf -m 30 "$URL/v1/completions" -H 'Content-Type: application/json' \
      -d "{\"model\":\"$MODEL\",\"prompt\":\"Q: What is 2+2? Answer with one number.\nA:\",\"max_tokens\":4,\"temperature\":0}")
    if echo "$out" | grep -q '4'; then T1=$(date +%s.%N); echo "serving $T1 elapsed=$(echo "$T1-$T0"|bc)s"; exit 0; fi
  fi
  [ $(echo "$(date +%s.%N)-$T0 > $TIMEOUT"|bc) -eq 1 ] && { echo "TIMEOUT after ${TIMEOUT}s"; exit 1; }
  sleep 2
done
