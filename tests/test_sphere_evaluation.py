"""Checks for benchmark identity, archive alignment, and deletion boundaries."""
import hashlib
import importlib.util
from pathlib import Path
import zipfile

import pytest

spec = importlib.util.spec_from_file_location("sphere_runner", Path(__file__).parents[1] / "scripts" / "evaluate_sphere.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def dataset(tmp_path, expected, members):
    archive = tmp_path / "videos.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for name, content in members:
            z.writestr(name, content)
    return {"archive": str(archive), "media": [
        {"asset_id": "asset-1", "path": "original.mp4", "size": len(expected),
         "sha256": hashlib.sha256(expected).hexdigest()}]}


def test_alignment_accepts_renamed_identical_content(tmp_path):
    d = dataset(tmp_path, b"video", [("nested/renamed_LL360.mp4", b"video")])
    a = runner.align_archive(d)
    assert a["aligned"]
    assert a["matched"][0]["asset_id"] == "asset-1"


def test_alignment_rejects_same_name_with_changed_bytes(tmp_path):
    d = dataset(tmp_path, b"video", [("original.mp4", b"wrong")])
    a = runner.align_archive(d)
    assert not a["aligned"]
    assert len(a["problems"]) == 1
    assert a["problems"][0]["filename_candidates"][0]["sha256"] != d["media"][0]["sha256"]


def test_alignment_rejects_ambiguous_duplicate_and_extra_video(tmp_path):
    d = dataset(tmp_path, b"video", [("a.mp4", b"video"), ("b.mp4", b"video"), ("c.mp4", b"other")])
    a = runner.align_archive(d)
    assert not a["aligned"]
    assert a["problems"][0]["hash_matches"] == 2
    assert len(a["extra_videos"]) == 3


def test_cleanup_removes_all_active_data_preserves_results(tmp_path):
    active = tmp_path / "active"
    (active / "db").mkdir(parents=True)
    (active / "db" / "old.sqlite").write_bytes(b"old")
    report = tmp_path / "report.md"
    report.write_text("evidence")
    result = runner.safe_clear(active, tmp_path)
    assert result["empty_after_cleanup"]
    assert not list(active.iterdir())
    assert report.read_text() == "evidence"


def test_cleanup_rejects_outside_target(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ValueError, match="Unsafe cleanup"):
        runner.safe_clear(outside, tmp_path)
    assert outside.exists()


def test_incomplete_report_never_marks_goal_done(tmp_path):
    runner.write_json(tmp_path / "completion.json", {"status": "running", "fingerprint": "same"})
    assert not runner.completed(tmp_path, "same")


def test_active_run_lock_returns_none_for_second_caller(tmp_path):
    first = runner.acquire_lock(tmp_path)
    assert first is not None
    try:
        assert runner.acquire_lock(tmp_path) is None
    finally:
        first.close()
    released = runner.acquire_lock(tmp_path)
    assert released is not None
    released.close()
