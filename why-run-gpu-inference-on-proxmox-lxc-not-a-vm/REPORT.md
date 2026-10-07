# LXC GPU sharing on Proxmox: evidence report

Date: 2026-10-07. Single host, one NVIDIA GeForce RTX 5090 (32 GB).

Each finding is labelled:

- **[measured]**: taken from command output in `raw/`.
- **[calculated]**: derived from the measured values.
- **[inferred]**: my interpretation, not directly measured.
- **[docs]**: from documentation, not tested here.

## 0. How the brief changed on this box (read first)

The brief assumed three things: one production vLLM container, free container IDs for clones, and permission to load the GPU. The audit found otherwise:

- **Production is `102`.** It is a container hosting a GPU-marketplace rental. At audit time it held 27,150 MiB of VRAM through a renter's Docker workload, which is not vLLM. The owner confirmed it is production and asked that the GPU not be stress-tested while it runs.
- **The vLLM container is `105`.** It is a lab container whose vLLM service has been stopped since 2026-09-18. The owner allowed it to be used for testing, along with `104`.
- **No free IDs were authorized**, so no clones were made.
- **No GPU load was run at all.**

What this means for each phase:

- **Phases 1–3 and 7:** done.
- **Phase 4:** snapshots only, taken on `105`. Backup and clone were not run.
- **Phase 5:** skipped.
- **Phase 6:** only the snapshot-rollback part, on `105`.
- **Phase 8:** done on `102`. It checks config and state only, not inference speed, because production is not a vLLM endpoint.

## 1. Environment

| Item | Value |
|---|---|
| Proxmox VE | 9.1.0, kernel 6.17.4-2-pve |
| GPU | NVIDIA GeForce RTX 5090, 32,607 MiB |
| Host driver | NVIDIA Open Kernel Module 580.82.09, CUDA 13.0 |
| Power limit | 500 W (default 575 W, min 400 W, max 600 W) |
| Host RAM | 125 GiB, no swap |
| Storage `hdd1` | LVM-thin, 1.79 TiB, 63.98 % used (holds the container rootfs volumes) |
| Storage `local` | directory on ZFS, ~192 GiB free (the only storage that accepts backups) |
| Storage `local-zfs` | zfspool, ~192 GiB free |

## 2. Audit summary [measured]

- **8 LXC containers and 2 VMs.** Both VMs are stopped, and neither has a GPU attached in its listed config.
- **7 of the 8 containers can open the GPU.** Each has the same `/dev/nvidia*` bind mounts (`lxc.mount.entry`) and `lxc.cgroup2.devices.allow: c 195:* / 235:* / 510:* rwm`.
- **All 7 GPU containers are privileged**, with no `unprivileged: 1`. They also set:
  - `lxc.cgroup2.devices.allow: a` (all devices),
  - an empty `lxc.cap.drop:`,
  - `lxc.apparmor.profile: unconfined`.
- **The one unprivileged container has no GPU.** It contains no `/dev/nvidia*` (`03-power-limit-noop.txt`).
- **5 GPU-capable containers were running at once.** Only one process was using the GPU: PID 3332087, with 27,150 MiB.
- **That process belongs to production.** Its `/proc/<pid>/cgroup` was `0::/lxc/102/ns/docker/<container-hash>`.

Where data lives:

- **`105`:** has no `mpX` entries. Its model weights (`/models`) and Docker images live inside the 500 G rootfs, which is 103 G used. One of the images is `vllm/vllm-openai:v0.29.0`, at 21.5 GB.
- **Production:** `mp0` is a host bind mount (`/mnt/docker-xfs` → `/var/lib/docker`). That source is an XFS filesystem on a separate thin LV of 500 G with 141 G used. So production's Docker images and data are outside its rootfs.
- **One other rental container** has the same bind-mount layout.

Snapshots and space:

- **Snapshots are supported** on `hdd1`, which is LVM-thin.
- **Thin-pool overcommit [measured]:** LVM warned that the sum of thin volume sizes exceeds the pool. It was about 4.1 TiB before my snapshots and 5.57 TiB after, against a 1.79 TiB pool, and autoextend protection is off.
- **[inferred]:** thin snapshots consume space only as the origin diverges. Still, every snapshot on this shared pool adds a little risk to every container on it, production included.
- **Clone [calculated]:** a full clone of `105` (~103 G) would fit on `hdd1`, which has ~659 GiB free.
- **Backup [calculated]:** a backup would have to go to `local` (~192 GiB free). Its upper bound is ~103 G before compression, so it would fit but use more than half of that space.

## 3. Driver lock-step [measured]

| Where | Version |
|---|---|
| Host kernel module (`/proc/driver/nvidia/version`) | 580.82.09 |
| `105`: `nvidia-smi` "Driver Version" | 580.82.09 |
| `105`: `libnvidia-ml.so.*` | `libnvidia-ml.so.580.82.09` (file date Sep 11 2025) |
| `105`: dpkg NVIDIA packages | only nvidia-container-toolkit 1.18.2; no driver package |

The versions match exactly.

[inferred] The userspace library is not owned by any dpkg package, so it was probably installed by the `.run` installer with `--no-kernel-module`. If the host driver is upgraded, every GPU container's userspace must be upgraded to the same version. Nothing was changed to test this.

## 4. Who controls the GPU settings [measured]

| Check | Result |
|---|---|
| Power limit from the host | 500.00 W |
| Power limit seen from inside `105` | 500.00 W (same values) |

**No-op test.** I ran `nvidia-smi -pl 500` inside `105`. **It succeeded** (rc=0, 0.84 s):

```
Power limit for GPU 00000000:C1:00.0 was set to 500.00 W from 500.00 W.
All done.
```

From the host afterwards, the current and requested limits were both still 500.00 W, so nothing changed.

[inferred]

- A privileged GPU container can change host-wide GPU hardware state, and that change applies to every other container on the card, including production.
- Seven containers on this host have that ability.
- Whether a renter's Docker container *inside* such an LXC also has it was not tested.

## 5. Backup, snapshot, clone

**Snapshots of `105` [measured]:** taken while running, on LVM-thin, with a 500 G rootfs.

| Run | Elapsed |
|---|---|
| 1 | 1.238 s |
| 2 | 1.199 s |
| 3 | 1.196 s |

`lvs` showed no Data% for the new thin snapshots, which means they share all blocks with the origin. There is no separate size figure to report.

**Not run:**

- **vzdump (snapshot mode).** It would read about 100 G from the thin pool that production shares, and no backup storage was confirmed.
- **Full clone.** No free container IDs were authorized.
- **Bind mounts and backup: not measured.** The vLLM container has no bind mounts, and I did not touch production. See section 9 for the documentation answer.

## 6. Sharing test: SKIPPED

The owner ruled out GPU load because production was using the card. No TFLOPS figures were collected.

Section 2 already shows, without any load, that several containers are running with the GPU open at once. It also shows which container a GPU PID belongs to (from `/proc/<pid>/cgroup`). The load script is in `scripts/gpu_load.py`, unrun.

## 7. Contention: not tested, but visible in place [measured + inferred]

- **The vLLM container's start-up script** (`wait-gpu-free.sh`) says the config needs "~30.7 GiB free at startup". It polls for up to 180 s.
  - **[inferred, from reading the script]:** it `exit 0`s even on timeout, so vLLM would then start and fail on memory.
- **[calculated]:** with production holding 27,241 MiB of the 32,607 MiB card, only ~4.8 GiB is free, so `105`'s vLLM cannot start right now. Sharing access to the device is not sharing its memory.
- I did not start the service to capture the exact error, because doing that would put load on production's GPU.

## 8. Swap and rollback

**Swap and rollback cycles (production ↔ test):** skipped. That needs GPU load and stopping production.

**Snapshot rollback on `105` [measured]:**

- **Attempt 1 (invalid: my script bug).** I tried to edit the compose file through `pct mount`, but the script could not read the mount path, so the file was never changed. The rollback that followed (1.322 s) restored a harmless `docker compose create`, not a broken upgrade. Raw output: `06-snapshot-rollback-ct-test-a.txt`.
- **Attempt 2 (valid).** Raw output: `06-...-attempt2.txt`.

| Step | Result |
|---|---|
| `pct snapshot before-break-2` | 1.230 s |
| Bad upgrade: compose image tag → `v0.0.0-does-not-exist` | compose sha256 changed from `6b2c70fa…` to `ee4ec7d0…` |
| Confirm broken | `Error response from daemon: manifest for vllm/vllm-openai:v0.0.0-does-not-exist not found: manifest unknown` |
| `pct stop` | 2.355 s |
| `pct rollback before-break-2` | 1.322 s |
| `pct start` | **46.04 s** |
| After the rollback | compose sha256 back to `6b2c70fa…`, image tag `v0.29.0` |

- **The "serving" check was not run** because the GPU is occupied. The poll script is `scripts/wait_serving.sh`, unrun.
- **Surprise [measured]:** a normal `pct start` of this container took 3.18 s. The first start after a rollback took 45.76 s in attempt 1 and 46.04 s in attempt 2. The cause was not investigated. [inferred] It may be one-off work on the freshly re-created thin LV, but that is unverified.

## 9. VM facts from documentation (documentation, not tested here) [docs]

**Ballooning with passthrough.**

> "if you are passing through a physical PCI(e) device or a Virtual Function I/O (VFIO) Mediated device (MDEV) such as a vGPU, then ballooning will not work since these devices are mapped to fixed memory addresses in the host and in the guest."

https://pve.proxmox.com/wiki/Dynamic_Memory_Management

The word "pinned" appears only in a forum post by a community member, not Proxmox staff: https://forum.proxmox.com/threads/memory-ballooning-pcie-passthrough-booting-from-nvme.123161/

**Sharing a passed-through device.**

> "if you pass through a device to a virtual machine, you cannot use that device anymore on the host or in any other VM."

The device must be bound to `vfio-pci`, or the host driver blacklisted. https://pve.proxmox.com/wiki/PCI(e)_Passthrough

**Bind mounts in backups.**

> "Device and bind mounts are never backed up as their content is managed outside the Proxmox VE storage library."

https://pve.proxmox.com/pve-docs/chapter-vzdump.html

> "The contents of bind mount points are not backed up when using vzdump."

https://pve.proxmox.com/pve-docs/chapter-pct.html

- Volume mount points are also excluded by default, unless `backup=1` is set.
- There was no measurement to compare against: vzdump was not run (section 5).
- For snapshots, the current docs say nothing about bind mounts. A 2019 Proxmox staff forum post says "A bind-mount has no snapshot functionality": https://forum.proxmox.com/threads/snapshot-feature-is-not-available.32246/ . This was not verified on PVE 9.1.

**MIG.**

> "MIG is supported on GPUs starting with the NVIDIA Ampere generation"

The supported list covers only data-center and RTX PRO Blackwell cards. No GeForce card is listed. https://docs.nvidia.com/datacenter/tesla/mig-user-guide/supported-gpus.html

**vGPU.**

- The validated GPU list has data-center, RTX PRO and Ada/Ampere workstation cards, and no GeForce. https://docs.nvidia.com/vgpu/latest/grid-vgpu-release-notes-generic-linux-kvm/validated-platforms.html
- It requires the NVIDIA License System:

> "the performance of the virtual GPU or physical GPU is degraded if the VM fails to obtain a license within 20 minutes"

https://docs.nvidia.com/vgpu/latest/grid-licensing-user-guide/intro-to-grid-licensing.html

"RTX 5090 not supported" is inferred from it being absent from these lists. NVIDIA does not state it outright.

Quotes were gathered by web fetch. Check them against the live pages before publishing.

## 10. Surprises and failures

- The GPU was held by a rental workload in production, not by the vLLM container the brief assumed.
- All 7 GPU containers are privileged, with all devices allowed, no capabilities dropped and AppArmor unconfined.
- A container could set the host power limit (section 4).
- The thin pool is 3× overcommitted, and my 5 snapshots increased that.
- The first start after a rollback takes about 46 s, against about 3 s for a normal start.
- My first rollback attempt was invalid because of a script bug (section 8).
- `105` prints `WARN: Systemd 249 detected. You may need to enable nesting.` on every start.

## 11. Skipped and why

| Skipped | Why |
|---|---|
| vzdump | I/O on the pool production shares, and no backup storage confirmed |
| Full clone | no free IDs authorized |
| Phase 5 (sharing, contention) | no GPU load allowed while production uses the card |
| Phase 6 swap and rollback cycles | needs GPU load and stopping production |
| "Serving" check after snapshot rollback | GPU occupied |
| Decode-speed check against ~223 tok/s | production is not a vLLM endpoint, and `105` could not start |

## 12. Production verification [measured]

`raw/08-*`

- **`102`:** running. Its config is identical to the Phase 1 copy (`diff` was empty). The rental container is still up. The same GPU PID still holds 27,150 MiB. The power limit is still 500 W.
- **All other containers:** configs identical to Phase 1. 105 had a `parent:` line while snapshots existed; it was removed when they were deleted.
- **`105`:**
  - Running, as at the start.
  - Its vLLM service is `disabled`, as at the start.
  - The service's active state was `failed` at the start and is `inactive` now. That is because the container was restarted.
- **`104`:** stopped and not touched. Neither test container has `onboot` set.
- **No inference health check:** see section 11.

**Created this session:** 5 LVM-thin snapshots on 105 (`article-test-20261007-1/2/3`, `before-break`, `before-break-2`). The owner approved deleting them, and all 5 are now deleted. Each `pct delsnapshot` took 1.05–2.15 s. Afterwards, `lvs` shows no remaining snapshot LVs and 105's config is identical to Phase 1 again (`raw/09-snapshot-cleanup.txt`).

Nothing created in this session remains on the host: no snapshots, backups, clones or VMs.
