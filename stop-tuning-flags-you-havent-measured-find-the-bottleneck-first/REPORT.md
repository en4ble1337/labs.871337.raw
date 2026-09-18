# Qwen3.8-27B on a single RTX 5090: from Ollama to a tuned vLLM stack

**Executive report: inference-server upgrade, tuning and validation**
Host: Proxmox LXC container (Ubuntu 22.04.5) · Date: 2026-09-18 · All numbers below were measured on this machine.

---

## TL;DR

| | Before | After | Change |
|---|---|---|---|
| Engine | Ollama 0.20.6 (llama.cpp/GGUF) | **vLLM 0.29.0** (Docker, V1 model runner) | |
| Model | gemma3:27b, Q4_K_M | **Qwen3.8-27B NVFP4, RTX-5090 build** + MTP-4 speculative decoding | |
| Context window | 32,768 (Ollama auto-default) | **262,144 (full native window)** | **8×** |
| Decode, single stream (512-token outputs) | 65.5 tok/s | **226.3 tok/s** | **+245% (3.45×)** |
| Decode, same needle harness @ ~8K ctx | 63.2 tok/s | **138.7 tok/s** | **+119%** |
| Decode, same needle harness @ ~32K ctx | 59.5 tok/s | **143.7 tok/s** | **+141%** |
| Prefill @ ~8K | 3,044 tok/s | **8,881 tok/s** | **+192%** |
| Prefill @ ~32K | 3,356 tok/s | **7,790 tok/s** | **+132%** |
| Parallel requests | 1 slot | **8 slots, 1,015–1,125 tok/s aggregate** | |
| Tool calling | not tested on old stack | **31/31 functional checks pass** (parallel, typed, streaming) | |
| VRAM in use | 20.7 GB of 32.6 GB | **30.9 GB loaded, 31.5–31.7 GB peak** | |

> **Comparison caveat:** the old and new stacks run **different models** (Gemma-3-27B Q4_K_M vs Qwen3.8-27B NVFP4) with different tokenizers. So the "before → after" rows compare the whole serving stack as the user experiences it, not the same weights on two engines. The same-model comparisons (no-spec vs MTP, vLLM 0.28 vs 0.29) are the cleaner engineering data points, and they're in the sections below.

**Final configuration:** vLLM 0.29.0 · `VLLM_USE_V2_MODEL_RUNNER=0` · checkpoint `pre-final` revision (has the MTP head) · NVFP4 weights · FP8 KV cache · MTP with 4 speculative tokens · `max-model-len 262144` · `max-num-seqs 8` · `gpu-memory-utilization 0.98` · prefix caching on · `qwen3_xml` tool parser · `qwen3` reasoning parser. OpenAI-compatible API on port 11434.

---

## 1. Starting point (Phase 1–2 audit)

The handoff said the box ran "an older vLLM". **The audit found no vLLM anywhere**: no containers, no venvs, no systemd units. The machine actually ran:

- **Ollama 0.20.6** as systemd `ollama.service` (`Restart=always`, `OLLAMA_HOST=0.0.0.0:11434`, enabled at boot)
- One model pulled: **gemma3:27b** (Q4_K_M GGUF, 17 GB). Ollama auto-selected a **32K context** and 1 parallel slot.
- GPU: RTX 5090 32 GB (32,607 MiB), driver 580.82.09, CUDA 13.0, persistence mode on, **no display attached** (dedicated compute), idle at 0 MiB.
- **Power limit capped at 500 W** by the host (default 575 W, max 600 W). Left unchanged, per the brief.
- 24 vCPU, 125 GiB RAM, 443 GB free disk, Docker 29.2.1 + Compose v5.0.2 with the NVIDIA runtime.

The existing unit files and binary were backed up to `/root/llm-upgrade/backup/ollama-rollback-*`.

### Baseline (Ollama + gemma3:27b)

| Test | Prompt tok | TTFT | Prefill tok/s | Decode tok/s | Needles |
|---|---:|---:|---:|---:|---|
| Decode suite (chat/code/json/reason, 512 tok) | 34–74 | 0.43 s warm (24.6 s cold load) | — | **65.5** avg | — |
| ~1K context | 614 | 0.56 s | 1,097 | 65.3 | ✅ |
| ~8K context | 6,943 | 2.28 s | 3,044 | 63.2 | ✅ |
| ~32K context | 26,285 | 7.83 s | 3,356 | 59.5 | ✅ |

VRAM loaded: 20.7 GB. Power under load: 428–450 W. Baselines beyond 32K weren't run because the old deployment's configured window is 32K. (The owner asked to stop spending time on the old stack.)

---

## 2. The model: `gittensor-model-hub/Qwen3.8-27B-NVFP4-RTX5090`

A ModelOpt NVFP4 (W4A4, group 16) quant built specifically for the RTX 5090: `lm_head` is also NVFP4 (0.72 GB instead of 2.54 GB BF16), and the Gated-DeltaNet `conv1d`/`in_proj_a/b` layers, embeddings and vision tower stay in BF16. It's a hybrid architecture (`Qwen3_5ForConditionalGeneration`) mixing Gated-DeltaNet linear-attention layers with full-attention layers, which is why the KV cache is small enough to hold 262K tokens on 32 GB.

**Key finding: the `main` revision has the MTP head deleted.** The model card's final build removed the Multi-Token-Prediction head, because the authors recommend their external DSpark drafter instead. MTP speculative decoding therefore needs the **`pre-final` branch** (commit `35fd99fb8434ef70ee61372df3face5174bed2c8`): the same NVFP4 weights and NVFP4 `lm_head`, plus the MTP head (+0.85 GB).

Downloaded to persistent `/models` and every shard was SHA-256-verified against the Hub:

| Directory | Revision | Size | Role |
|---|---|---|---|
| `/models/Qwen3.8-27B-NVFP4-RTX5090` | `main` @ `5b7a687f` | 17.9 GB, 2 shards | no-spec baseline |
| `/models/Qwen3.8-27B-NVFP4-RTX5090-pre-final` | `pre-final` @ `35fd99fb` | 18.8 GB, 3 shards | **production (MTP)** |
| `/models/Qwen3.8-27B-DSpark-NVFP4` | `main` | 1.4 GB | DSpark drafter (evaluated, see §5) |

---

## 3. Engine selection: vLLM 0.29.0 vs 0.28.0, and a regression we hit

vLLM **0.29.0** was the current stable release (PyPI and Docker Hub `latest`, published 2026-09-09) and was evaluated first, with **0.28.0** as the fallback, both via the official `vllm/vllm-openai` images. Nightlies weren't used.

### The regression

With the default settings, vLLM 0.29.0 + MTP ran perfectly up to 200K context, but **hung indefinitely on a ~261K-token prompt**: GPU at 100% and ~480 W, no progress for over 13 minutes, where the expected prefill time is about 2 minutes. `py-spy` showed the engine stuck at a host sync point in the Gated-DeltaNet forward (`qwen_gdn_linear_attn.py`). The GPU-side kernel never completed.

Isolation matrix (single ~261K request, needle test, 420 s watchdog):

| Engine | Spec decode | Model runner | Result |
|---|---|---|---|
| 0.29.0 | none | V2 (default) | ✅ 260,813 tok, TTFT 114 s, 60.9 tok/s |
| 0.29.0 | MTP-3 | V2 (default) | ❌ **hang** |
| 0.29.0 | MTP-4 | V2 (default) | ❌ **hang** (during the context ladder, after 1K–200K all passed) |
| 0.28.0 | MTP-3 | V1 | ✅ 261,088 tok, TTFT 129 s, 110.1 tok/s |
| 0.28.0 | MTP-4 | V1 | ✅ 260,891 tok, TTFT 118 s, 106.5 tok/s |
| **0.29.0** | **MTP-4** | **V1 (`VLLM_USE_V2_MODEL_RUNNER=0`)** | ✅ **261,116 tok, TTFT 120 s, 115.7 tok/s** |

**Root cause (narrowed down):** vLLM 0.29 made the new **V2 model runner** the default (`Using V2 Model Runner` in the logs), while 0.28 used the V1 `gpu_model_runner`. The hang only occurs with **V2 runner + MTP + a near-max-length prefill**. Forcing the V1 runner on 0.29 removes it.

### Why 0.29 (with the V1 runner) won over 0.28

| MTP-4, same checkpoint | 0.28.0 | 0.29.0 + V1 runner |
|---|---:|---:|
| Short-context decode | 217.8 tok/s | **227.4 tok/s** (+4%) |
| ~261K decode | 106.5 tok/s | **115.7 tok/s** (+9%) |
| KV capacity | 266,488 (@0.975) | **270,833** (@0.98) |

0.29's default V2 runner was fastest on short prompts (234.8 tok/s), but it can't safely serve the full window, so it was rejected.

---

## 4. Memory utilization tuning (Phase 6)

Each speculative-token level adds weight memory (the MTP head) and draft-KV requirements, which sets a **minimum** `gpu-memory-utilization` for holding one full 262,144-token request:

| Config | Util | Boots with 262K? | KV cache tokens | Max concurrency @262K |
|---|---:|---|---:|---:|
| No spec (`main`) | 0.965 | ✅ | 319,393 | 1.22× |
| MTP-2 | 0.965 | ❌ short by 0.04 GiB | — | — |
| MTP-2 | 0.975 | ✅ | 271,080 | 1.03× |
| MTP-3 | 0.975 | ✅ | 266,537 | 1.02× |
| MTP-4 (V2 runner) | 0.975 | ❌ short by 0.10 GiB | — | — |
| MTP-4 (V2 runner) | 0.98 | ✅ | 265,040 | 1.01× |
| MTP-5 | 0.98 | ❌ short by 0.10 GiB | — | — |
| **MTP-4, V1 runner, max-num-seqs 8** | **0.98** | ✅ | **267,937** | **1.02×** |
| MTP-4, V1 runner, max-num-seqs 4 | 0.98 | ✅ | 270,833 | 1.03× |
| MTP-4, V1 runner, max-num-seqs 1 | 0.98 | ✅ | 273,730 | 1.04× |

Notes:
- 0.95 and 0.97 weren't booted separately for the MTP configs. The measured shortfalls above (MTP-2 fails at 0.965; MTP-4 fails at 0.975) already show those settings can't hold the full window with speculation enabled.
- **0.98 is the ceiling allowed by the brief and the setting chosen.** It's stable across a 28-minute soak with 8 full-window requests (§8). 1.0 wasn't used.
- **Actual VRAM:** 30,863 MiB loaded and idle, plateauing at 31,163 MiB after the first full-window request, with a transient peak of 31,666 MiB (of 32,607). That's inside the 30–31+ GB target, with ~0.9 GB of headroom never breached.
- **Operational consequence:** at 0.98, vLLM needs about 30.7 GiB free at startup, so any other process holding more than ~600 MB of VRAM will block the service from starting. The systemd unit therefore waits for the GPU to be empty before launching (`wait-gpu-free.sh`), and Ollama is disabled.

### Prefill chunk size: tested, rejected

With speculative decoding on, vLLM caps prefill chunks at ~2,048 tokens and warns that this "may lead to suboptimal performance". Raising `--max-num-batched-tokens` to 8192 costs about 2.3 GB of activation memory, which drops the KV cache to ~192K tokens, so it **can't be combined with full context on 32 GB**. The default was kept, and prefill numbers in this report reflect that trade-off.

---

## 5. Speculative decoding (Phase 7)

Measured with the same harness each time: 4 workloads (chat, code, JSON, reasoning) × 2 reps, 512 output tokens, temperature 0, thinking off, single stream.

| Config | Decode tok/s | vs no-spec | Chat | Code | JSON | Reason | Mean accept length | Per-position acceptance |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| No spec | 86.2 | 1.00× | 86.0 | 86.4 | 86.2 | 86.2 | — | — |
| MTP-2 | 176.2 | 2.04× | 155.2 | 176.7 | 191.6 | 181.3 | 2.6 | 0.88 / 0.73 |
| MTP-3 | 203.8 | 2.36× | 167.9 | 201.9 | 236.8 | 208.6 | 3.2 | 0.88 / 0.73 / 0.59 |
| **MTP-4** | **234.8** (V2) / **227–230** (V1) | **2.6–2.7×** | 171–180 | 220–231 | 268–280 | 235–251 | **3.7** | 0.90 / 0.74 / 0.61 / 0.48 |
| MTP-5 | — | doesn't fit 262K at 0.98 | | | | | | |

- The no-spec result (86.2 tok/s) matches the model card's vLLM figure (86.8), which validates the harness.
- **MTP-4 beat MTP-3 by ~13–15%.** Each extra position still accepts ~48%, and on this bandwidth-bound card the extra verify tokens are almost free.
- Structured output benefits most (JSON: 3.2× no-spec); free-form chat benefits least (~2×).
- **Correctness:** MTP-3 and MTP-4 were each put through the full functional suite (§7). There were no loops, garbling, broken JSON or broken tool calls. Speculation is verified by the target model, so output quality is unchanged by construction.
- **DSpark drafter** (`Qwen3.8-27B-DSpark-NVFP4`, which the model card says beats MTP): vLLM 0.29 has a `dspark` method, but loading this NVFP4 drafter failed with `RuntimeError: size of tensor a (128) must match ... (256)`. Its card says it requires a custom SGLang build, and it also reduces max context to ~165K, which conflicts with the full-window requirement. It wasn't pursued.
- vLLM warns that `min_p` and `logit_bias` aren't supported with speculative decoding. Temperature, top_p and top_k work normally.

---

## 6. Concurrency (Phase 8)

`ignore_eos`, 512 fixed output tokens per stream, distinct prompts (so no cache help), measured after warmup:

| max-num-seqs | 1 stream | 3 streams (aggregate / per-stream) | 4 streams | 8 streams |
|---:|---:|---|---|---|
| 1 | 221.1 (decode suite) | queued: TTFT up to 5.9 s | — | — |
| 4 | 227.4 | 434 / 206 | 581 / 208–221 | — |
| **8** | **229.7** | **419–453 / 201–206** | **604–629 / 213** | **1,015–1,125 / 189–193** |

**Single-stream speed is flat across max-num-seqs 1/4/8** (differences are within run-to-run noise), so 8 slots cost nothing for interactive use. Only ~6K KV tokens are given up versus 1 slot, and the full 262K window still fits. Per-stream speed stays near 190 tok/s even with 8 simultaneous agents, because MTP verification batches well on a bandwidth-bound GPU. TTFT with 8 concurrent short prompts is 0.31–0.33 s.

Caveat: all 8 slots share one KV pool of ~268K tokens. Eight agents at ~32K each fit together, but a single 262K request uses the whole pool, and other requests wait for it.

---

## 7. Functional correctness (Phase 10)

Suite: `bench/func.py`, 31 checks, run 3+ times on every candidate and on the live service.

| Area | What's checked | Result |
|---|---|---|
| Chat | natural answer, loop/corruption detector | ✅ |
| Coding | generated Python is **executed** and its output compared (primes < 50) | ✅ |
| Reasoning, thinking on | correct answer; reasoning separated into the `reasoning` field; no `<think>` leakage | ✅ |
| Reasoning, thinking off | correct answer, empty reasoning | ✅ |
| Parallel tool calls ×3 rounds ×2 thinking modes | 2× `get_weather`, correct cities and enum arg, `finish_reason=tool_calls` | ✅ 6/6 |
| Tool-result synthesis | answer uses both returned values; no stray tool-call XML | ✅ 6/6 |
| Typed arguments | IATA strings, ISO date, **integer** passengers | ✅ 6/6 |
| Restraint | no tool call when none is needed | ✅ 6/6 |
| Streaming tool call | parser assembles name and JSON args from streamed deltas | ✅ |
| Long multi-turn (~10.6K-token system prompt, 4 turns) | cross-turn arithmetic correct | ✅ |
| Prefix cache | turns 2–4 report 8,000–8,080 cached prompt tokens | ✅ |

**Final live-service result: 31/31.** During the soak, 8 more runs of the 15-check subset also all passed (120/120).

**Prefix-caching detail:** this hybrid model caches in **1,600-token aligned blocks** (vLLM's "align" Mamba cache mode). A 10.6K-token conversation therefore reuses ~8K tokens (76%) per turn, not 100%. For agents that resend a long system prompt and tool manifest, most of the prefix is still reused.

---

## 8. Long context (Phase 9)

Needle-in-a-haystack test: two secret codes, one ~4 sentences from the start and one ~4 sentences from the end, with random filler (a fresh seed each run, so no cache help). The model has to return both codes and then write ~250 words. Figures are from the **live production service**:

| Target | Prompt tokens | TTFT | Prefill tok/s | Decode tok/s | Both needles |
|---|---:|---:|---:|---:|---|
| 1K | 703 | 0.19 s | 3,700 | 144.7 | ✅ |
| 8K | 7,876 | 0.89 s | 8,881 | 138.7 | ✅ |
| 32K | 32,310 | 4.15 s | 7,790 | 143.7 | ✅ |
| 64K | 65,151 | 11.35 s | 5,740 | 125.6 | ✅ |
| 128K | 130,611 | 34.8 s | 3,752 | 140.6 | ✅ |
| 200K | 199,380 | 73.5 s | 2,714 | 119.3 | ✅ |
| **~256K (max)** | **260,961** | **119.5 s** | **2,185** | **107.9** | ✅ |

For reference, the same ~261K prompt **without** speculation decoded at 60.9 tok/s, so MTP-4 gives 1.77× even at full context.
Decode numbers in this table are lower than the 226 tok/s headline because the output here is free-form prose (the workload where MTP accepts least), not because of context length. The lower 64K row is run-to-run content variance. Across the 8 soak cycles, ~261K decode ranged 100.2–111.8 tok/s.

---

## 9. Sustained load and thermals (Phase 12)

**Soak:** 28 minutes, 8 full cycles. Each cycle ran the decode suite, 3- and 4-way concurrency, 32K/64K/128K needle tests, the functional suite, and **one ~261K-token request**. GPU telemetry was sampled every 2 s (834 samples).

| Metric | Result |
|---|---|
| Full-window (~261K) requests | **8/8 completed**, TTFT 119.5–119.9 s, decode 100.2–111.8 tok/s, needles found in all |
| Functional checks | 120/120 (8 × 15-check subset) |
| Decode drift across cycles | 225.6–230.4 tok/s (no degradation) |
| VRAM | 30,067 → plateau **31,163 MiB** from cycle 4 onward; peak 31,666. **No leak or growth.** |
| OOMs, CUDA errors, tracebacks, worker crashes | **0** |
| GPU temperature | avg 63 °C, **max 69 °C**, no thermal slowdown |
| Power | busy avg 479 W, max 511 W; hitting the host's **500 W cap** |
| SM clock under load | avg 2,800 MHz, min 2,580 MHz (memory clock steady at 13,801 MHz) |

**Power-cap finding:** `SW Power Cap` was active in 654 of 791 busy samples, all during heavy **prefill** (compute-bound). Decode is memory-bandwidth-bound, draws ~380–400 W, and isn't affected. Raising the host limit toward the 575 W default would likely speed up long-prompt prefill somewhat. That's a host (Proxmox) power decision and was deliberately **not** changed.

---

## 10. Deployment (Phase 14)

- **Service:** systemd `vllm-qwen38.service` (enabled at boot, `Restart=always`) wrapping Docker Compose `/opt/vllm-qwen38/compose.yaml`.
- **Image pinned:** `vllm/vllm-openai:v0.29.0` (`sha256:c2914767605584b6d8f45686b82de173ecc99e781897aa3d0a66dacd72c51ae1`).
- **Port 11434**, the same port as the old Ollama service. OpenAI-style clients that pointed at `http://<host>:11434/v1` keep working; only the model name changes, to `qwen3.8-27b`. (Ollama's native `/api/*` endpoints no longer exist.)
- **Persistence:** model weights live in `/models` (mounted read-only). FlashInfer JIT and torch.compile caches live in `/opt/vllm-qwen38/cache`, so a warm restart takes about 2.5 minutes versus about 6 on a cold first boot. Logs go to persistent journald (`journalctl -u vllm-qwen38`), with a Docker json-file copy (5×100 MB rotation).
- **Hardening added during cutover:**
  1. `wait-gpu-free.sh` runs pre-start. The first cutover attempt failed because a just-removed test container hadn't released its VRAM yet.
  2. `--abort-on-container-exit --exit-code-from vllm`, so an engine failure propagates to systemd and triggers a restart.
  3. The warmup runs in the background. It pre-exercises 1/2/3/4/6/8-way batch shapes, which removes a one-time ~2 s TTFT on the first concurrent batch.
  4. `init: true` (tini as PID 1). Without it, `docker kill` hit a zombie PID 1.
- **Ollama:** `systemctl disable --now ollama`. The binary, unit and gemma3 model are all untouched, kept for rollback.
- **Validated:** a forced `docker kill` of the live container led to an automatic restart, healthy in 176 s. A full LXC reboot couldn't be tested from inside the session; the service and Docker are both enabled at boot.

---

## 11. Rejected alternatives and why

| Alternative | Why it lost |
|---|---|
| vLLM 0.29 default (V2 runner) + MTP | Fastest short-context decode (234.8 tok/s), but **hangs on full-window prompts**. Correctness and stability come first. |
| vLLM 0.28.0 + MTP-4 | Stable, but 4–9% slower than 0.29 with the V1 runner. It's the standby fallback. |
| MTP-3 | Stable, but ~13–15% slower than MTP-4 at equal correctness. |
| MTP-2 | Slower still (176 tok/s). |
| MTP-5 | Can't hold 262K within the 0.98 utilization cap. |
| No speculation | 86 tok/s, 2.6× slower. Its only advantage is more KV headroom (1.22×). |
| DSpark drafter | Doesn't load on vLLM 0.29, and would cap context at ~165K anyway. |
| `max-num-batched-tokens 8192` | Faster prefill in theory, but max context drops to ~192K. |
| max-num-seqs 1 or 4 | Same single-stream speed as 8, but less agent concurrency. |

---

## 12. Rollback procedure

```bash
# 1. Stop and disable vLLM (frees the GPU and port 11434)
systemctl disable --now vllm-qwen38
docker compose -f /opt/vllm-qwen38/compose.yaml down

# 2. Restore and start Ollama (unit file is unchanged; backup copy in /root/llm-upgrade/backup/)
systemctl enable --now ollama
ollama list    # gemma3:27b still present
curl http://127.0.0.1:11434/api/tags
```

**Fallback without leaving vLLM:** if a future vLLM issue appears, change `image:` in `compose.yaml` to `vllm/vllm-openai:v0.28.0` (already pulled; it uses the V1 runner natively) and set `--gpu-memory-utilization=0.975` and `--max-num-seqs=4`, the validated 0.28 settings. Then run `systemctl restart vllm-qwen38`.

---

## 13. Reproducibility and artifacts

Everything is in `/root/llm-upgrade/`:

| Path | Contents |
|---|---|
| `audit/phase1_audit.txt` | full pre-change audit (`nvidia-smi -q`, OS, services) |
| `backup/ollama-rollback-*` | original Ollama unit(s), binary, daemon.json, shell history |
| `bench/bench.py` | benchmark harness (decode / ctx needle / concurrency + GPU sampler) |
| `bench/func.py` | 31-check functional/tool-calling suite |
| `bench/soak.sh` | sustained-load test |
| `bench/launch.sh`, `run_decode_cfg.sh`, `ctx_probe.sh`, `full_probe.sh` | config-sweep drivers |
| `results/bench.jsonl`, `results/func.jsonl` | every raw result, one JSON line per run |
| `results/gpu_*.jsonl`, `results/final_gpu_trace.csv` | GPU telemetry per run |
| `results/soak_final-candidate/` | soak GPU trace, `nvidia-smi -q` after soak |
| `logs/vllm_*.log`, `logs/HANG_*` | server logs for every config, plus hang evidence (log and py-spy stack) |
| `deploy/` | copies of the production compose file, unit and scripts |
| `OPTIMAL_CONFIG.md` | one-page summary of the winning configuration |

---

## 14. How long it took and what it cost: the AI engineer

The whole engagement (audit, benchmarking, debugging, tuning, deployment, validation and this report) was carried out autonomously by **Claude Code running Claude Opus 5 (1M context)** in a terminal session on the host. A human set the brief and made two course corrections (skip further Ollama baselining; a change to the host access method). Figures below come from the session's own transcript log (`~/.claude/projects/-root/<session>.jsonl`). No sub-agents were used.

### Wall-clock time

| Phase | Time (UTC) | Duration |
|---|---|---|
| Audit, backups, downloads started, harness written | 13:48–13:55 | ~7 min |
| Ollama baseline | 13:55–14:00 | ~5 min |
| First vLLM boot (image pull + FlashInfer JIT) | 14:00–14:07 | ~7 min |
| MTP-2/3/4/5 and DSpark sweep, functional suites | 14:07–14:55 | ~48 min |
| 261K hang: detection, py-spy, isolation matrix, 0.28 comparison, V1-runner fix | 14:55–15:52 | ~57 min |
| max-num-seqs 1/4/8 and concurrency | 15:52–16:09 | ~17 min |
| 28-minute sustained soak | 16:09–16:38 | ~29 min |
| Cutover, unit hardening, prefill-chunk test, crash-recovery tests | 16:38–17:10 | ~32 min |
| Final post-deployment validation (functional, decode, concurrency, 1K→261K ladder) | 17:10–17:17 | ~7 min |
| Report and configuration summary written | 17:17–17:20 | ~3 min |
| **Total, brief to finished report** | **13:48:47 → 17:19:45** | **3 h 31 min** |

Most of that time was spent **waiting on the GPU**, not on reasoning. Every configuration change needs a 3–6 minute engine boot, and a single full-window prompt takes ~2 minutes just to prefill. Model downloads (~38 GB) ran in parallel with the baseline.

### Token spend

| | Up to finished report | Whole session (incl. SSH set-up and this addendum, at time of writing) |
|---|---:|---:|
| Model API calls | 109 | 119+ |
| Output tokens (generated by the model) | 82,548 | ~88,500+ |
| Fresh input tokens | 218 | ~240 |
| Cache-write input tokens | 170,294 | ~177,000 |
| Cache-read input tokens | 13,717,604 | ~15.8 M |
| **Total tokens processed** | **~13.97 M** | **~16.1 M** |

How to read this: an agent re-sends its growing working context (tool outputs, logs, code) on every step. Prompt caching lets ~98% of those input tokens be served as **cheap cache reads** rather than full-price input. The model itself only *wrote* about 83K tokens, the equivalent of roughly 60–70 pages of text including all scripts, configs and this report. Dollar cost depends on the account's plan and pricing tier, so it isn't stated here.
