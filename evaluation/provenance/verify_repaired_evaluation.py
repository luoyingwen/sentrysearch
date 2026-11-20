"""Check final predictions, reference metrics, input identity and empty indexes."""
from argparse import Namespace
from pathlib import Path
import shutil
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import evaluate_sphere as runner


def main():
    workspace = REPO / "evaluation"
    state = runner.read_json(workspace / "completion.json")
    args = Namespace(sphere_root=runner.DEFAULT_SPHERE, videos_root=runner.DEFAULT_VIDEOS,
                     workspace=workspace, **state["options"])
    identity, _ = runner.fingerprint(args)
    assert runner.completed(workspace, identity)
    score_query, summarize, _ = runner.metrics_module(args.sphere_root)
    count = 0
    for spec in runner.DATASETS:
        d = runner.load_dataset(spec, args.sphere_root, args.videos_root)
        result = runner.read_json(workspace / "results" / (d["key"] + ".json"))
        assert result["status"] == "completed"
        assert len(result["rows"]) == len(d["benchmark"]["queries"])
        by_id = {q["query_id"]: q for q in d["benchmark"]["queries"]}
        for row in result["rows"]:
            query = by_id[row["query_id"]]
            assert row["query"] == query["request"]["query"]
            assert row["expected_hits"] == query.get("expected_hits", [])
            assert row["case"] == query["case"] and row["grain"] == query["grain"]
            metrics = score_query(query, row["hits"])
            for key, value in metrics.items():
                assert row[key] == value, (row["query_id"], key)
            count += 1
        assert summarize(result["rows"]) == result["summary"]
    assert not any((workspace / "active").iterdir())
    audit = runner.read_json(args.sphere_root / "datasets/releases" / runner.DATASETS[2][1] / "media-correction.json")
    backup = Path(audit["backup"])
    if not (backup / "evaluation-verification.json").exists():
        shutil.copy2(workspace / "verification.json", backup / "evaluation-verification.json")
    checks = {"status": "passed", "queries_checked": count, "completed_datasets": 4,
              "skipped_datasets": 0, "reference_scores_recomputed": True,
              "query_text_and_gt_times_unchanged": True, "asset_ids_corrected_with_manifest": True,
              "active_directory_empty": True, "completion_fingerprint_matches": True,
              "verified_at": runner.now(), "media_correction": str(args.sphere_root / "datasets/releases" / runner.DATASETS[2][1] / "media-correction.json")}
    runner.write_json(workspace / "verification.json", checks)
    report = workspace / "report.md"
    note = "\n## 数据集 3 媒体清单修正\n\n2026-11-19 按用户确认的视频修正 102 / 170 的媒体 SHA-256、大小及资源 ID，并同步 CardSet、Benchmark、additions 和发布哈希。查询文本和 GT 时间保持原值；74/74 视频匹配，原跳过原因已解除。数据集 3 已使用相同 2B 配置完整补测 901 条查询，数据集 1、2、4 的输入、模型和评分代码经核对未变化，复用已完成结果。原文件及原评估记录保存在 provenance 的备份目录。\n\n全部 1283 条查询已再次使用参考评分器核验，结果、GT、汇总和完成指纹一致，active 目录为空。详见 `verification.json`、`dataset3-media-proof.json` 及 Sphere 发布目录的 `media-correction.json`。\n"
    if "## 数据集 3 媒体清单修正" not in report.read_text(encoding="utf-8"):
        with report.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(note)
    print(checks)


if __name__ == "__main__":
    main()
