import os

import pytest

from aster.skills import MAX_SKILL_BYTES, Skills


@pytest.mark.parametrize("link_location", ["file", "directory", "parent", "internal_file"])
def test_skills_reject_symlinks(tmp_path, link_location):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    target = workspace / "target" if link_location == "internal_file" else tmp_path / "outside"
    target.mkdir()
    (target / "hidden.md").write_text("must not load", encoding="utf-8")
    if link_location == "directory":
        (workspace / ".skills").symlink_to(target, target_is_directory=True)
    elif link_location == "parent":
        (target / "skills").mkdir()
        (target / "skills" / "hidden.md").write_text("must not load", encoding="utf-8")
        (workspace / ".aster").symlink_to(target, target_is_directory=True)
    else:
        (workspace / ".skills").mkdir()
        (workspace / ".skills" / "hidden.md").symlink_to(target / "hidden.md")

    skills = Skills(workspace)
    assert skills.list() == []
    with pytest.raises(FileNotFoundError):
        skills.load("hidden")


def test_skills_catalog_matches_regular_loadable_files(tmp_path):
    root = tmp_path / ".skills"
    root.mkdir()
    (root / "valid.md").write_text("local instructions", encoding="utf-8")
    (root / "directory.md").mkdir()
    os.mkfifo(root / "fifo.md")
    (root / "invalid name.md").write_text("not a valid skill name", encoding="utf-8")
    protected = tmp_path / ".aster" / "skills"
    protected.mkdir(parents=True)
    (protected / "second.md").write_text("another skill", encoding="utf-8")

    skills = Skills(tmp_path)
    assert skills.list() == ["second", "valid"]
    assert skills.load("valid") == "local instructions"
    assert skills.load("second") == "another skill"
    with pytest.raises(FileNotFoundError):
        skills.load("fifo")


def test_skill_size_limit_counts_bytes_before_reading(tmp_path, monkeypatch):
    root = tmp_path / ".skills"
    root.mkdir()
    (root / "boundary.md").write_bytes(b"a" * MAX_SKILL_BYTES)
    (root / "oversize.md").write_text("中" * (MAX_SKILL_BYTES // 3 + 1), encoding="utf-8")
    skills = Skills(tmp_path)
    assert len(skills.load("boundary")) == MAX_SKILL_BYTES

    def unexpected_read(*args, **kwargs):
        raise AssertionError("oversize skill must be rejected before opening")

    monkeypatch.setattr(type(root), "open", unexpected_read)
    with pytest.raises(ValueError, match="exceeds 20 KiB"):
        skills.load("oversize")
