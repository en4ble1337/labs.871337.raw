You are working on the Proxmox VE host of a single-GPU inference server (NVIDIA RTX 5090, 32 GB). The GPU is owned by the host driver and shared into LXC containers through device bind mounts. One LXC container runs the production inference service (vLLM in Docker, configured to use ~98% of VRAM).

Your objective is to **collect evidence** for an article about why a single-GPU inference box should run on Proxmox with LXC containers instead of passing the GPU through to a VM. You are not tuning anything. You are measuring and documenting how this architecture behaves, including where it hurts.

Report only what you measured. If a test fails or gives an unexpected result, that is a valid result. Record it; do not work around it silently.

## Fill in before starting (Bart)

```text
PROD_CTID=            # production GPU container ID
PROD_SERVICE=         # systemd service inside it that runs inference
API_PORT=             # inference API port inside the container
TEST_CTID_RANGE=      # free IDs the agent may use for clones, e.g. 9001-9009
BACKUP_STORAGE=       # Proxmox storage ID for vzdump backups
MAINTENANCE_WINDOW=   # "yes" = production may be stopped during Phases 4-6
```

If any value is empty, find it during the audit and **ask Bart to confirm it before Phase 3**.

## Important rules

* Production must end this session exactly as it started: same config, running, healthy. Verify it at the end.
* Do not stop production unless `MAINTENANCE_WINDOW=yes`. If it is not set, run Phases 1-3 and 7-8 only, and say what was skipped.
* Do **not** create a VM with GPU passthrough. It unbinds the GPU from the host driver and can take down every GPU container. VM facts come from documentation only (Phase 7).
* Do not change the host driver, kernel, packages, power limit, or any existing container config.
* Never delete anything you did not create. At the end, leave your test containers **stopped**, list them, and ask Bart before destroying them.
* Time every operation with wall-clock timestamps (`date +%s.%N` before and after). Repeat timed operations 3 times where practical and report every run, not only the average.

---

## Phase 1: Audit the host (read-only)

Collect and save:

```bash
pveversion -v
uname -r
cat /proc/driver/nvidia/version
nvidia-smi
nvidia-smi -q -d POWER,MEMORY
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
pvesm status
pct list
qm list
free -g
df -h
```

For every container, record from `pct config <id>`:

* whether it has GPU access (the `lxc.cgroup2.devices.allow` and `/dev/nvidia*` mount entries);
* privileged or unprivileged;
* mount points (`mpX`), and which of them are bind mounts from the host;
* cores, memory, rootfs size and storage.

Answer in plain words:

* How many containers and VMs exist, and which ones can use the GPU?
* Where do the model weights live: inside the production rootfs, or on a host bind mount?
* Does the production rootfs storage support snapshots (ZFS, LVM-thin, Ceph, etc.)?
* How much free space is there for a full clone of production and a backup? If there is not enough, stop and tell Bart.

## Phase 2: Driver lock-step check (read-only)

Show that the container's NVIDIA userspace must match the host kernel module:

* host: `cat /proc/driver/nvidia/version`
* inside production: `pct exec $PROD_CTID -- nvidia-smi` (driver version line) and the installed userspace version (e.g. `pct exec $PROD_CTID -- sh -c 'ls /usr/lib/x86_64-linux-gnu/libnvidia-ml.so.*'`)

Record both versions side by side. Do not install or change anything.

## Phase 3: Who controls the GPU settings

From the **host**, record the current power limit (`nvidia-smi -q -d POWER`).

From **inside production**, record what the container sees for the same value.

Then test whether the container is allowed to change host GPU settings, **using a no-op**: inside the container, set the power limit to the value it already has (e.g. `nvidia-smi -pl <current-value>`). Record whether it succeeded or was refused, and the exact message. Then confirm from the host that the limit is unchanged.

Why this matters: if a container can set it, the container is effectively able to change host hardware state. That is evidence for the trust-boundary section of the article. Do not try any other value.

## Phase 4: Backup, snapshot and clone cost (needs MAINTENANCE_WINDOW=yes for stop-mode)

On production:

1. **Snapshot** (only if storage supports it): `pct snapshot $PROD_CTID article-test-<date>`. Time it. Record its size if the storage reports it.
2. **Backup in snapshot mode** (no downtime): `vzdump $PROD_CTID --mode snapshot --storage $BACKUP_STORAGE`. Time it. Record archive size and the full vzdump log.
3. From the vzdump log, find the lines about mount points. **Confirm or refute: are bind mounts excluded from the backup?** Quote the exact log line.
4. **Full clone**: `pct clone $PROD_CTID <test-id> --full --hostname <generic-name>`. Time it. Record its size.
5. Before the clone ever starts, disable its inference service so it cannot autostart and fight production for VRAM. For example: `pct mount <test-id>`, then `systemctl --root=<mounted-rootfs> disable $PROD_SERVICE`, then `pct unmount <test-id>`. Also set `onboot: 0` on the clone. Give the clone a different IP if production uses a static one, so they never collide.

## Phase 5: Sharing test (needs MAINTENANCE_WINDOW=yes)

Goal: show that two containers really use one GPU at the same time, and show where that stops working.

1. Stop the production service (not the container yet). Time how long until host `nvidia-smi` shows the VRAM is released.
2. Create a second full clone (`<test-id-2>`) the same way as Phase 4 step 4-5, or clone from the first test container.
3. Start both test containers.
4. **Side by side.** In each container, run a small GPU load that allocates a fixed amount of VRAM (for example 8 GB) and does a sustained matrix multiply for 60 seconds, printing achieved TFLOPS. Use the PyTorch already present in the vLLM image, for example:

   ```bash
   docker run --rm --gpus all --entrypoint python3 <vllm-image-already-on-box> -c '<script>'
   ```

   Write the script yourself and save it. Run it:
   * in container A alone,
   * in container B alone,
   * in both at the same time.

   While both run, capture from the **host**:
   * `nvidia-smi` (full output, screenshot-friendly),
   * `nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv`,
   * for each GPU PID, `cat /proc/<pid>/cgroup` to prove which container it belongs to.

   Report TFLOPS alone vs together. This shows that compute is time-shared, not split.

5. **Contention.** In container A, allocate most of the card (for example 28 GB) and hold it. In container B, try to start the production inference config (the original service, unchanged). Record exactly how it fails and how long it takes to fail. Then release A and confirm B can start. This shows that sharing access is not sharing memory.

6. Stop both test containers.

## Phase 6: The experiment/rollback cycle (needs MAINTENANCE_WINDOW=yes)

Goal: measure the real workflow from the article. Production is "old", a test container is "new".

Define "serving" as: the API on `$API_PORT` answers `/v1/models` and returns a correct short completion. Write a small script that polls every 2 seconds and prints the timestamp when that first succeeds.

Run this cycle **3 times**:

1. Production running and serving. Start the clock.
2. Stop production (service stop, then `pct stop`).
3. Start test container A and its inference service.
4. Record time until A is serving. **(swap-in time)**
5. Stop A.
6. Start production.
7. Record time until production is serving again. **(rollback time)**

Note whether runs after the first are faster (warm caches), and say why if you can tell.

Then test snapshot rollback on test container A (not production):

1. `pct snapshot <test-id> before-break`.
2. Make a deliberate, harmless "bad upgrade" inside it, for example change the image tag in its compose file to a non-existent one, so the service cannot start.
3. Confirm it is broken.
4. `pct rollback <test-id> before-break`. Time it.
5. Start it, confirm it serves again, time it.

## Phase 7: VM facts from documentation (no testing)

Do not build a VM. From current official Proxmox and NVIDIA documentation, find and quote with URLs:

* What happens to memory ballooning and RAM allocation for a VM with PCIe passthrough.
* Whether a PCIe device can be used by more than one running VM, or by the host, while passed through.
* Whether bind mount points are included in LXC backups and snapshots (compare with your Phase 4 result).
* Which NVIDIA GPUs support MIG, and what vGPU requires (supported cards, license).

Mark each item as "documentation, not tested here". If documentation and your measurement disagree, report both.

## Phase 8: Restore and verify production

1. Make sure production is running, its config is identical to the Phase 1 copy (`diff` the `pct config` output), and the service is enabled.
2. Run a quick health check: `/v1/models`, one short completion, and one decode speed sample. If the earlier benchmark scripts exist on the box, use them; otherwise a single timed 512-token generation is enough. Compare against the article's known value of ~223 tok/s single-stream and say if it is far off.
3. Confirm all test containers are stopped and `onboot: 0`.
4. List everything you created (containers, snapshots, backup archives) with sizes. **Ask Bart which ones to delete. Do not delete them yourself.**

## Phase 9: Day-to-day management, LXC vs VM (no GPU load)

Goal: show what the host can change on a **running** container without a reboot. This phase uses no GPU at all and never touches production.

1. Create one small, **non-GPU** test container from a template that already exists on the host (no GPU device entries, `onboot: 0`, ID from `TEST_CTID_RANGE`). Use DHCP, or ask Bart for a free IP.
2. Time `pct start` until the container answers `pct exec <id> -- true`. Repeat 3 times. Record idle RAM use of the running container from the host.
3. While it runs, and checking from inside after each step (`nproc`, `free -m`, `df -h /`, `ip addr`):
   * change cores and memory with `pct set`;
   * grow the root disk with `pct resize <id> rootfs +1G`;
   * add a small second mount point (a storage volume, not a bind mount) with `pct set <id> -mp0 ...`;
   * change a network setting with `pct set <id> -net0 ...` (for example, a static IP Bart confirmed, or a different VLAN tag on an unused VLAN).
   For each: did it apply live, need a restart, or fail? Record the exact output.
4. From current Proxmox documentation (not tested), find what the same changes need on a VM: CPU/RAM hotplug requirements, what happens inside the guest after a disk resize, and which console types support copy/paste. Quote with URLs.
5. Stop the test container. Leave it for Bart to delete.

Add the results to `REPORT.md` as its own section, "Live management: LXC vs VM".

---

## Final deliverable

Write everything to one folder, for example `/root/lxc-article-evidence/`:

* `REPORT.md`: the findings, in this order:
  1. Environment (sanitized).
  2. Audit summary: containers, which have GPU access, where models live, storage type.
  3. Driver lock-step: host vs container versions.
  4. GPU settings control: did the no-op power-limit change succeed from inside a container?
  5. Backup/snapshot/clone: times and sizes, plus the bind-mount answer with the log line.
  6. Sharing: TFLOPS alone vs together, VRAM per container, PID-to-container proof.
  7. Contention: how the second service failed, exact error.
  8. Swap and rollback: every run's times, plus the snapshot rollback.
  9. VM facts from docs, with URLs, clearly marked as not tested.
  10. Anything that surprised you or failed.
  11. What was skipped and why.
  12. Production verification result.
* `raw/`: every command output, log and script, one file per step.
* `scripts/`: the GPU load script and the "serving" poll script, so a reader can rerun them.

Separate **measured**, **calculated**, and **inferred** in the report.

## Sanitization (required before you finish)

Everything in the deliverable folder will be published. Before you finish:

* Replace hostnames with `<host>`, container hostnames with `<ct-prod>`, `<ct-test-a>`, `<ct-test-b>`.
* Replace LAN IPs, MAC addresses, domains and endpoint URLs with placeholders like `<host-ip>`.
* Remove serial numbers, UUIDs, credentials, tokens and SSH keys.
* Keep container IDs only if Bart agrees; otherwise use `<prod-ctid>` and `<test-ctid>`.
* Run a final `grep` across the folder for IPv4 patterns, the real hostname, and the word `serial`, and show Bart the result.
