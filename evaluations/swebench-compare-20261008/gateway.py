"""Keep credentials on the controller and record usage from the shared model endpoint."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn, UnixStreamServer

import httpx

from aster.config import load_settings

REPO = Path(__file__).resolve().parents[2]
PRIVATE = REPO / "artifacts/private/swebench-compare-20261008"
SETTINGS = load_settings(REPO / ".env")


class Gateway(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_POST(self):
        match = re.fullmatch(r"/([a-z0-9_-]+)/v1/responses", self.path)
        if not match:
            self.send_error(404)
            return
        job = PRIVATE / "runs" / match[1]
        if not (job / "launch.json").exists():
            self.send_error(403)
            return
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        original = {k: payload.get(k) for k in ("model", "reasoning", "max_output_tokens", "temperature", "top_p")}
        payload.update(model=SETTINGS.model, max_output_tokens=8192, store=False)
        payload["reasoning"] = {**payload.get("reasoning", {}), "effort": "medium"}
        payload.pop("temperature", None)
        payload.pop("top_p", None)
        request_id = uuid.uuid4().hex
        archive = job / "provider"
        archive.mkdir(exist_ok=True)
        encoded = json.dumps(payload).encode()
        (archive / (request_id + ".request.json")).write_bytes(encoded)
        started = time.monotonic()
        row = {"request_id": request_id, "started": time.time(), "original_parameters": original,
               "model": SETTINGS.model, "reasoning_effort": "medium", "max_output_tokens": 8192,
               "request_sha256": hashlib.sha256(encoded).hexdigest(), "usage": None}
        sent = False
        try:
            with httpx.Client(timeout=httpx.Timeout(120, connect=20)) as client:
                with client.stream("POST", SETTINGS.base_url.rstrip("/") + "/responses", content=encoded,
                                   headers={"Authorization": "Bearer " + SETTINGS.api_key.get_secret_value(),
                                            "Content-Type": "application/json"}) as response:
                    row["http_status"] = response.status_code
                    self.send_response(response.status_code)
                    self.send_header("Content-Type", response.headers.get("content-type", "text/event-stream"))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    sent = True
                    pending = b""
                    with (archive / (request_id + ".response")).open("wb") as output:
                        for chunk in response.iter_bytes():
                            output.write(chunk)
                            pending += chunk
                            while b"\n" in pending:
                                line, pending = pending.split(b"\n", 1)
                                if line.startswith(b"data: ") and line[6:].strip() != b"[DONE]":
                                    event = json.loads(line[6:])
                                    result = event.get("response", {})
                                    if result.get("usage"):
                                        row["usage"] = result["usage"]
                                    if event.get("type") in {"response.completed", "response.failed", "response.incomplete"}:
                                        row["terminal"] = event["type"]
                                        row["response_model"] = result.get("model")
                            self.wfile.write(chunk)
                            self.wfile.flush()
        except Exception as exc:
            row["error"] = type(exc).__name__
            if not sent:
                self.send_error(502, "Upstream unavailable")
        finally:
            row["seconds"] = round(time.monotonic() - started, 3)
            (archive / (request_id + ".json")).write_text(json.dumps(row, indent=2) + "\n")
            self.close_connection = True


if __name__ == "__main__":
    import sys
    if sys.argv[1].startswith("/"):
        class UnixServer(ThreadingMixIn, UnixStreamServer):
            daemon_threads = True
        server = UnixServer(sys.argv[1], Gateway)
    else:
        server = ThreadingHTTPServer((sys.argv[1], 18080), Gateway)
    print("Evaluation model gateway ready", flush=True)
    server.serve_forever()
