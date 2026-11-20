"""Repair the user-confirmed TSV360 media identities with a restorable audit trail."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

REPO = Path(__file__).resolve().parents[2]
SPHERE = Path(r"C:\Users\luoyw\work\lab\Sphere\sphere-eval")
RELEASE = SPHERE / "datasets/releases/sphere-public-tsv360-cross-video-agreement-full-queries-20261116-r01"
SOURCE = SPHERE.parent / "360videos/videos"
ORIGINAL = {
    "102": "b068562826dca9e6e7fce4564eac8e95cf3ba971aba547d998fb6465373961f9",
    "170": "6fe647a223bc552df4f051f365503049c7f9d83213b86337dfe9e6d5cba9c2b2",
}


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    sys.path[:0] = [str(REPO), str(SPHERE)]
    from sphere_eval.contracts import validate_cards, validate_queries
    from sentrysearch.chunker import _get_video_duration

    names = ("media.json", "card-set.json", "benchmark.json", "additions.json")
    original = {n: json.loads((RELEASE / n).read_text(encoding="utf-8-sig")) for n in names}
    mapping, corrections = {}, []
    for number, old_sha in ORIGINAL.items():
        expected = next(m for m in original["media.json"] if m["path"] == f"tsv360/{number}.mp4")
        if expected["sha256"] != old_sha:
            raise ValueError("Manifest already changed; inspect before applying this one-time repair")
        source = SOURCE / f"{number}_LL360.mp4"
        with source.open("rb") as stream:
            new_sha = hashlib.file_digest(stream, "sha256").hexdigest()
        duration = _get_video_duration(str(source))
        old_id, new_id = "tsv360-sha256-" + old_sha, "tsv360-sha256-" + new_sha
        max_gt = max(h["t_end"] for q in original["benchmark.json"]["queries"] for h in q.get("expected_hits", []) if h["asset_id"] == old_id)
        if max_gt > duration + 0.5:
            raise ValueError(f"GT exceeds actual media duration for {number}")
        mapping.update({old_id: new_id, old_sha: new_sha})
        corrections.append({"source_file": str(source), "manifest_path": expected["path"],
                            "old_asset_id": old_id, "new_asset_id": new_id,
                            "old_sha256": old_sha, "new_sha256": new_sha,
                            "old_size": expected["size"], "new_size": source.stat().st_size,
                            "actual_duration_s": duration, "max_gt_end_s": max_gt})

    def remap(value, replacements):
        if isinstance(value, dict):
            return {k: remap(v, replacements) for k, v in value.items()}
        if isinstance(value, list):
            return [remap(v, replacements) for v in value]
        return replacements.get(value, value) if isinstance(value, str) else value

    updated = {n: remap(v, mapping) for n, v in original.items()}
    for correction in corrections:
        row = next(m for m in updated["media.json"] if m["asset_id"] == correction["new_asset_id"])
        row["size"] = correction["new_size"]
    # Prove that reversing the two identities and sizes restores every JSON value.
    inverse = {v: k for k, v in mapping.items()}
    restored = {n: remap(v, inverse) for n, v in updated.items()}
    for correction in corrections:
        next(m for m in restored["media.json"] if m["asset_id"] == correction["old_asset_id"])["size"] = correction["old_size"]
    assert restored == original
    cards = updated["card-set.json"]["cards"]
    queries = updated["benchmark.json"]["queries"]
    validate_cards(cards)
    validate_queries(queries, {c["asset_id"]: c["duration_s"] for c in cards})
    assert len(cards) == len(updated["media.json"]) == 74
    assert len(queries) == 901
    assert sum(len(q["expected_hits"]) for q in queries) == 1312
    assert {c["asset_id"] for c in cards} == {m["asset_id"] for m in updated["media.json"]}
    assert all(m["asset_id"] == "tsv360-sha256-" + m["sha256"] for m in updated["media.json"])

    timestamp = datetime.now(timezone.utc)
    backup = Path(__file__).parent / ("dataset3-manifest-before-" + timestamp.strftime("%Y%m%dT%H%M%SZ"))
    backup.mkdir()
    for name in (*names, "verification.json", "README.md"):
        shutil.copy2(RELEASE / name, backup / name)
    for name in ("completion.json", "report.md", "dataset3-media-proof.json"):
        path = REPO / "evaluation" / name
        if path.exists():
            shutil.copy2(path, backup / ("evaluation-" + name))
    shutil.copy2(REPO / "evaluation/results/dataset3.json", backup / "evaluation-dataset3.json")
    audit = {"corrected_at": timestamp.isoformat(), "reason": "User confirmed the supplied 102_LL360.mp4 and 170_LL360.mp4 are authoritative; repair erroneous manifest identities.",
             "backup": str(backup), "corrections": corrections,
             "checks": {"cards": 74, "queries": 901, "expected_hits": 1312,
                        "contracts_valid": True, "query_text_and_gt_times_unchanged": True,
                        "all_other_json_values_unchanged": True},
             "scope": "Only this 20261116-r01 release; other releases and input videos remain unchanged."}
    output = {n: encode(v) for n, v in updated.items()}
    output["media-correction.json"] = encode(audit)
    verification = json.loads((RELEASE / "verification.json").read_text(encoding="utf-8-sig"))
    old_hashes = copy.deepcopy(verification["file_sha256"])
    verification["file_sha256"] = {n: sha(raw) for n, raw in output.items()}
    verification["media_correction"] = "media-correction.json"
    output["verification.json"] = encode(verification)
    readme = (RELEASE / "README.md").read_text(encoding="utf-8-sig")
    for name, old_hash in old_hashes.items():
        readme = readme.replace(old_hash, sha(output[name]))
    old_ver_hash = sha((RELEASE / "verification.json").read_bytes().replace(b"\r\n", b"\n"))
    readme = readme.replace(old_ver_hash, sha(output["verification.json"]))
    readme = readme.replace("（与 20261112-r01 相同）", "（来源于 20261112-r01，两个媒体 ID 已按下述记录修正）")
    readme += "\n## 2026-11-19 媒体清单修正\n\n以用户确认的 `102_LL360.mp4` 和 `170_LL360.mp4` 修正媒体 SHA-256、大小及由 SHA-256 生成的资源 ID，同步 CardSet、Benchmark 和 additions 中的引用。查询文本、标注时间、74 视频 / 901 查询 / 1312 GT 数量不变。\n\n修正映射、原文件备份位置及校验证据见 [media-correction.json](media-correction.json)。`verification.json` 与本页哈希已更新；JSON 使用 LF 换行。旧评估的跳过结论对应修正前清单，需要重新评估数据集 3。\n"
    readme += f"\n- `media-correction.json`：`{sha(output['media-correction.json'])}`\n"
    output["README.md"] = readme.encode("utf-8")
    for name, raw in output.items():
        temporary = RELEASE / (name + ".repair-tmp")
        temporary.write_bytes(raw)
    for name in output:
        (RELEASE / (name + ".repair-tmp")).replace(RELEASE / name)
    for name, expected_hash in verification["file_sha256"].items():
        assert sha((RELEASE / name).read_bytes()) == expected_hash
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
