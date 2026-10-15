"""Paid probe: how does the provider's prompt cache respond to a replaced trailing message?

Each trial is one synthetic conversation with a unique nonce (no cross-trial reuse).
Conditions:
  append        pure append, no trailing dynamic message
  append_nokey  same, without prompt_cache_key
  tail_small    append + a replaced trailing message of ~300 tokens
  tail_large    append + a replaced trailing message of ~1300 tokens
  statelog      append + a ~150-token state note appended (kept) after every chunk
"""
import argparse
import json
import random
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import tiktoken

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from aster.config import load_settings  # noqa: E402

SETTINGS = load_settings(REPO / ".env")
ENC = tiktoken.get_encoding("o200k_base")
OUT = Path(__file__).resolve().parent / "results.jsonl"
LOCK = threading.Lock()
WORDS = ("alpha beta gamma delta value index result buffer parser token cache node tree "
         "list item config path error check update render state merge split format").split()


def text(rng, tokens, label):
    lines, n = [], 0
    while n < tokens:
        name = "_".join(rng.choice(WORDS) for _ in range(3))
        line = f"{label}:{len(lines)} def {name}(x{rng.randint(0, 999)}): return {rng.randint(0, 99999)}"
        lines.append(line)
        n += len(ENC.encode(line)) + 1
    return "\n".join(lines)


def send(payload):
    url = SETTINGS.base_url.rstrip("/") + "/responses"
    headers = {"Authorization": "Bearer " + SETTINGS.api_key.get_secret_value(),
               "Content-Type": "application/json"}
    usage, status, terminal = None, None, None
    with httpx.Client(timeout=httpx.Timeout(120, connect=20)) as client:
        with client.stream("POST", url, json=payload, headers=headers) as response:
            status = response.status_code
            for line in response.iter_lines():
                if line.startswith("data: ") and line[6:].strip() != "[DONE]":
                    event = json.loads(line[6:])
                    result = event.get("response", {})
                    if result.get("usage"):
                        usage = result["usage"]
                    if event.get("type") in {"response.completed", "response.failed",
                                             "response.incomplete"}:
                        terminal = event["type"]
    return status, terminal, usage


def trial(condition, rep, steps, gap):
    rng = random.Random(f"{condition}-{rep}")
    nonce = uuid.uuid4().hex
    key = None if condition == "append_nokey" else "probe-" + nonce
    prompt = [
        {"role": "system", "content": f"[probe {nonce}] Answer with the single word OK.\n"
                                      + text(rng, 600, "sys")},
        {"role": "user", "content": "Task context:\n" + text(rng, 5000, "task")},
    ]
    for step in range(steps):
        if step:
            prompt.append({"role": "user", "content": f"Observation {step}:\n" + text(rng, 1200, f"obs{step}")})
            if condition == "statelog":
                prompt.append({"role": "user", "content": f"State note {step}:\n" + text(rng, 150, f"st{step}")})
        request = list(prompt)
        tail_tokens = 0
        if condition in {"tail_small", "tail_large"}:
            size = 300 if condition == "tail_small" else 1300
            tail = {"role": "user", "content": f"Context sections {step}:\n" + text(rng, size, f"tail{step}")}
            request.append(tail)
            tail_tokens = len(ENC.encode(tail["content"]))
        payload = {"model": SETTINGS.model, "input": request, "tools": [], "max_output_tokens": 64,
                   "store": False, "stream": True, "reasoning": {"effort": "low"}}
        if key:
            payload["prompt_cache_key"] = key
        started = time.time()
        try:
            status, terminal, usage = send(payload)
            error = None
        except Exception as exc:  # keep going; the row records the failure
            status, terminal, usage, error = None, None, None, type(exc).__name__
        row = dict(condition=condition, rep=rep, step=step, nonce=nonce, started=started,
                   seconds=round(time.time() - started, 2), status=status, terminal=terminal,
                   error=error, items=len(request), tail_tokens=tail_tokens,
                   local_tokens=sum(len(ENC.encode(m["content"])) for m in request),
                   input_tokens=(usage or {}).get("input_tokens"),
                   cached_tokens=((usage or {}).get("input_tokens_details") or {}).get("cached_tokens"),
                   output_tokens=(usage or {}).get("output_tokens"))
        with LOCK, OUT.open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(condition, rep, step, row["input_tokens"], row["cached_tokens"], terminal, error, flush=True)
        time.sleep(gap)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--conditions", default="append,append_nokey,tail_small,tail_large,statelog")
    parser.add_argument("--reps", type=int, default=2)
    parser.add_argument("--steps", type=int, default=10)
    parser.add_argument("--gap", type=float, default=3.0)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    jobs = [(c, r) for r in range(args.reps) for c in args.conditions.split(",")]
    with ThreadPoolExecutor(args.workers) as pool:
        for future in [pool.submit(trial, c, r, args.steps, args.gap) for c, r in jobs]:
            future.result()


if __name__ == "__main__":
    main()
