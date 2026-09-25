#!/usr/bin/env python3
"""Functional correctness suite against an OpenAI-compatible endpoint.
Exit code = number of failed checks. Usage: func.py <label> [--base URL] [--rounds N] [--model NAME]"""
import os, json, subprocess, sys, tempfile, time, argparse, re
from openai import OpenAI

ap = argparse.ArgumentParser(); ap.add_argument("label"); ap.add_argument("--base", default="http://127.0.0.1:8000")
ap.add_argument("--rounds", type=int, default=3); ap.add_argument("--model", default="qwen3.8-27b"); a = ap.parse_args()
c = OpenAI(base_url=a.base + "/v1", api_key="none"); M = a.model
fails = []; log = {"label": a.label, "checks": []}

def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail[:160]}", flush=True)
    log["checks"].append(dict(name=name, ok=bool(ok), detail=str(detail)[:500]))
    if not ok: fails.append(name)

def chat(msgs, think=False, **kw):
    return c.chat.completions.create(model=M, messages=msgs, temperature=0.6, top_p=0.95,
        extra_body={"chat_template_kwargs": {"enable_thinking": think}, "top_k": 20}, **kw)

def garbled(t):  # crude corruption / loop detector
    if re.search(r"(.{8,40}?)\1{4,}", t, re.S): return True
    if "<tool_call>" in t or "<function=" in t or "</think>" in t: return True
    return sum(ch == "�" for ch in t) > 0

# 1. normal chat
r = chat([{"role": "user", "content": "In 3-4 sentences, what are the main causes of ocean tides?"}])
t = r.choices[0].message.content or ""
check("chat", "moon" in t.lower() and not garbled(t) and 80 < len(t) < 2000, t)

# 2. coding - generated code is actually executed
r = chat([{"role": "user", "content": "Write a Python function `is_prime(n)` and a function `primes_below(n)` returning a list. "
           "Then at module level print(primes_below(50)). Output only a single ```python code block."}], max_tokens=1500)
t = r.choices[0].message.content or ""; m = re.search(r"```python\n(.*?)```", t, re.S)
out = ""
if m:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f: f.write(m.group(1))
    out = subprocess.run([sys.executable, f.name], capture_output=True, text=True, timeout=20).stdout
check("code_exec", "[2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47]" in out, out.strip())

# 3. reasoning, thinking ON and OFF
q = [{"role": "user", "content": "A bat and a ball cost $1.10 total. The bat costs $1.00 more than the ball. "
      "How much is the ball in cents? End with 'ANSWER: <number>'."}]
r = chat(q, think=True, max_tokens=8000); msg = r.choices[0].message
reasoning = getattr(msg, "reasoning", None) or getattr(msg, "reasoning_content", None) or ""
check("reasoning_think_on", "ANSWER: 5" in (msg.content or "") and len(reasoning) > 20 and "<think>" not in (msg.content or ""),
      f"reasoning_len={len(reasoning)} content={msg.content!r}")
r = chat(q, think=False, max_tokens=800); msg = r.choices[0].message
reasoning = getattr(msg, "reasoning", None) or getattr(msg, "reasoning_content", None) or ""
check("reasoning_think_off", "ANSWER: 5" in (msg.content or "") and not reasoning.strip(), f"content={msg.content!r}")

# 4. tool calling - multiple rounds, parallel + sequential, both thinking modes
tools = [
 {"type": "function", "function": {"name": "get_weather", "description": "Get current weather for a city",
  "parameters": {"type": "object", "properties": {"city": {"type": "string"}, "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}},
                 "required": ["city", "unit"]}}},
 {"type": "function", "function": {"name": "search_flights", "description": "Search flights",
  "parameters": {"type": "object", "properties": {"origin": {"type": "string", "description": "IATA code"},
   "destination": {"type": "string", "description": "IATA code"}, "date": {"type": "string", "description": "YYYY-MM-DD"},
   "passengers": {"type": "integer"}}, "required": ["origin", "destination", "date", "passengers"]}}},
 {"type": "function", "function": {"name": "calculate", "description": "Evaluate an arithmetic expression",
  "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}}]

for rnd in range(a.rounds):
    for think in (False, True):
        tag = f"r{rnd}_think{int(think)}"
        # parallel calls
        msgs = [{"role": "user", "content": "What's the weather in Paris and in Tokyo, in celsius? Use the tool for both cities."}]
        r = chat(msgs, think=think, tools=tools, tool_choice="auto", max_tokens=4000); m_ = r.choices[0].message
        calls = m_.tool_calls or []
        try: args = [json.loads(tc.function.arguments) for tc in calls]
        except Exception as e: args = [];
        cities = sorted(x.get("city", "").lower() for x in args)
        check(f"tool_parallel_{tag}", len(calls) == 2 and all(tc.function.name == "get_weather" for tc in calls)
              and cities == ["paris", "tokyo"] and all(x.get("unit") == "celsius" for x in args)
              and r.choices[0].finish_reason == "tool_calls", f"{[(tc.function.name, tc.function.arguments) for tc in calls]}")
        # feed results back -> expect natural-language synthesis
        msgs.append({"role": "assistant", "content": m_.content or "", "tool_calls": [tc.model_dump() for tc in calls]})
        for tc, temp in zip(calls, ["18", "24"]):
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps({"temp_c": int(temp), "sky": "clear"})})
        r = chat(msgs, think=think, tools=tools, max_tokens=4000); t = r.choices[0].message.content or ""
        check(f"tool_synthesis_{tag}", "18" in t and "24" in t and not r.choices[0].message.tool_calls and not garbled(t), t)
        # typed args + integer
        r = chat([{"role": "user", "content": "Find flights from JFK to LHR on 2026-10-05 for 3 passengers."}],
                 think=think, tools=tools, max_tokens=4000); calls = r.choices[0].message.tool_calls or []
        try: x = json.loads(calls[0].function.arguments) if calls else {}
        except Exception: x = {}
        check(f"tool_typed_{tag}", len(calls) == 1 and calls[0].function.name == "search_flights" and x.get("origin") == "JFK"
              and x.get("destination") == "LHR" and x.get("date") == "2026-10-05" and x.get("passengers") == 3,
              calls[0].function.arguments if calls else "no call")
        # restraint: no tool needed
        r = chat([{"role": "user", "content": "Say hello in French, one word."}], think=think, tools=tools, max_tokens=2000)
        check(f"tool_restraint_{tag}", not r.choices[0].message.tool_calls and "bonjour" in (r.choices[0].message.content or "").lower(),
              r.choices[0].message.content or "")

# 5. streaming tool call (parser in streaming mode)
st = c.chat.completions.create(model=M, messages=[{"role": "user", "content": "Use the calculator to compute 1234*5678."}],
     tools=tools, stream=True, temperature=0.6, extra_body={"chat_template_kwargs": {"enable_thinking": False}})
name = ""; argstr = ""
for ch in st:
    for tc in (ch.choices[0].delta.tool_calls or []) if ch.choices else []:
        if tc.function and tc.function.name: name += tc.function.name
        if tc.function and tc.function.arguments: argstr += tc.function.arguments
try: ok = name == "calculate" and "1234" in json.loads(argstr)["expression"] and "5678" in json.loads(argstr)["expression"]
except Exception: ok = False
check("tool_streaming", ok, f"{name} {argstr}")

# 6. long multi-turn conversation with prefix caching
sysmsg = {"role": "system", "content": "You are a meticulous assistant. " + " ".join(
    f"Reference fact {i}: item-{i} weighs {i * 7 % 97} kg." for i in range(600))}
msgs = [sysmsg]; cached_seen = []; ok_turns = True
qs = ["How much does item-12 weigh? Answer with just the number and unit.",
      "And item-305? Just the number and unit.", "What is the sum of the two weights you just gave? Just the number.",
      "Which of the two items is heavier? Just the item name."]
exp = [f"{12*7%97}", f"{305*7%97}", f"{12*7%97 + 305*7%97}", "item-12" if 12*7%97 > 305*7%97 else "item-305"]
for q_, e in zip(qs, exp):
    msgs.append({"role": "user", "content": q_})
    r = chat(msgs, max_tokens=200); t = r.choices[0].message.content or ""
    msgs.append({"role": "assistant", "content": t})
    cd = getattr(r.usage, "prompt_tokens_details", None)
    cached_seen.append((r.usage.prompt_tokens, getattr(cd, "cached_tokens", None) if cd else None))
    if e not in t: ok_turns = False
    print(f"    turn: {q_[:30]!r} -> {t.strip()[:60]!r} (expected {e}) usage={cached_seen[-1]}")
check("multiturn_correct", ok_turns, str(exp))
check("prefix_cache_hits", all((cc or 0) >= 0.5 * pt for pt, cc in cached_seen[1:]), str(cached_seen))  # hybrid model: 1600-tok aligned blocks

log["failed"] = fails
RESULTS = os.environ.get("RESULTS_DIR", "results"); os.makedirs(RESULTS, exist_ok=True)
with open(os.path.join(RESULTS, "func.jsonl"), "a") as f: f.write(json.dumps(log) + "\n")
print(f"== {a.label}: {len(log['checks']) - len(fails)}/{len(log['checks'])} passed; failed={fails}")
sys.exit(len(fails))
