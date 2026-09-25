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
| [`bench/`](bench/) | The measurement harness that produced every number in the report. See *Running the harness* below. |
| [`deploy/`](deploy/) | The production deployment: Docker Compose file, systemd unit, GPU-free wait gate, and batch-shape warmup. |

## Running the harness

The harness is the durable part of this set: the numbers belong to one machine, but the loop that produced them can be re-run on yours. It targets any OpenAI-compatible endpoint; `launch.sh` and the probe scripts additionally assume Docker, the NVIDIA container toolkit, and the `vllm/vllm-openai` image.

| Script | What it does |
| --- | --- |
| `bench.py` | Streaming benchmark. `decode` (fixed chat/code/json/reasoning workloads), `ctx` (needle-in-a-haystack at a target context size, retrieval checked), `conc` (N concurrent streams). Samples GPU telemetry via `nvidia-smi` throughout. |
| `func.py` | Functional gate: chat, executed code, reasoning with thinking on and off, parallel/typed/streaming tool calls, tool restraint, multi-turn correctness, and prefix-cache hits. Exit code is the number of failed checks. |
| `launch.sh` | Starts one vLLM configuration in a container and waits until it serves or dies. |
| `run_decode_cfg.sh` | Launch, decode benchmark, and speculative-decoding acceptance stats for one configuration. The unit of the tuning sweep. |
| `ctx_probe.sh` / `full_probe.sh` | Launch plus a near-max-context request under a 420 s watchdog. This is how the long-context hang was caught. |
| `soak.sh` | Sustained mixed workload against a running server, with throttle, clock, temperature, and VRAM drift summaries. |

```bash
pip install -r bench/requirements.txt
python3 bench/bench.py decode --base http://127.0.0.1:8000 --model <served-model-name> --label my-baseline
python3 bench/func.py my-baseline --base http://127.0.0.1:8000 --model <served-model-name>
```

Environment overrides: `RESULTS_DIR` (default `results/`), `PY` (Python interpreter for the shell scripts, default `python3`), `MODELS_DIR` (host model directory for `launch.sh`, default `/models`), `DOCKER_ENV` (extra `docker run` flags, e.g. `-e VLLM_USE_V2_MODEL_RUNNER=0`). Model name, served name, context sizes, and `--tok-per-sentence` (tokenizer-specific, calibrated at 38.64 for this model) are set for this engagement; change them for yours.

The `deploy/` files are this host's production setup, published as a worked example rather than a default. They expect to be installed under `/opt/vllm-qwen38/`.

## How to read the numbers

**Read the environment first.** These figures are correct for one machine, one model revision, one engine version, and one sampling configuration. They are not a specification for the hardware.

Three caveats that materially change how the results should be read, all stated in full in the documents:

1. The decode tables were measured at `temperature 0`. At the model's recommended sampling, fewer drafted tokens are accepted, so real-world gains are lower than the headline. The long-context prose runs are the more conservative guide.
2. Tuning uses the hardware better; it does not make it faster. The card's bandwidth ceiling still applies. Prompt processing got up to 5% slower in exchange for the decode gain.
3. The before/after comparison against the previous stack involves two different models with different tokenizers. It describes the change in the serving stack as experienced, not the same weights on two engines. The same-model comparisons are the cleaner engineering data.

## Sanitization

Host names, the LAN address, and host access details were removed. Placeholders such as `<host-ip>` mark where an address was. Loopback and bind addresses (`127.0.0.1`, `0.0.0.0`) are kept, since they are not identifying.

The harness scripts were changed only for portability: hardcoded working-directory paths became relative paths with environment overrides, and `func.py` gained a `--model` flag. Measurement logic, workloads, and defaults are unchanged from the runs in the report.

No measured value was altered.
