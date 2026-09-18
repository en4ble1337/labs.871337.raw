# Appendix A: out-of-the-box vLLM vs the tuned deployment

Companion to `REPORT.md`. Same hardware (RTX 5090 32 GB, 500 W host cap), same engine image (vLLM 0.29.0), same Qwen3.8-27B NVFP4 weights, **same benchmark scripts run back to back on 2026-09-18** (17:55–18:45 UTC). The only variable is the serving configuration.

## The three configurations

| | Command-line flags | Checkpoint |
|---|---|---|
| **A0: pure vanilla** | `vllm serve /models/Qwen3.8-27B-NVFP4-RTX5090` (nothing else) | `main` |
| **A: vanilla, minimum to boot** | A0 + `--max-num-seqs 16` | `main` |
| **B: model-card recipe** | `--quantization modelopt --kv-cache-dtype fp8 --trust-remote-code --max-model-len 262144 --max-num-seqs 16 --gpu-memory-utilization 0.97 --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_xml` | `main` |
| **C: tuned (production)** | `VLLM_USE_V2_MODEL_RUNNER=0` + `--quantization modelopt --kv-cache-dtype fp8 --trust-remote-code --max-model-len 262144 --max-num-seqs 8 --gpu-memory-utilization 0.98 --enable-prefix-caching --enable-prompt-tokens-details --speculative-config '{"method":"mtp","num_speculative_tokens":4}' --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_xml` | `pre-final` (MTP head) |

B is what most people would deploy: it's the exact vLLM command published on the model's Hugging Face card.

## A0: pure vanilla doesn't start

```
torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 768.00 MiB.
GPU 0 has a total capacity of 31.36 GiB of which 137.00 MiB is free.
  (in profile_cudagraph_memory → qwen_gdn_linear_attn._forward_core)
```

vLLM auto-detected the right things: NVFP4 (`modelopt_fp4`), FP8 KV from the checkpoint, the native 262,144-token length, and prefix caching. But its defaults are sized for datacenter GPUs, with CUDA graphs captured up to batch 512. Profiling those graphs on a 32 GB card runs out of memory after a ~3.5-minute startup. **Out of the box, vLLM doesn't serve this model on an RTX 5090.** Adding `--max-num-seqs 16` (config A) is enough to boot, and vLLM then picks 0.92 memory utilization, which happens to hold the full window.

## Headline

| Metric | A: vanilla | B: model card | **C: tuned** | C vs B |
|---|---:|---:|---:|---:|
| Boots with zero flags? | ❌ OOM | — | — | |
| Single-stream decode (4-workload avg) | 86.3 tok/s | 86.5 tok/s | **223.1 tok/s** | **+158% (2.58×)** |
| Decode at ~256K context | 61.0 tok/s | 60.6 tok/s | **114.6 tok/s** | **+89%** |
| 8 concurrent: aggregate | 581 tok/s | 583 tok/s | **1,085 tok/s** | **+86%** |
| 8 concurrent: per stream | 76.5 tok/s | 76.7 tok/s | **190.5 tok/s** | **+148%** |
| TTFT, short prompt | 0.18 s | 0.18 s | **0.10–0.11 s** | −40% |
| TTFT, ~256K prompt | 114.0 s | 114.3 s | 120.1 s | **+5% (slower)** |
| Functional suite (31 checks) | **4/31** | 30/31 | **31/31** | |
| Tool calling | ❌ HTTP 400 | ✅ | ✅ | |
| Reasoning separated from answer | ❌ leaks into `content` | ✅ | ✅ | |
| KV cache capacity | 272,690 | 322,406 | 267,937 | −17% |
| VRAM loaded | 28.5 GB | 30.1 GB | 30.9 GB | |

**Summary:** tuning more than doubles generation speed at every context length and concurrency level tested, and it's the only configuration that passes every functional check. It costs up to ~5% in prompt processing (prefill) and ~17% of KV capacity, and still holds the full 262K window.

## Single-stream decode by workload (512 tokens, temp 0, thinking off, 2 reps)

| Workload | A: vanilla | B: model card | C: tuned | C vs B |
|---|---:|---:|---:|---:|
| Chat (prose) | 86.1 | 86.6 | **178.0** | +105% |
| Code | 86.3 | 86.8 | **224.7** | +159% |
| JSON | 86.8 | 86.1 | **258.9** | +201% |
| Reasoning | 86.2 | 86.4 | **230.8** | +167% |
| **Mean** | **86.3** | **86.5** | **223.1** | **+158%** |

A and B are identical within noise. Without speculative decoding, the flags only change features and memory, not speed. The model is weight-bandwidth-bound at ~86 tok/s either way. **All of the speed gain comes from MTP-4 speculative decoding**, which is why the structured workloads (JSON, code), where drafted tokens are accepted most often, gain the most.

## Concurrency (512 fixed output tokens per stream, `ignore_eos`, mean of 2 runs)

| Streams | A aggregate | B aggregate | **C aggregate** | C vs B | B per-stream | **C per-stream** |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 83.6 | 83.7 | **165.1** | +97% | 85.9 | **170.5** |
| 3 | 213.9 | 214.2 | **450.5** | +110% | 75.0 | **213.5** |
| 4 | 283.1 | 281.6 | **566.7** | +101% | 74.0 | **206.4** |
| 8 | 580.8 | 582.9 | **1,084.8** | +86% | 76.7 | **190.5** |

At 8 concurrent agents, each agent in the tuned config still gets ~190 tok/s, more than **double the single-user speed of stock vLLM**. (The 1-stream row uses the chat prompt, which is MTP's weakest workload, so it sits below the 223 tok/s mean.)

## Long context (needle test: both codes retrieved in every run, all configs)

| Context | Prompt tok | Decode A | Decode B | **Decode C** | C vs B | Prefill B | Prefill C | TTFT B | TTFT C |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1K | ~705 | 86.4 | 86.1 | **142.6** | +66% | 3,931 | 3,748 | 0.18 s | 0.19 s |
| 8K | ~7.8K | 86.5 | 85.9 | **149.0** | +74% | 8,442 | 8,836 | 0.93 s | 0.89 s |
| 32K | ~32.4K | 82.5 | 82.4 | **142.1** | +72% | 7,997 | 7,759 | 4.05 s | 4.18 s |
| 64K | ~65.1K | 78.9 | 79.1 | **135.3** | +71% | 6,009 | 5,761 | 10.8 s | 11.3 s |
| 128K | ~130.6K | 72.0 | 71.5 | **120.9** | +69% | 3,883 | 3,756 | 33.6 s | 34.8 s |
| 200K | ~199.4K | 65.5 | 66.0 | **119.7** | +81% | 2,835 | 2,710 | 70.3 s | 73.6 s |
| ~256K | ~261K | 61.0 | 60.6 | **114.6** | +89% | 2,282 | 2,175 | 114.3 s | 120.1 s |

The output here is ~250 words of free-form prose, the workload where MTP accepts the fewest drafted tokens, so gains are smaller than on the decode suite. **The speed-up grows with context length:** without speculation, decode falls from 86 to 61 tok/s as the prompt grows (−30%), while the tuned config falls from 143 to 115 (−20%).

**The honest trade-off is prefill:** the tuned config processes prompts 3–5% slower at most sizes (it was 5% faster at 8K). MTP adds draft work, and speculative decoding makes vLLM cap scheduling chunks at ~2K tokens. Larger chunks would need ~2.3 GB more memory and break the 262K window (see REPORT.md §4). For an interactive agent that generates hundreds or thousands of tokens per turn, the 2× decode gain far outweighs 5% on prefill.

## Functional correctness

| Check group | A: vanilla | B: model card | C: tuned |
|---|---|---|---|
| Chat, coding (executed), reasoning with thinking off | ✅ | ✅ | ✅ |
| Reasoning with thinking on | ❌ chain-of-thought dumped into `content` | ✅ | ✅ |
| Tool calls: parallel / typed / synthesis / restraint / streaming (25 checks) | ❌ all fail: `400 "auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser` | ✅ | ✅ |
| Multi-turn correctness (~10.6K-token system prompt) | ✅ | ✅ | ✅ |
| Prefix-cache hits reported to the client | ❌ not reported* | ❌ not reported* | ✅ 8,080 of ~10.6K tokens reused per turn |
| **Total** | **4/31** | **30/31** | **31/31** |

\*Prefix caching is **on** by default in A and B. They just don't report `cached_tokens` to clients without `--enable-prompt-tokens-details`, so reuse can't be verified from the API. C enables it, making cache effectiveness observable.

**Stock vLLM is not usable for agent frameworks** (Hermes, OpenAI Agents SDK, LangChain tools and so on): every tool request is rejected with HTTP 400, and thinking-mode output leaks raw reasoning into the answer.

## Memory

| | A: vanilla | B: model card | C: tuned |
|---|---:|---:|---:|
| gpu-memory-utilization | 0.92 (auto) | 0.97 | 0.98 |
| Weights on GPU | 17.1 GiB | 17.1 GiB | 17.9 GiB (+ MTP head) |
| KV cache tokens | 272,690 (1.04×) | 322,406 (1.23×) | 267,937 (1.02×) |
| VRAM loaded (idle) | 28.5 GB | 30.1 GB | 30.9 GB |
| VRAM peak during this run | 31.1 GB | 31.7 GB | 32.1 GB |

The tuned config deliberately spends the extra memory on speculation (the MTP head and draft KV) instead of spare KV capacity. B can keep 1.23 full windows in cache; C keeps 1.02. For one to eight agents with typical 10–60K contexts, that spare capacity goes unused, while MTP speeds up every single token.

**Note:** C's transient peak in this run (32,100 of 32,607 MiB during the ~256K request) was ~430 MB higher than the 31,666 MiB peak seen in the 28-minute soak. It completed without error, but it confirms that 0.98 is the practical ceiling on this card; don't go higher.

## Where the ~3.5 hours of tuning went, and what each piece bought

| Tuning step | Effect vs model-card recipe (B) |
|---|---|
| Switch to `pre-final` checkpoint (restores the MTP head the `main` branch deleted) | prerequisite for everything below |
| MTP speculative decoding, 4 tokens (tested 2/3/4/5) | **+158% single-stream decode**, +86–110% concurrent throughput |
| `gpu-memory-utilization` 0.97 → 0.98 | needed for MTP-4 to still fit the 262K window |
| `VLLM_USE_V2_MODEL_RUNNER=0` | avoids a **hang on ~256K prompts** with MTP in vLLM 0.29 (found only by testing near-max context) |
| `max-num-seqs` 16 → 8 | frees KV for MTP; no single-stream cost; 8 concurrent agents |
| `--enable-prompt-tokens-details` | makes prefix-cache reuse observable to clients |
| systemd + wait-for-free-GPU + warmup + tini | stability and ops (not reflected in these numbers) |

## Conclusion: is tuning critical?

**Yes, but with a qualification that matters. The data shows configuration mattering as much as the hardware, with only a few settings doing the heavy lifting.**

1. **Out of the box, it doesn't run.** Plain `vllm serve` hits an out-of-memory error during startup on a 32 GB card (A0).
2. **The model-card recipe works but leaves most of the performance unused.** It decodes at ~86 tok/s, the card's ceiling *without* speculation. Decode on this model is bound by memory bandwidth (each token reads the full ~18 GB of weights), so **none of the ordinary flags changed speed at all**: memory utilization, batch size and parsers left A and B identical within noise.
3. **One decision produced the ~2.6× gain: MTP speculative decoding.** It depended on three findings:
   - The recommended checkpoint (`main`) had the MTP head deleted, so the `pre-final` branch was needed.
   - 4 draft tokens was the sweet spot (2, 3, 4 and 5 were tested).
   - `gpu-memory-utilization` had to rise to 0.98 for MTP-4 to still fit the full 262K window.
4. **Most of the remaining effort bought reliability, not speed.**
   - Finding the vLLM 0.29 hang, which only appears near 256K context with MTP on.
   - Enabling tool-call and reasoning parsing (stock vLLM passes 4/31 functional checks).
   - Hardening restarts: waiting for a free GPU, tini as PID 1, warmup.

   None of this shows in the speed tables, but without it the fast configuration would freeze on a long agent session or reject every tool call.

> **In one sentence:** tuning is the difference between a card that can't start the model and one that serves it at 223 tok/s. Most of that gain came from a single feature, speculative decoding, and most of the tuning time went into making it stable at full context.

### Caveats

- **Scope:** one machine, one model, one engine version. A model without an MTP head, or a future vLLM release (for example, one that fixes the V2-runner hang or changes defaults), could shift these conclusions.
- **Sampling temperature:** the decode suite runs at `temperature=0`. At the model's recommended sampling (`temperature 1.0, top_p 0.95, top_k 20`), fewer drafted tokens are accepted, so real-world speed-ups will likely be **somewhat below 2.6×**. The long-context prose runs (+66–89%) are a more conservative guide for everyday chat; structured output (JSON, code, tool calls) sits at the high end.
- **Tuning uses the hardware better; it doesn't make it faster.** The card's ~1.8 TB/s bandwidth ceiling still applies. Speculation works around it by producing several verified tokens per weight read. It does nothing for prefill, which got up to ~5% slower in exchange.
- **Headroom is thin:** C runs at 0.98 memory utilization with 1.02× the full-window KV capacity and a measured transient peak of 32.1 of 32.6 GB. It's stable in every test run here, but there's no room left to push further on this card.

## Raw data

- `logs/cmp_A1.out`, `logs/cmp_B.out`, `logs/cmp_C.out`: console output of the three identical runs
- `logs/vllm_A-vanilla.log`: OOM trace for pure vanilla
- `logs/vllm_A1-vanilla-seqs16.log`, `logs/vllm_B-modelcard.log`: server logs
- `results/bench.jsonl`, `results/func.jsonl`: every measurement, labels `A1-vanilla-seqs16*`, `B-modelcard*`, `C-tuned-prod*`
- `bench/fullbench.sh`: the exact script run against all three
