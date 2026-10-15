"""Ten bounded requests: cache exploration then four-way provider health check."""
import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx

from aster.config import load_settings
from aster.providers.usage import normalize_usage

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
PRIVATE = REPO / "artifacts/private" / ROOT.name / "cache-probe"


async def main():
    assert not PRIVATE.exists(), "The fixed probe has already been attempted"
    PRIVATE.mkdir(parents=True)
    settings = load_settings(REPO / ".env")
    assert settings.model == "gpt-5.6-luna"
    rows = []
    prefixes = {kind: "Cache probe " + uuid4().hex + "\n" + "\n".join(
        f"Record {i:04d}: the module reads a file and validates its contents." for i in range(256)
    ) for kind in ("baseline", "candidate")}
    cache_key = uuid4().hex
    headers = {"Authorization": "Bearer " + settings.api_key.get_secret_value()}

    async def request(label, prefix, key=None):
        payload = {"model": settings.model, "store": False, "max_output_tokens": 128,
                   "reasoning": {"effort": "medium"}, "input": [
                       {"role": "system", "content": "Read the reference and reply only OK.\n" + prefix},
                       {"role": "user", "content": "Reply only OK."}]}
        if key is not None:
            payload["prompt_cache_key"] = key
        (PRIVATE / (label + ".request.json")).write_text(json.dumps(payload))
        row = {"label": label, "with_cache_key": key is not None}
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await asyncio.wait_for(client.post(
                    settings.base_url.rstrip("/") + "/responses", headers=headers, json=payload), 60)
            (PRIVATE / (label + ".response")).write_bytes(response.content)
            row["http_status"] = response.status_code
            body = response.json()
            row["status"] = body.get("status")
            row["response_model"] = body.get("model")
            row["usage"] = normalize_usage(body.get("usage"), "responses")
            row["healthy"] = (response.is_success and bool(body.get("usage"))
                              and body.get("model") == settings.model
                              and body.get("status") in ("completed", "incomplete"))
        except Exception as exc:
            row.update(error=type(exc).__name__, healthy=False)
        row["seconds"] = round(time.monotonic() - started, 3)
        rows.append(row)
        (PRIVATE / (label + ".json")).write_text(json.dumps(row, indent=2) + "\n")
        print(json.dumps(row), flush=True)
        return row

    for number, kind in enumerate(("baseline", "candidate", "candidate", "baseline", "baseline", "candidate")):
        await request(f"cache-{number + 1}-{kind}", prefixes[kind], cache_key if kind == "candidate" else None)
    smoke = await asyncio.gather(*[
        request("health-" + str(i), "Independent health check " + uuid4().hex, uuid4().hex)
        for i in range(4)])
    result = {"requests": rows, "concurrency_health_passed": all(r["healthy"] for r in smoke),
              "cache_field_accepted": all(r["healthy"] for r in rows if "candidate" in r["label"]),
              "scope": "Small exploratory sample; no guarantee of cache routing or fee savings."}
    (ROOT / "cache-probe.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
