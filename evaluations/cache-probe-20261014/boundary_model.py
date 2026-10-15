"""Offline: explain Aster's cached_tokens with a one-snapshot block-boundary model. No model calls.

Rule (inferred from gateway archives and confirmed by probe.py):
  after a request with server input I, the provider keeps one reusable snapshot at
  S = floor_block(I) = (I - OFF) // 1024 * 1024 + OFF.
  If S cuts into the trailing dynamic message (S > I - tail), the snapshot contains tail
  text that the next request replaces, so it is unusable; the last usable snapshot stays.
"""
import argparse
import collections
import json
import sys
from pathlib import Path

import tiktoken

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "context-cache-20261013"))
from metrics import requests, stable  # noqa: E402

ENC = tiktoken.get_encoding("o200k_base")


def analyse(runs, offset, *, append_only=False):
    floor = lambda x: (x - offset) // 1024 * 1024 + offset  # noqa: E731
    count, uncached = collections.Counter(), collections.Counter()
    exact = predicted = 0
    counterfactual = 0
    for directory in sorted(runs.glob("aster-*/provider")):
        snaps, prev = [], None
        for metadata, payload in requests(directory):
            usage = metadata.get("usage") or {}
            i = usage.get("input_tokens", 0)
            c = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0
            if not i:
                continue
            # v10 retains every input item; a final tool result is not a replaced tail.
            tail = 0 if append_only else len(ENC.encode(payload["input"][-1]["content"])) + 4
            if prev is None:
                kind = "first"
                counterfactual += i - c
            elif not (prev["input"] == payload["input"][:len(prev["input"])] if append_only
                      else stable(prev["input"], payload["input"])):
                kind, snaps = "rewrite", []
                counterfactual += i - c
            else:
                guess = max(snaps) if snaps else 0
                predicted += 1
                exact += guess == c
                if c / prev["i"] >= 0.85:
                    kind = "full"
                elif guess / prev["i"] >= 0.85:
                    kind = "low:provider_miss"
                else:
                    kind = "low:tail_straddle"
                # Pure-append counterfactual: previous request's stable part is always reusable.
                counterfactual += i - min(i, floor(prev["i"] - prev["tail"]))
            count[kind] += 1
            uncached[kind] += i - c
            if prev is not None and snaps and c < max(snaps):
                snaps = [c]
            s = floor(i)
            if s <= i - tail:
                snaps.append(s)
            elif c:
                snaps.append(c)
            prev = dict(input=payload["input"], i=i, tail=tail)
    return dict(offset=offset, predicted_requests=predicted, exact_predictions=exact,
                kinds={k: dict(requests=count[k], uncached=uncached[k]) for k in count},
                total_uncached=sum(uncached.values()),
                pure_append_counterfactual_uncached=counterfactual)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path)
    parser.add_argument("--offset", type=int, default=512)
    parser.add_argument("--append-only", action="store_true", help="Retain the whole v10 input; tail=0")
    args = parser.parse_args()
    print(json.dumps(analyse(args.runs, args.offset, append_only=args.append_only), indent=2))
