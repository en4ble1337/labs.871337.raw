You are taking over an existing NVIDIA RTX 5090 32 GB inference server that already runs an older vLLM engine.

Your objective is to upgrade the inference stack, deploy the best available Qwen3.8-27B build for the RTX 5090, and fully tune/test it for maximum practical performance while using essentially all available GPU memory.

This is a production-style optimization task. Do not blindly overwrite the existing working installation.

## Primary objective

Deploy and optimize:

`gittensor-model-hub/Qwen3.8-27B-NVFP4-RTX5090`

Target hardware:

* NVIDIA RTX 5090
* 32 GB GDDR7
* Blackwell / SM120
* Dedicated inference GPU
* Linux host
* Existing older vLLM deployment already running

Desired characteristics:

* Full native 262,144-token context
* NVFP4 model weights
* FP8 KV cache unless a demonstrably better stable option exists
* MTP speculative decoding where beneficial
* No CPU model offloading
* Nearly full utilization of the 32 GB VRAM
* OpenAI-compatible API
* Good tool-calling support
* Prefix caching
* Maximum single-user interactive performance
* Maintain useful concurrency for agent workloads
* Stable enough for continuous use

## Important rule

DO NOT destroy or overwrite the existing working deployment until the new configuration has been completely validated.

Preserve a rollback path.

---

## Phase 1 — Audit the existing server

Before changing anything, document the current environment.

Collect at minimum:

```bash
nvidia-smi
nvidia-smi -q
uname -a
cat /etc/os-release
docker --version 2>/dev/null || true
docker compose version 2>/dev/null || true
python3 --version
pip show vllm 2>/dev/null || true
vllm --version 2>/dev/null || true
```

Determine:

* current vLLM version
* whether vLLM runs through Docker, Python venv, system Python, systemd, Compose, etc.
* current model
* current model directory/cache
* current startup command
* current environment variables
* current API port
* systemd/docker restart behavior
* NVIDIA driver version
* available system RAM
* free disk space
* whether the 5090 drives the desktop/display
* any other processes currently consuming GPU VRAM

Record:

```bash
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
```

Also save copies of all relevant existing:

* compose files
* systemd units
* shell scripts
* `.env`
* startup commands
* service configuration

Create a clearly named rollback backup before making changes.

---

## Phase 2 — Baseline the existing deployment

Before touching the existing service, benchmark it so we know whether the upgrade actually improves anything.

Record:

* model
* quantization
* context limit
* idle VRAM usage
* loaded VRAM usage
* prompt processing/prefill speed
* decode tokens/sec
* time to first token
* GPU utilization
* GPU power
* GPU temperature
* API latency

Run representative tests around:

* ~1K context
* ~8K
* ~32K

If practical also test:

* 64K
* 128K

Do not spend excessive time benchmarking the old implementation if it obviously cannot handle those contexts.

Run at least one:

* normal chat test
* code-generation test
* reasoning test
* tool-call test

Save the results.

---

## Phase 3 — Build the upgraded environment

Do NOT initially modify the existing vLLM installation.

Prefer an isolated:

1. Docker container/Compose deployment, or
2. separate Python virtual environment

depending on how the existing machine is organized.

Current stable vLLM should be evaluated first. As of this handoff, vLLM 0.29.0 is current, but verify the installed/latest stable version before proceeding.

Do not assume newest automatically means fastest.

If vLLM 0.29.x has an RTX 5090 / SM120 / NVFP4 / MTP regression, compare against the known-good 0.28.x configuration and use whichever version produces the best combination of:

* performance
* correctness
* stability
* full context support

Document any reason for using something other than current stable.

Do NOT use a random nightly unless there is a clear benchmarked reason.

---

## Phase 4 — Download the model

Download:

`gittensor-model-hub/Qwen3.8-27B-NVFP4-RTX5090`

Keep the model in a persistent model directory outside any ephemeral container filesystem.

Verify all shards downloaded correctly.

Do not accidentally substitute:

* BF16
* FP16
* generic NVFP4
* GGUF
* another Qwen3.8-27B quant

unless testing it as a deliberate comparison.

The primary target is the RTX-5090-specific NVFP4 checkpoint.

---

## Phase 5 — Establish the baseline optimized configuration

Start with approximately:

```text
model:
gittensor-model-hub/Qwen3.8-27B-NVFP4-RTX5090

quantization:
modelopt / NVFP4

max-model-len:
262144

kv-cache-dtype:
fp8

gpu-memory-utilization:
0.965

prefix caching:
enabled

CPU offload:
disabled

served model name:
qwen3.8-27b

trust remote code:
enabled if required by the checkpoint

reasoning parser:
qwen3

automatic tool choice:
enabled

tool-call parser:
qwen3_xml or the current recommended Qwen3.8 parser
```

The GPU should be treated as a dedicated inference device.

After model load, inspect:

```bash
nvidia-smi
```

We WANT vLLM to consume nearly all usable VRAM.

We DO NOT want arbitrary unused VRAM simply for safety.

However, do not push memory utilization so high that CUDA graphs, request processing, or long-context inference intermittently OOM.

The target is approximately:

**30–31+ GB committed/used under normal loaded conditions**

while remaining stable.

---

## Phase 6 — Tune GPU memory utilization

Benchmark at least:

```text
0.95
0.965
0.97
```

You may cautiously test:

```text
0.975
0.98
```

ONLY if they remain completely stable.

Do not use `1.0`.

Choose the highest configuration that:

* boots consistently
* compiles CUDA graphs successfully
* survives repeated requests
* survives long-context requests
* does not OOM
* does not create operational instability

Report both:

* total GPU memory consumed
* reported KV cache/token capacity

The KV cache capacity must support the complete 262,144-token context with reasonable operating margin.

---

## Phase 7 — Test MTP speculative decoding

This is important.

Benchmark:

### MTP disabled

Base vLLM inference.

### MTP-2

Equivalent current syntax for:

```json
{"method":"mtp","num_speculative_tokens":2}
```

### MTP-3

Equivalent current syntax for:

```json
{"method":"mtp","num_speculative_tokens":3}
```

Use whatever syntax the installed vLLM release currently requires.

Do NOT assume MTP-3 is automatically better.

Compare:

* output tokens/sec
* acceptance rate if exposed
* TTFT
* VRAM consumption
* output correctness
* tool-call reliability
* long-context behavior

Speculative decoding must not introduce:

* repetition loops
* malformed output
* broken JSON
* broken tool calls
* garbled responses
* reasoning corruption

If MTP-3 is faster but materially less reliable, use MTP-2.

---

## Phase 8 — Test max-num-seqs / concurrency

We care primarily about fast interactive agent inference but would also like useful concurrency.

Test at minimum:

```text
max-num-seqs=1
max-num-seqs=3
```

Also test higher concurrency if memory allows and vLLM benefits.

Measure separately:

### Single request

One active generation.

Report tokens/sec and TTFT.

### Three concurrent requests

Report:

* aggregate throughput
* per-stream throughput
* latency

### Higher concurrency

Optional, but useful if the deployment could serve multiple agents.

Do not sacrifice substantial single-stream performance merely to advertise high aggregate throughput.

For this machine, interactive inference is the first priority.

---

## Phase 9 — Context testing

Verify that setting:

```text
--max-model-len 262144
```

does not merely allow the server to start.

Actually test long prompts.

Test approximately:

* 1K
* 8K
* 32K
* 64K
* 128K
* 200K+
* near 256K if feasible

Measure separately:

* prompt/prefill tokens/sec
* prefill duration
* decode tokens/sec
* TTFT
* VRAM consumption

For long-context tests, confirm the model can retrieve information from near the beginning and end of the prompt.

A simple needle-in-a-haystack validation is acceptable.

---

## Phase 10 — Functional correctness

Performance alone is not enough.

Test:

### Normal conversation

Natural answer without corruption.

### Coding

Generate a meaningful Python or shell task.

### Reasoning

Test with thinking both enabled and disabled if supported.

### Tool calling

Test a representative OpenAI-compatible tool definition.

Verify:

* tool name
* arguments
* JSON formatting
* parser behavior

Run multiple tool calls, not merely one.

### Long conversation

Use prefix caching and verify subsequent turns behave correctly.

---

## Phase 11 — Benchmark performance

Create a reproducible benchmark harness rather than eyeballing output speed.

Record results in a table similar to:

| Config   | Context |  MTP | Max Seqs | Prefill tok/s | Decode tok/s | TTFT | VRAM |
| -------- | ------: | ---: | -------: | ------------: | -----------: | ---: | ---: |
| Existing |      1K |  N/A |        ? |               |              |      |      |
| New      |      1K |  Off |        1 |               |              |      |      |
| New      |      1K |    2 |        1 |               |              |      |      |
| New      |      1K |    3 |        1 |               |              |      |      |
| New      |     32K | best |        1 |               |              |      |      |
| New      |     64K | best |        1 |               |              |      |      |
| New      |    128K | best |        1 |               |              |      |      |
| New      |   ~256K | best |        1 |               |              |      |      |
| New      |  normal | best |        3 |               |    aggregate |      |      |

Also capture:

```bash
nvidia-smi --query-gpu=timestamp,name,memory.used,memory.total,utilization.gpu,utilization.memory,power.draw,temperature.gpu,clocks.sm,clocks.mem --format=csv
```

during representative inference.

---

## Phase 12 — Thermal / sustained test

Do not benchmark only short bursts.

Run repeated inference for at least several sustained workloads and verify:

* clocks remain stable
* GPU does not thermally throttle
* power limit is not unexpectedly constraining performance
* no CUDA errors
* no memory leaks
* no progressive VRAM growth
* no vLLM worker crashes

Check:

```bash
nvidia-smi -q -d PERFORMANCE,TEMPERATURE,POWER,CLOCK
```

Do not modify GPU voltage or unsafe power settings.

---

## Phase 13 — Choose the winner

Select the configuration based on measured results.

Priority order:

1. Correct output
2. Stable operation
3. Full 262K context
4. Maximum single-stream decode performance
5. Fast prefill
6. Tool-call correctness
7. Low TTFT
8. Useful concurrent-agent throughput
9. Maximum safe VRAM utilization

Do not select a configuration merely because it consumes more memory.

The purpose of using the whole 32 GB is to increase capability/performance, not to make `nvidia-smi` look full.

---

## Phase 14 — Deploy

Once the winning configuration is identified:

* stop the old service cleanly
* deploy the new service using persistent configuration
* configure automatic startup/restart
* retain old configuration for rollback
* ensure model files remain persistent
* ensure logs are persistent/reviewable

Use the SAME API port as the previous service if clients depend upon it, unless there is a technical reason not to.

Do not break existing OpenAI-compatible clients.

---

## Phase 15 — Final validation

After final deployment:

```bash
nvidia-smi
```

Verify API:

```bash
curl http://127.0.0.1:<PORT>/v1/models
```

Then make actual chat/completion requests.

Verify:

* service survives reboot/restart
* model automatically loads
* API responds
* OpenAI-compatible clients work
* no unexpected GPU consumers
* expected VRAM allocation
* expected context length
* MTP configuration
* prefix caching
* tool calling
* reasoning
* normal chat

Run the final benchmark again after deployment rather than relying on pre-deployment testing.

---

## Final deliverable

When finished, give me a concise report containing:

### Before

* vLLM version
* model
* startup configuration
* VRAM usage
* decode performance

### After

* vLLM version
* exact model/revision
* exact startup command or Compose configuration
* quantization
* KV cache format
* MTP configuration
* max model length
* max sequences
* GPU memory utilization setting
* actual loaded VRAM
* KV token capacity

### Performance

Provide measured:

* 1K decode tok/s
* 8K decode tok/s
* 32K decode tok/s
* 64K decode tok/s
* 128K decode tok/s
* near-max-context decode tok/s
* prompt/prefill speed where available
* TTFT
* concurrent throughput

### Improvement

Calculate percentage improvement over the old installation.

### Stability

State whether:

* full 262K context works
* tool calls work
* MTP works correctly
* prefix caching works
* repeated requests are stable
* sustained inference is stable
* there were any OOMs

### Rollback

Provide the exact rollback procedure.

### Final recommendation

Explain why the selected configuration won against the alternatives tested.

Do not claim performance improvements that were not measured.

Do not stop after merely getting the model to load. The assignment is complete only when the server is **benchmarked, tuned, validated, and deployed with the best stable configuration found for this specific RTX 5090**.
