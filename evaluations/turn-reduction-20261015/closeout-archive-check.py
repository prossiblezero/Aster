"""Read the five historical mismatches; test new archives in a temporary directory."""

import hashlib
import json
import tarfile
import tempfile
from pathlib import Path

from aster.safety.redaction import Redactor
from aster.storage.evidence import EvidenceStore

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / (
    "artifacts/private/turn-reduction-20261015/round2/attempt2/runs/"
    "aster-terminal-cobol-modernization"
)
CASES = {
    "bd75a801da0540838c697bfd1165b029": 1207,
    "386a6c7bc00e42cdba48521bb82791ba": 1286,
    "aecc1f7a785a46bea4e225e4e65611c9": 1501,
    "a0fd3b4c03a64e05b1ad356d5925ce72": 1871,
    "1bcc2aba145e4f77ac92945204cd9c2c": 2351,
}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    archive = ROOT / ".aster.tar"
    sources = [archive, *(ROOT / "provider" / f"{ident}.request.json" for ident in CASES)]
    before = {path: sha(path) for path in sources}
    checked = []
    with tarfile.open(archive) as bundle, tempfile.TemporaryDirectory() as temporary:
        store = EvidenceStore(Path(temporary), Redactor())
        contexts = {}
        for member in bundle.getmembers():
            if member.isfile() and member.name.endswith("/trace.jsonl"):
                for line in bundle.extractfile(member):
                    event = json.loads(line)
                    if event["event"] == "context_built":
                        contexts[event["seq"]] = event
        for ident, seq in CASES.items():
            payload = json.loads((ROOT / "provider" / f"{ident}.request.json").read_bytes())
            event = contexts[seq]
            old = json.load(bundle.extractfile(".aster/" + event["request_artifact"]))["payload"]
            differences = [i for i, (a, b) in enumerate(zip(old["input"], payload["input"], strict=True)) if a != b]
            assert differences == [62], ident
            assert payload["input"][62]["type"] == "reasoning"
            assert store.redactor.value(payload["input"], mask_fields=False) == old["input"], ident
            store.save((ident + ".json",), {"payload": payload},
                       native_history_paths=(("payload", "input"),))
            saved = json.loads(store.path(ident + ".json").read_text())["payload"]
            assert saved == payload, ident
            normalized = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
            persisted = json.dumps(saved, sort_keys=True, ensure_ascii=False).encode()
            hashed = json.dumps(store.redact(payload, native_history_paths=(("input",),)),
                                sort_keys=True, ensure_ascii=False).encode()
            assert persisted == hashed == normalized, ident
            checked.append({"request_id": ident, "context_seq": seq,
                            "payload_and_hash_match": True,
                            "canonical_payload_sha256": hashlib.sha256(normalized).hexdigest()})
    assert before == {path: sha(path) for path in sources}, "Historical evidence changed"
    print(json.dumps({"checked": checked, "historical_evidence_unchanged": True,
                      "model_requests_made": 0}, indent=2))


if __name__ == "__main__":
    main()
