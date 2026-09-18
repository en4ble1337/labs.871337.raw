# Optimal configuration: Qwen3.8-27B on RTX 5090 (32 GB)

Validated 2026-09-18 on a Proxmox LXC container ( driver 580.82.09, host power cap 500 W). The full report is in `REPORT.md`.

## The winning stack

| Setting | Value | Why |
|---|---|---|
| Engine | **vLLM 0.29.0** (`vllm/vllm-openai:v0.29.0`, digest `sha256:c2914767…51ae1`) | 4–9% faster than 0.28.0 |
| Model runner | **V1** (`VLLM_USE_V2_MODEL_RUNNER=0`) | the default V2 runner **hangs** on MTP + ~261K prompts |
| Checkpoint | `gittensor-model-hub/Qwen3.8-27B-NVFP4-RTX5090`, **branch `pre-final`** @ `35fd99fb8434ef70ee61372df3face5174bed2c8` | `main` deleted the MTP head |
| Quantization | ModelOpt NVFP4 (W4A4, incl. `lm_head`) | |
| KV cache | FP8 | |
| Speculative decoding | `{"method":"mtp","num_speculative_tokens":4}` | 2.6× no-spec; beats MTP-3 by ~13–15%; MTP-5 won't fit 262K |
| max-model-len | **262,144** | full native window, verified with a needle test |
| max-num-seqs | **8** | same single-stream speed as 1, up to 1,125 tok/s aggregate |
| gpu-memory-utilization | **0.98** | MTP-4 + 262K needs ≥ 0.98 |
| KV capacity | **267,937 tokens** (1.02× the full window) | |
| Loaded VRAM | 30.9 GB (plateau 31.2 GB, peak 31.7 GB of 32.6 GB) | |
| Prefix caching | on (hybrid "align" mode, 1,600-token blocks) | |
| Parsers | `--reasoning-parser qwen3`, `--enable-auto-tool-choice --tool-call-parser qwen3_xml` | |
| Prefill chunking | vLLM default (~2K with spec decode) | 8K chunks would cut max context to ~192K |

## Measured performance (live service, port 11434)

| Metric | Value |
|---|---|
| Decode, single stream (chat / code / JSON / reasoning) | **226 tok/s** avg (168 / 214 / 274 / 249) |
| Decode at ~1K / 8K / 32K / 64K / 128K / 200K / ~256K (prose) | 145 / 139 / 144 / 126 / 141 / 119 / 108 tok/s |
| Prefill at 8K / 32K / 128K / ~256K | 8,881 / 7,790 / 3,752 / 2,185 tok/s |
| TTFT at short / 32K / 128K / ~256K | 0.10 s / 4.1 s / 34.8 s / 119.5 s |
| 3 / 4 / 8 concurrent | 419–453 / 604–629 / 1,015–1,125 tok/s aggregate (≈190–213 per stream) |
| vs old Ollama + gemma3:27b | decode **+245%**, prefill **+132–192%**, context **8×** (different model; see report caveat) |

## Stability

- Full 262K context works; 8/8 full-window requests succeeded in the 28-minute soak.
- Tool calls, reasoning on/off, streaming tools and prefix caching all pass (31/31, plus 120/120 during the soak).
- No OOMs, CUDA errors or VRAM growth. Max temperature 69 °C. The only throttle is the host's 500 W cap, during prefill.

## Endpoint

| | |
|---|---|
| Base URL (LAN) | `http://<host-ip>:11434/v1` |
| Base URL (local) | `http://127.0.0.1:11434/v1` |
| Model name | `qwen3.8-27b` |
| API key | none required (any non-empty string works for SDKs that insist on one) |
| Context window | 262,144 tokens (input and output share it) |
| Endpoints | `/v1/chat/completions`, `/v1/completions`, `/v1/models`, `/tokenize`, `/health`, `/metrics` |

Per-request options (via `extra_body`):
- Disable thinking: `{"chat_template_kwargs": {"enable_thinking": false}}`. Thinking is on by default, and the reasoning text is returned in `message.reasoning`.
- Reasoning effort: `{"chat_template_kwargs": {"reasoning_effort": "xhigh"|"medium"|"low"}}` (default `xhigh`).
- Recommended sampling (the model's `generation_config`): `temperature 1.0, top_p 0.95, top_k 20`. `min_p` and `logit_bias` are ignored under speculative decoding.

## Operations

```bash
systemctl status vllm-qwen38                 # service state
journalctl -u vllm-qwen38 -f                 # logs (persistent)
systemctl restart vllm-qwen38                # ~2.5 min to healthy (warm cache)
curl http://127.0.0.1:11434/v1/models
```

Files: `/opt/vllm-qwen38/{compose.yaml,vllm-qwen38.service,warmup.sh,wait-gpu-free.sh}` (copies in `/root/llm-upgrade/deploy/`).
Nothing else may hold more than ~600 MB of VRAM at startup, or the 0.98 allocation fails. The unit waits for the GPU to be free before starting.

## Rollback

```bash
systemctl disable --now vllm-qwen38 && systemctl enable --now ollama
```
