#!/bin/bash
# vLLM at --gpu-memory-utilization=0.98 needs ~30.7 GiB free at startup. Wait for other GPU users to release.
for i in $(seq 1 90); do
  used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1)
  [ "$used" -lt 400 ] && exit 0
  sleep 2
done
echo "WARNING: GPU still has ${used} MiB in use by: $(nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader | tr '\n' ';')" >&2
exit 0
