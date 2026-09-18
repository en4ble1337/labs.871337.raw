# Stop Tuning Flags You Haven't Measured: Find the Bottleneck First

Artifacts for the Labs article of the same name.

**Tested:** 2026-09-18. **Subject:** a 27B NVFP4 model served on a single 32 GB consumer GPU, moved from Ollama to a tuned vLLM stack.

## The finding, in short

Decode on this model is bound by memory bandwidth: every generated token reads the full weight set. That single fact decides which tuning knobs can possibly help.

- Stock `vllm serve` with no flags does not start. Its defaults are sized for datacenter GPUs and it runs out of memory during startup.
- The model card's published command works, and leaves most of the performance unused.
- Ordinary flags (memory utilization, batch size, parsers) changed speed **not at all**. Two configurations differing across all of them measured identical within noise.
- One decision produced the 2.6x gain: MTP speculative decoding, which changes the arithmetic by producing several verified tokens per weight read.
- Most of the remaining effort bought reliability rather than speed, and none of it appears in the throughput tables.

## Contents

| File | What it is |
| --- | --- |
| [`REPORT.md`](REPORT.md) | The full engagement: audit, baseline, engine selection, the near-max-context hang and its root cause, memory tuning, speculative decoding sweep, concurrency, functional suite, long context, soak, deployment, rejected alternatives, and the time and token cost. |
| [`APPENDIX-A_vanilla-vs-tuned-vllm.md`](APPENDIX-A_vanilla-vs-tuned-vllm.md) | Stock vs model-card vs tuned, all three run back to back on identical hardware, weights, and scripts. The cleanest comparison in the set. |
| [`OPTIMAL_CONFIG.md`](OPTIMAL_CONFIG.md) | The final configuration on one page, with the reasoning for each setting, plus operations and rollback. |

## How to read the numbers

**Read the environment first.** These figures are correct for one machine, one model revision, one engine version, and one sampling configuration. They are not a specification for the hardware.

Three caveats that materially change how the results should be read, all stated in full in the documents:

1. The decode tables were measured at `temperature 0`. At the model's recommended sampling, fewer drafted tokens are accepted, so real-world gains are lower than the headline. The long-context prose runs are the more conservative guide.
2. Tuning uses the hardware better; it does not make it faster. The card's bandwidth ceiling still applies. Prompt processing got up to 5% slower in exchange for the decode gain.
3. The before/after comparison against the previous stack involves two different models with different tokenizers. It describes the change in the serving stack as experienced, not the same weights on two engines. The same-model comparisons are the cleaner engineering data.

## Sanitization

Host names, the LAN address, and host access details were removed. Placeholders such as `<host-ip>` mark where an address was. Loopback and bind addresses (`127.0.0.1`, `0.0.0.0`) are kept, since they are not identifying.

No measured value was altered.
