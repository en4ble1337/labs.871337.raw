# Why Should You Run Your GPU Inference Box on Proxmox LXC Instead of a VM?

Artifacts for the Labs article of the same name.

**Tested:** 2026-10-07. **Subject:** one RTX 5090 shared by several LXC containers on a Proxmox VE host.

## The finding, in short

Whoever owns the GPU driver decides who can use the GPU. On Proxmox, keeping the driver on the host lets many containers open one card, and lets every experiment get a near-instant rollback.

- 7 of 8 containers on the host can open the GPU. 5 were running with it open at the same time. The host can tell which container owns each GPU process.
- A snapshot of a 500 G container took about 1.2 s. Rolling back a deliberately broken upgrade took 1.3 s and restored the exact original file.
- Sharing access is not sharing memory. With a client workload holding 27 GB, the lab vLLM container (needs ~30.7 GiB free) cannot start.
- A privileged GPU container was able to set the host-wide GPU power limit (tested as a no-op).
- GPU load, the sharing test, and the swap-time test were **not run**, because a client was using the GPU.

## Contents

| File | What it is |
| --- | --- |
| [`REPORT.md`](REPORT.md) | The full evidence report: audit, driver lock-step, GPU settings control, snapshot and rollback timing, documentation checks, what was skipped and why. |
| [`agent_brief.md`](agent_brief.md) | The prompt given to the agent that ran the tests. The run deviated from it; `REPORT.md` section 0 explains how. |
| [`raw/`](raw/) | Every command output, one file per step. Sanitized. |
| [`scripts/`](scripts/) | The GPU load script and the "serving" poll script. Written for the sharing and swap tests, **not run** in this session. |

## Not tested here

The sharing test under load (throughput alone vs together), the VRAM contention failure, and the production-to-test swap timing all need GPU load and downtime. The scripts are included so they can be run in a maintenance window.
