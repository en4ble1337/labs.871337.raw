# GPU load for the LXC sharing test. NOT RUN in this session (GPU was in use by production).
# Usage (inside a GPU container, with an image that has PyTorch, e.g. vllm/vllm-openai):
#   docker run --rm --gpus all --entrypoint python3 -v $PWD:/s <image> /s/gpu_load.py --gib 8 --seconds 60
#   --hold-only: allocate --gib and sleep (contention test), no compute.
import argparse, time, torch
p = argparse.ArgumentParser()
p.add_argument("--gib", type=float, default=8)
p.add_argument("--seconds", type=float, default=60)
p.add_argument("--n", type=int, default=8192)
p.add_argument("--hold-only", action="store_true")
a = p.parse_args()
dev = "cuda"
ballast = torch.empty(int(a.gib * 2**30), dtype=torch.uint8, device=dev)  # fixed VRAM allocation
print(f"{time.time():.3f} allocated {a.gib} GiB; torch reserved={torch.cuda.memory_reserved()/2**30:.2f} GiB", flush=True)
if a.hold_only:
    time.sleep(a.seconds); raise SystemExit
x = torch.randn(a.n, a.n, device=dev, dtype=torch.bfloat16)
y = torch.randn(a.n, a.n, device=dev, dtype=torch.bfloat16)
for _ in range(5): x @ y
torch.cuda.synchronize()
flop = 2 * a.n ** 3
t_end = time.time() + a.seconds; win_t = time.time(); win_i = 0; total = 0; t0 = time.time()
while time.time() < t_end:
    x @ y; win_i += 1; total += 1
    if win_i == 50:
        torch.cuda.synchronize(); now = time.time()
        print(f"{now:.3f} {win_i*flop/(now-win_t)/1e12:.1f} TFLOPS", flush=True)
        win_t, win_i = now, 0
torch.cuda.synchronize()
print(f"{time.time():.3f} AVG {total*flop/(time.time()-t0)/1e12:.1f} TFLOPS over {time.time()-t0:.1f}s", flush=True)
