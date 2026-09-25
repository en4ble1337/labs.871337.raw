#!/usr/bin/env python3
"""Reproducible OpenAI-compatible streaming benchmark.

Measures per request: TTFT, prefill tok/s (prompt_tokens/TTFT), decode tok/s
((completion_tokens-1)/(t_last-t_first)), plus GPU telemetry sampled via nvidia-smi.

Modes:
  decode   : short prompt, fixed workloads (chat/code/json/prose), thinking off
  ctx      : needle-in-a-haystack at a target context size (retrieval checked)
  conc     : N concurrent decode requests, aggregate + per-stream throughput
"""
import argparse, json, random, subprocess, threading, time, statistics, sys, os
import requests

RESULTS = os.environ.get("RESULTS_DIR", "results")

WORDS = ("amber river quartz lantern falcon orchard meridian copper harbor violet summit "
         "cinder willow granite beacon saffron tundra marble ember cobalt prairie juniper "
         "glacier monsoon obsidian canyon zephyr lagoon thistle basalt aurora sequoia").split()
VERBS = "measured described archived repaired painted surveyed catalogued visited traded mapped".split()
PLACES = "Lisbon Kyoto Nairobi Oslo Lima Hanoi Perth Quebec Tbilisi Accra Bergen Cusco".split()

def filler_sentence(rng):
    return (f"In {rng.randint(1500, 2020)} the {rng.choice(WORDS)} {rng.choice(WORDS)} of "
            f"{rng.choice(PLACES)} was {rng.choice(VERBS)} by a team of {rng.randint(2, 90)} "
            f"people who noted {rng.randint(10, 9999)} {rng.choice(WORDS)} samples near the "
            f"{rng.choice(WORDS)} {rng.choice(WORDS)}. ")

def build_haystack(target_tokens, seed, tok_per_sentence):
    rng = random.Random(seed)
    code1 = f"{rng.choice(WORDS).upper()}-{rng.randint(1000, 9999)}"
    code2 = f"{rng.choice(WORDS).upper()}-{rng.randint(1000, 9999)}"
    n = max(4, int(target_tokens / tok_per_sentence))
    sents = [filler_sentence(rng) for _ in range(n)]
    sents.insert(min(3, len(sents)), f"IMPORTANT: The first secret code is {code1}. ")
    sents.insert(max(len(sents) - 3, 4), f"IMPORTANT: The second secret code is {code2}. ")
    doc = "".join(sents)
    q = ("\n\nThe document above contains exactly two secret codes marked IMPORTANT. "
         "Reply with the first secret code and the second secret code on one line, then write "
         "a detailed 250-word description of how lighthouses work.")
    return doc + q, code1, code2

WORKLOADS = {
    "chat": "Explain, in about 400 words, why the sky is blue and why sunsets are red. Use plain language.",
    "code": "Write a complete, well-commented Python module implementing an LRU cache class with get/put, "
            "a TTL option, thread-safety via a lock, and a small unittest suite. Output only code.",
    "json": "Produce a JSON array of 12 fictional employees. Each object must have id (int), name, email, "
            "department, salary (int), skills (array of 3 strings), and manager_id (int or null). Output only JSON.",
    "reason": "A train leaves city A at 9:00 going 80 km/h; another leaves city B, 400 km away, at 10:00 going "
              "120 km/h toward A. When and where do they meet? Show the steps, then verify the answer.",
}

class GpuSampler(threading.Thread):
    Q = "timestamp,memory.used,memory.total,utilization.gpu,utilization.memory,power.draw,temperature.gpu,clocks.sm,clocks.mem"
    def __init__(self, path=None, interval=0.5):
        super().__init__(daemon=True); self.rows = []; self.stop_ev = threading.Event()
        self.path = path; self.interval = interval
    def run(self):
        while not self.stop_ev.is_set():
            try:
                out = subprocess.check_output(["nvidia-smi", f"--query-gpu={self.Q}",
                                               "--format=csv,noheader,nounits"], text=True).strip()
                f = [x.strip() for x in out.split(",")]
                self.rows.append(dict(ts=f[0], mem=float(f[1]), memtot=float(f[2]), util=float(f[3]),
                                      mutil=float(f[4]), power=float(f[5]), temp=float(f[6]),
                                      sm=float(f[7]), memclk=float(f[8])))
            except Exception:
                pass
            self.stop_ev.wait(self.interval)
    def stop(self):
        self.stop_ev.set(); self.join()
        if self.path and self.rows:
            with open(self.path, "a") as fh:
                for r in self.rows: fh.write(json.dumps(r) + "\n")
        return self.summary()
    def summary(self):
        if not self.rows: return {}
        busy = [r for r in self.rows if r["util"] > 50] or self.rows
        return dict(mem_peak_mib=max(r["mem"] for r in self.rows),
                    util_avg_busy=round(statistics.mean(r["util"] for r in busy), 1),
                    power_avg_busy=round(statistics.mean(r["power"] for r in busy), 1),
                    power_max=max(r["power"] for r in self.rows),
                    temp_max=max(r["temp"] for r in self.rows),
                    sm_clk_avg_busy=round(statistics.mean(r["sm"] for r in busy)),
                    sm_clk_min_busy=min(r["sm"] for r in busy))

def stream_chat(base, model, messages, max_tokens, thinking=False, extra=None, timeout=3600):
    body = dict(model=model, messages=messages, max_tokens=max_tokens, stream=True,
                temperature=0.0, stream_options={"include_usage": True})
    body["chat_template_kwargs"] = {"enable_thinking": thinking}
    if extra: body.update(extra)
    t0 = time.perf_counter(); t_first = None; t_last = None; text = []; reasoning = []; usage = None
    n_chunks = 0
    with requests.post(f"{base}/v1/chat/completions", json=body, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line or not line.startswith(b"data: "): continue
            d = line[6:]
            if d == b"[DONE]": break
            j = json.loads(d)
            if j.get("usage"): usage = j["usage"]
            for ch in j.get("choices", []):
                delta = ch.get("delta", {})
                piece = delta.get("content") or ""
                rpiece = delta.get("reasoning_content") or delta.get("reasoning") or ""
                if piece or rpiece:
                    now = time.perf_counter()
                    if t_first is None: t_first = now
                    t_last = now; n_chunks += 1
                    text.append(piece); reasoning.append(rpiece)
    t_end = time.perf_counter()
    pt = usage["prompt_tokens"] if usage else None
    ct = usage["completion_tokens"] if usage else n_chunks
    cached = ((usage or {}).get("prompt_tokens_details") or {}).get("cached_tokens")
    ttft = (t_first - t0) if t_first else None
    dec = (ct - 1) / (t_last - t_first) if t_first and t_last and t_last > t_first and ct > 1 else None
    return dict(ttft_s=round(ttft, 3) if ttft else None, prompt_tokens=pt, completion_tokens=ct,
                cached_tokens=cached, total_s=round(t_end - t0, 3),
                prefill_tps=round(pt / ttft, 1) if pt and ttft else None,
                decode_tps=round(dec, 2) if dec else None,
                text="".join(text), reasoning="".join(reasoning))

def run_decode(a, gs):
    res = []
    for rep in range(a.reps):
        for name, prompt in WORKLOADS.items():
            r = stream_chat(a.base, a.model, [{"role": "user", "content": prompt}], a.max_tokens,
                            thinking=False, extra=a.extra)
            r.update(workload=name, rep=rep); res.append(r)
            print(f"  {name:6s} rep{rep} ttft={r['ttft_s']}s pt={r['prompt_tokens']} ct={r['completion_tokens']} "
                  f"decode={r['decode_tps']} tok/s", flush=True)
    return res

def run_ctx(a, gs):
    res = []
    for ctx in a.ctx:
        for rep in range(a.reps):
            prompt, c1, c2 = build_haystack(ctx - 400, seed=int(time.time() * 1000) % 10**9 + rep,
                                           tok_per_sentence=a.tok_per_sentence)
            r = stream_chat(a.base, a.model, [{"role": "user", "content": prompt}], a.max_tokens,
                            thinking=False, extra=a.extra)
            ans = r["text"]
            r.update(target_ctx=ctx, rep=rep, needle_first=c1 in ans, needle_last=c2 in ans)
            r["text"] = ans[:300]; res.append(r)
            print(f"  ctx~{ctx:>6} pt={r['prompt_tokens']} ttft={r['ttft_s']}s prefill={r['prefill_tps']} tok/s "
                  f"decode={r['decode_tps']} tok/s needles={r['needle_first']}/{r['needle_last']} "
                  f"cached={r['cached_tokens']}", flush=True)
    return res

def run_conc(a, gs):
    res = []
    for n in a.conc:
        prompts = list(WORKLOADS.values())
        out = [None] * n
        def worker(i):
            # distinct prompts so prefix cache doesn't flatter
            p = f"[request {i} nonce {random.random()}] " + prompts[i % len(prompts)]
            out[i] = stream_chat(a.base, a.model, [{"role": "user", "content": p}], a.max_tokens,
                                 thinking=False, extra=dict(a.extra or {}, ignore_eos=True) if a.ignore_eos else a.extra)
        t0 = time.perf_counter()
        th = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        [t.start() for t in th]; [t.join() for t in th]
        wall = time.perf_counter() - t0
        tot = sum(o["completion_tokens"] for o in out)
        per = [o["decode_tps"] for o in out if o["decode_tps"]]
        r = dict(concurrency=n, wall_s=round(wall, 2), total_completion_tokens=tot,
                 aggregate_tps=round(tot / wall, 1), per_stream_decode_avg=round(statistics.mean(per), 1),
                 per_stream_decode_min=round(min(per), 1), ttft_avg=round(statistics.mean(o["ttft_s"] for o in out), 3),
                 ttft_max=max(o["ttft_s"] for o in out), latency_max_s=max(o["total_s"] for o in out))
        res.append(r)
        print(f"  conc={n} aggregate={r['aggregate_tps']} tok/s per-stream avg={r['per_stream_decode_avg']} "
              f"min={r['per_stream_decode_min']} ttft avg={r['ttft_avg']}s max={r['ttft_max']}s wall={r['wall_s']}s", flush=True)
    return res

def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["decode", "ctx", "conc"])
    p.add_argument("--base", default="http://127.0.0.1:8000")
    p.add_argument("--model", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--max-tokens", type=int, default=512)
    p.add_argument("--reps", type=int, default=2)
    p.add_argument("--ctx", type=int, nargs="*", default=[1024, 8192, 32768])
    p.add_argument("--conc", type=int, nargs="*", default=[1, 3])
    p.add_argument("--tok-per-sentence", type=float, default=40.0)
    p.add_argument("--ignore-eos", action="store_true")
    p.add_argument("--extra", type=json.loads, default=None)
    p.add_argument("--out", default=os.path.join(RESULTS, "bench.jsonl"))
    a = p.parse_args()
    os.makedirs(RESULTS, exist_ok=True); os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    gs = GpuSampler(path=os.path.join(RESULTS, f"gpu_{a.label}_{a.mode}.jsonl")); gs.start()
    t0 = time.time()
    try:
        res = {"decode": run_decode, "ctx": run_ctx, "conc": run_conc}[a.mode](a, gs)
    finally:
        g = gs.stop()
    summ = dict(label=a.label, mode=a.mode, time=time.strftime("%F %T"), dur_s=round(time.time() - t0),
                gpu=g, results=[{k: v for k, v in r.items() if k not in ("text", "reasoning")} for r in res])
    if a.mode == "decode":
        d = [r["decode_tps"] for r in res if r["decode_tps"]]
        summ["decode_tps_mean"] = round(statistics.mean(d), 2)
        summ["by_workload"] = {w: round(statistics.mean(r["decode_tps"] for r in res if r["workload"] == w), 2)
                               for w in WORKLOADS}
        summ["ttft_mean"] = round(statistics.mean(r["ttft_s"] for r in res), 3)
        print(f"== {a.label}: decode mean {summ['decode_tps_mean']} tok/s {summ['by_workload']} ttft {summ['ttft_mean']}s")
    print("== GPU:", g)
    with open(a.out, "a") as fh: fh.write(json.dumps(summ) + "\n")

if __name__ == "__main__":
    main()
