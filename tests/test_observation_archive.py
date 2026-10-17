"""Archived tool text stays recoverable without granting access to private run files."""

import json
import os

import pytest

from aster.safety.redaction import Redactor
from aster.storage.evidence import EvidenceStore
from aster.storage.workspace import BoundaryError, digest


def test_archive_pages_reconstruct_redacted_text_and_bind_source(tmp_path):
    store = EvidenceStore(tmp_path, Redactor(["private-provider-secret"]))
    original = "开始\n" + "log line\n" * 200 + "private-provider-secret\n结束"
    expected = original.replace("private-provider-secret", "[REDACTED]")
    reference = store.archive_observation("run", "call", "run_shell", original)
    assert reference["sha256"] == digest(expected)
    assert reference["characters"] == len(expected)
    assert reference["call_id"] == "call" and reference["tool"] == "run_shell"
    path = store.path("runs", "run", "observations", reference["id"] + ".json")
    assert "private-provider-secret" not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    modified = path.stat().st_mtime_ns
    assert store.archive_observation("run", "call", "run_shell", original) == reference
    assert path.stat().st_mtime_ns == modified

    # Reopen just as resume does: no in-memory index or cached full body is necessary.
    restored = EvidenceStore(tmp_path, store.redactor)
    chunks = []
    offset = 0
    while offset is not None:
        page = restored.read_observation("run", reference["id"], offset=offset, limit=137)
        assert len(page["content"]) <= 137
        chunks.append(page["content"])
        offset = page["next_offset"]
    assert "".join(chunks) == expected


def test_archive_literal_search_and_bounded_listing(tmp_path):
    store = EvidenceStore(tmp_path, Redactor())
    references = []
    for i, text in enumerate(["header\nkey.*=one\nkey.*=two\n", "second", "third"]):
        reference = store.archive_observation("run", f"call-{i}", "run_shell", text)
        path = store.path("runs", "run", "observations", reference["id"] + ".json")
        os.utime(path, ns=(1000 + i, 1000 + i))
        references.append(reference)
    listing = store.list_observations("run", limit=2)
    assert listing["observations"] == sorted(references, key=lambda ref: ref["id"])[:2]
    assert listing["next_offset"] == 2 and listing["total"] == 3
    assert (
        store.list_observations("run", offset=2)["observations"]
        == sorted(references, key=lambda ref: ref["id"])[2:]
    )
    assert store.list_observations("empty")["observations"] == []
    first = store.read_observation("run", references[0]["id"], query="key.*", limit=9)
    assert first["content"] == "key.*=one" and first["offset"] == 7
    second = store.read_observation(
        "run", references[0]["id"], query="key.*", offset=first["next_offset"]
    )
    assert second["content"] == "key.*=two\n"
    absent = store.read_observation("run", references[0]["id"], query="key.+")
    assert not absent["matched"] and absent["content"] == "" and absent["next_offset"] is None
    searched = store.list_observations("run", query="key.*")
    assert searched["total"] == 1 and searched["next_offset"] is None
    assert searched["observations"][0] == references[0] | {
        "match_offset": 7,
        "preview": "key.*=one\nkey.*=two\n",
    }
    assert store.list_observations("run", query="key.+")["observations"] == []


def test_archive_does_not_expose_other_runs_or_private_artifacts(tmp_path):
    store = EvidenceStore(tmp_path, Redactor())
    reference = store.archive_observation("run", "call", "run_shell", "captured")
    other = store.archive_observation("other", "call", "run_shell", "captured")
    assert reference["id"] != other["id"]
    with pytest.raises(FileNotFoundError):
        store.read_observation("other", reference["id"])
    for identifier in ("../checkpoint", "../../other/trace", "/etc/passwd", "a" * 63):
        with pytest.raises(BoundaryError, match="invalid_observation_id"):
            store.read_observation("run", identifier)
    for run_id in ("../other", "run/../other", ""):
        with pytest.raises(BoundaryError, match="invalid_observation_run"):
            store.read_observation(run_id, reference["id"])
        with pytest.raises(BoundaryError, match="invalid_observation_run"):
            store.list_observations(run_id)


@pytest.mark.parametrize(
    "arguments",
    [{"offset": -1}, {"offset": True}, {"limit": 0}, {"limit": 12001}, {"query": "x" * 1001}],
)
def test_archive_rejects_unbounded_pages(tmp_path, arguments):
    store = EvidenceStore(tmp_path, Redactor())
    reference = store.archive_observation("run", "call", "run_shell", "captured")
    with pytest.raises(ValueError, match="invalid_observation_page"):
        store.read_observation("run", reference["id"], **arguments)


def test_archive_detects_tampering_symlinks_and_invalid_offsets(tmp_path):
    store = EvidenceStore(tmp_path, Redactor())
    reference = store.archive_observation("run", "call", "run_shell", "captured")
    path = store.path("runs", "run", "observations", reference["id"] + ".json")
    record = json.loads(path.read_text())
    record["content"] = "tampered"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="observation_integrity_error"):
        store.read_observation("run", reference["id"])
    store.archive_observation("run", "call", "run_shell", "captured")
    with pytest.raises(ValueError, match="page_out_of_range"):
        store.read_observation("run", reference["id"], offset=100)
    with pytest.raises(ValueError, match="page_out_of_range"):
        store.list_observations("run", offset=100)
    path.unlink()
    path.symlink_to(tmp_path / ".aster" / "runs" / "run" / "checkpoint.json")
    with pytest.raises(BoundaryError, match="artifact_symlink"):
        store.read_observation("run", reference["id"])
    with pytest.raises(BoundaryError, match="artifact_symlink"):
        store.list_observations("run")


def test_archive_write_failure_propagates_before_returning_a_reference(tmp_path, monkeypatch):
    store = EvidenceStore(tmp_path, Redactor())

    def disk_full(*_):
        raise OSError("disk full")

    monkeypatch.setattr("aster.storage.evidence.atomic_json", disk_full)
    with pytest.raises(OSError, match="disk full"):
        store.archive_observation("run", "call", "run_shell", "irreplaceable observation")
    assert store.list_observations("run")["observations"] == []
