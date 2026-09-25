#!/bin/bash
# soak.sh <label> <minutes> [port]: sustained mixed workload against a running server.
L=$1; MIN=${2:-25}; PORT=${3:-8000}; cd "$(dirname "$0")/.."
B="--base http://127.0.0.1:$PORT --model qwen3.8-27b"
P=${PY:-python3}; OUT=results/soak_$L; mkdir -p $OUT
nvidia-smi --query-gpu=timestamp,name,memory.used,memory.total,utilization.gpu,utilization.memory,power.draw,temperature.gpu,clocks.sm,clocks.mem,clocks_throttle_reasons.active --format=csv -l 2 > $OUT/gpu.csv &
SMI=$!
END=$(( $(date +%s) + MIN*60 )); cyc=0
while [ $(date +%s) -lt $END ]; do
  cyc=$((cyc+1)); echo "=== cycle $cyc $(date +%T) vram=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"
  $P bench/bench.py decode $B --label soak-$L --reps 1 --out $OUT/bench.jsonl 2>&1 | grep "^=="
  $P bench/bench.py conc $B --label soak-$L --conc 3 4 --ignore-eos --out $OUT/bench.jsonl 2>&1 | grep "conc="
  $P bench/bench.py ctx $B --label soak-$L --reps 1 --ctx 32768 65536 131072 --tok-per-sentence 38.64 --max-tokens 300 --out $OUT/bench.jsonl 2>&1 | grep "ctx~"
  $P bench/func.py soak-$L --base http://127.0.0.1:$PORT --rounds 1 2>&1 | grep "^=="
  timeout 420 $P bench/bench.py ctx $B --label soak-$L --reps 1 --ctx 261500 --tok-per-sentence 38.64 --max-tokens 300 --out $OUT/bench.jsonl 2>&1 | grep "ctx~" || echo "!!! 261K HANG/TIMEOUT"
done
kill $SMI
nvidia-smi -q -d PERFORMANCE,TEMPERATURE,POWER,CLOCK > $OUT/nvidia_q_after.txt
echo "=== VRAM first/last/max (MiB):"; awk -F', ' 'NR>1{gsub(/ MiB/,"",$3); v[NR]=$3; if($3>m)m=$3} END{print v[2], v[NR], m}' $OUT/gpu.csv
echo "=== temp max / power max / SM clock min-when-busy:"; awk -F', ' 'NR>1{gsub(/ W/,"",$7); gsub(/ MHz/,"",$9); gsub(/ %/,"",$5); if($8>t)t=$8; if($7>p)p=$7; if($5>80 && (c==""||$9<c))c=$9} END{print t"C", p"W", c"MHz"}' $OUT/gpu.csv
echo "=== throttle reasons seen while busy:"; awk -F', ' 'NR>1{gsub(/ %/,"",$5); if($5>80) print $11}' $OUT/gpu.csv | sort | uniq -c
