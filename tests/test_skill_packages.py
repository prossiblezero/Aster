import os
from contextlib import contextmanager

import pytest

from aster.skills import MAX_METADATA_BYTES, MAX_RESOURCES, MAX_SKILL_BYTES, Skills


def package(workspace, name="review", source=".skills", header=None, body="Body instructions\n"):
    root = workspace / source / name
    root.mkdir(parents=True, exist_ok=True)
    text = f"---\n{header}\n---\n{body}" if header is not None else body
    (root / "SKILL.md").write_text(text, encoding="utf-8")
    return root


def test_package_catalog_is_metadata_only_and_legacy_is_compatible(tmp_path):
    root = package(tmp_path, header="name: review\ndescription: Inspect a patch")
    (root / "references").mkdir()
    (root / "references" / "rules.md").write_text("Rules", encoding="utf-8")
    (tmp_path / ".skills" / "legacy.md").write_text("Old instructions", encoding="utf-8")
    skills = Skills(tmp_path)
    assert skills.list() == ["legacy", "review"]
    record = skills.catalog()[1]
    assert record == {
        "name": "review",
        "description": "Inspect a patch",
        "path": ".skills/review/SKILL.md",
        "base_dir": ".skills/review",
        "resources": ["references/rules.md"],
        "resources_truncated": False,
    }
    assert skills.load("review") == "Body instructions\n"
    assert skills.load("review", "references/rules.md") == "Rules"
    assert skills.load("legacy") == "Old instructions"
    with pytest.raises(ValueError, match="legacy skills"):
        skills.load("legacy", "rules.md")


@pytest.mark.parametrize(
    ("value", "description"),
    [
        ("Plain words", "Plain words"),
        ('"A quoted: description"', "A quoted: description"),
        ("'It''s useful'", "It's useful"),
        (">-\n  First line\n  second line", "First line second line"),
        ("|\n  First line\n  second line", "First line\nsecond line"),
    ],
)
def test_supported_frontmatter_strings(tmp_path, value, description):
    package(tmp_path, header=f"name: review\ndescription: {value}\nlicense: ignored")
    assert Skills(tmp_path).catalog()[0]["description"] == description


@pytest.mark.parametrize(
    ("header", "error"),
    [
        ("name: other", "name must match"),
        ("description: one\ndescription: two", "duplicate description"),
        ("description: [one, two]", "must be a string"),
        ("description: false", "must be a string"),
        ("description: 42", "must be a string"),
        ("description: &anchor value", "must be a string"),
        ("description: 'unclosed", "quoted description"),
        ("description: one\n  surprise", "unsupported multiline"),
        (" description: value", "unindented"),
        ("description: |", "nonempty string"),
        ("description: " + "a" * 1001, "1000 characters"),
    ],
)
def test_invalid_metadata_is_diagnosed_and_never_loaded(tmp_path, header, error):
    package(tmp_path, header=header)
    skills = Skills(tmp_path)
    assert error in skills.catalog()[0]["error"]
    assert skills.list() == []
    with pytest.raises(ValueError, match=error):
        skills.load("review")


def test_missing_oversized_and_non_utf8_metadata(tmp_path):
    root = package(tmp_path)
    cases = [
        (b"---\ndescription: absent end", "missing closing"),
        (b"---\nunknown: " + b"a" * MAX_METADATA_BYTES + b"\n---\n", "exceeds 4 KiB"),
        (b"---\ndescription: \xff\n---\n", "must be UTF-8"),
    ]
    for data, error in cases:
        (root / "SKILL.md").write_bytes(data)
        assert error in Skills(tmp_path).catalog()[0]["error"]
        with pytest.raises(ValueError, match=error):
            Skills(tmp_path).load("review")


def test_authoritative_package_cannot_mix_resources_or_fall_back(tmp_path):
    lower = package(tmp_path, body="Lower")
    (lower / "lower.md").write_text("Lower resource", encoding="utf-8")
    higher = package(tmp_path, source=".aster/skills", body="Higher")
    (higher.parent / "review.md").write_text("Legacy higher", encoding="utf-8")
    skills = Skills(tmp_path)
    assert skills.load("review") == "Higher"
    assert skills.catalog()[0]["path"] == ".aster/skills/review/SKILL.md"
    with pytest.raises(FileNotFoundError):
        skills.load("review", "lower.md")
    (higher / "SKILL.md").write_text("---\nname: invalid\n---\n", encoding="utf-8")
    with pytest.raises(ValueError, match="name must match"):
        skills.load("review")


def test_resources_are_text_not_execution_and_enforce_size_and_encoding(tmp_path):
    root = package(tmp_path)
    marker = tmp_path / "executed"
    script = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    (root / "script.py").write_text(script, encoding="utf-8")
    (root / "boundary.txt").write_bytes(b"a" * MAX_SKILL_BYTES)
    (root / "large.txt").write_bytes(b"a" * (MAX_SKILL_BYTES + 1))
    (root / "binary.txt").write_bytes(b"\xff")
    skills = Skills(tmp_path)
    assert skills.load("review", "script.py") == script
    assert not marker.exists()
    assert len(skills.load("review", "boundary.txt")) == MAX_SKILL_BYTES
    assert "large.txt" not in skills.catalog()[0]["resources"]
    with pytest.raises(ValueError, match="exceeds 20 KiB"):
        skills.load("review", "large.txt")
    with pytest.raises(ValueError, match="must be UTF-8"):
        skills.load("review", "binary.txt")


@pytest.mark.parametrize(
    "resource",
    [
        "../outside",
        "/etc/passwd",
        "a/../../outside",
        "a\\b",
        "C:/Windows/a",
        "a\x00b",
        ".env",
        ".ssh/id_rsa",
        "private.pem",
        ".aster/state",
        "node_modules/a",
    ],
)
def test_resources_reject_traversal_sensitive_and_control_paths(tmp_path, resource):
    package(tmp_path)
    with pytest.raises(ValueError, match="invalid skill resource path"):
        Skills(tmp_path).load("review", resource)


def test_resource_symlinks_and_special_files_are_never_read_or_listed(tmp_path):
    root = package(tmp_path)
    (root / "okay.md").write_text("Allowed", encoding="utf-8")
    (root / "internal.md").symlink_to(root / "okay.md")
    (root / "outside.md").symlink_to(tmp_path / "outside.md")
    (tmp_path / "outside.md").write_text("Hidden", encoding="utf-8")
    (root / "linked").symlink_to(tmp_path, target_is_directory=True)
    (root / ".env").write_text("secret", encoding="utf-8")
    os.mkfifo(root / "pipe")
    skills = Skills(tmp_path)
    assert skills.catalog()[0]["resources"] == ["okay.md"]
    for name in ["internal.md", "outside.md", "linked/outside.md", "pipe"]:
        with pytest.raises((FileNotFoundError, ValueError)):
            skills.load("review", name)


def test_catalog_reads_only_header_and_never_opens_resources(tmp_path, monkeypatch):
    root = package(tmp_path, header="description: Available", body="Never read this body")
    (root / "resource.md").write_text("Never read this resource", encoding="utf-8")
    skills = Skills(tmp_path)
    original_open = skills._open
    opened = []

    @contextmanager
    def guarded_open(path):
        opened.append(path)
        with original_open(path) as stream:

            class HeaderOnly:
                def readline(self, limit):
                    assert stream.tell() < len(b"---\ndescription: Available\n---\n")
                    return stream.readline(limit)

            yield HeaderOnly()

    monkeypatch.setattr(skills, "_open", guarded_open)
    assert skills.catalog()[0]["description"] == "Available"
    assert opened == [root / "SKILL.md"]


def test_resource_catalog_is_bounded_but_known_names_remain_loadable(tmp_path):
    root = package(tmp_path)
    for number in range(MAX_RESOURCES + 2):
        (root / f"resource-{number:03}.txt").write_text(str(number), encoding="utf-8")
    skills = Skills(tmp_path)
    record = skills.catalog()[0]
    assert record["resources_truncated"] is True
    assert len(record["resources"]) == MAX_RESOURCES
    assert skills.load("review", f"resource-{MAX_RESOURCES + 1:03}.txt") == str(MAX_RESOURCES + 1)


def test_directory_swap_to_symlink_cannot_redirect_reads(tmp_path, monkeypatch):
    workspace = tmp_path / "repo"
    package(workspace)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("Outside secret", encoding="utf-8")
    skills = Skills(workspace)
    original_open = os.open
    swapped = False

    def swapping_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "review" and not swapped:
            swapped = True
            (workspace / ".skills" / "review").rename(workspace / ".skills" / "original")
            (workspace / ".skills" / "review").symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)
    with pytest.raises(OSError):
        skills.load("review")
    assert swapped
