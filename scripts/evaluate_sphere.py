"""Evaluate SentrySearch on all four local Sphere releases, with isolated indexes.

Run with .venv/Scripts/python.exe scripts/evaluate_sphere.py. Completed runs are
fingerprinted and skipped on subsequent invocations. No annotations enter the
embedding model: cards/GT are used only for alignment and scoring.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
# The Xet reconstruction endpoint returned HTTP 416 on this machine. Plain
# HTTP supports resumable downloads and does not change model contents.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
if os.name == "nt":
    # Locked torchcodec cannot load its shared FFmpeg DLLs here; PyAV is
    # already installed and provides the project's supported fallback.
    os.environ.setdefault("FORCE_QWENVL_VIDEO_READER", "torchvision")
DEFAULT_SPHERE = Path(r"C:\Users\luoyw\work\lab\Sphere\sphere-eval")
DEFAULT_VIDEOS = Path(r"D:\work\Sphere\benchmark_videos")
DATASETS = [
    ("dataset1", "sphere-benchmark-20261028-r02", "sphere-cards-20261028-r01", "sphere-cards-20261028-r01-proxy-media-47-old.zip"),
    ("dataset2", "sphere-public-video-jonghwa-12v-32q-20261104-r01", None, "SP_eval_set_provided_by_Jonghwa.zip"),
    ("dataset3", "sphere-public-tsv360-cross-video-agreement-full-queries-20261116-r01", None, "videos_fix_format_74_cross_video_agreement.zip"),
    ("dataset4", "sphere-public-qvhighlights-test-s50-20261113-r01", None, "qvhighlights-test-s50-20261113-r01.zip"),
]


class AlignmentError(ValueError):
    """The supplied media cannot be scored against these GT timestamps."""


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def stream_sha(stream):
    digest = hashlib.sha256()
    while block := stream.read(4 * 1024 * 1024):
        digest.update(block)
    return digest.hexdigest()


def file_sha(path):
    with Path(path).open("rb") as stream:
        return stream_sha(stream)


def load_dataset(spec, sphere, videos):
    key, release, card_release, archive = spec
    releases = sphere / "datasets" / "releases"
    benchmark_path = releases / release / "benchmark.json"
    card_path = releases / (card_release or release) / "card-set.json"
    benchmark, card_set = read_json(benchmark_path), read_json(card_path)
    media_path = card_path.parent / "media.json"
    media = read_json(media_path) if media_path.exists() else card_set["media"]
    if benchmark["card_set_id"] != card_set["version"]:
        raise ValueError("Benchmark card_set_id differs from CardSet version")
    cards = card_set["cards"]
    ids = {c["asset_id"] for c in cards}
    if len(ids) != len(cards) or len({m["asset_id"] for m in media}) != len(media):
        raise ValueError("Duplicate asset IDs")
    if ids != {m["asset_id"] for m in media}:
        raise ValueError("Media and CardSet asset IDs differ")
    queries = benchmark["queries"]
    if len({q["query_id"] for q in queries}) != len(queries):
        raise ValueError("Duplicate query IDs")
    for q in queries:
        if not q["request"]["query"].strip():
            raise ValueError("Empty query")
        if q["grain"] not in ("asset", "moment"):
            raise ValueError("Unsupported grain")
        if q["case"] != "no_result" and not q.get("expected_hits"):
            raise ValueError("Answerable query has no GT")
        for hit in q.get("expected_hits", []):
            if hit["asset_id"] not in ids or not (0 <= hit["t_start"] < hit["t_end"]):
                raise ValueError("Invalid GT asset/window")
    return {"key": key, "release": release, "archive": str(videos / archive),
            "benchmark_path": str(benchmark_path), "card_path": str(card_path),
            "benchmark": benchmark, "cards": cards, "media": media,
            "query_count": len(queries), "video_count": len(cards)}


def align_archive(dataset):
    """Exact content hashes allow renamed files, without filename guesswork."""
    expected = {m["asset_id"]: m for m in dataset["media"]}
    by_sha = defaultdict(list)
    archive = Path(dataset["archive"])
    with zipfile.ZipFile(archive) as z:
        inventory = []
        for member in z.infolist():
            if member.is_dir() or Path(member.filename).suffix.lower() not in (".mp4", ".mov", ".mkv", ".webm", ".avi"):
                continue
            with z.open(member) as stream:
                sha = stream_sha(stream)
            row = {"member": member.filename, "size": member.file_size, "sha256": sha}
            inventory.append(row)
            by_sha[sha].append(row)
    matched, problems = [], []
    for asset_id, media in expected.items():
        candidates = by_sha[media["sha256"]]
        if len(candidates) != 1 or candidates[0]["size"] != media["size"]:
            basename = Path(media["path"]).name
            aliases = {basename, asset_id + ".mp4", Path(basename).stem + "_LL360.mp4"}
            nearby = [r for r in inventory if Path(r["member"]).name in aliases]
            problems.append({"asset_id": asset_id, "expected_sha256": media["sha256"],
                             "expected_size": media["size"], "hash_matches": len(candidates),
                             "filename_candidates": nearby})
        else:
            matched.append({"asset_id": asset_id, **candidates[0]})
    used = {r["member"] for r in matched}
    extra = [r for r in inventory if r["member"] not in used]
    return {"method": "exact_sha256_and_size", "matched": matched, "problems": problems,
            "extra_videos": extra, "archive_video_count": len(inventory),
            "aligned": not problems and not extra, "archive_sha256": file_sha(archive)}


def safe_clear(active, workspace):
    """Delete only this runner's exact active directory, after path validation."""
    active, workspace = Path(active).resolve(), Path(workspace).resolve()
    if active != workspace / "active" or active == REPO or not active.is_relative_to(workspace):
        raise ValueError(f"Unsafe cleanup target: {active}")
    if active.exists():
        shutil.rmtree(active)
    active.mkdir(parents=True)
    return {"at": now(), "path": str(active), "empty_after_cleanup": not any(active.iterdir())}


def metrics_module(sphere):
    # Load the actual reference scorer without installing Sphere's web/DB stack.
    sys.path.insert(0, str(sphere))
    from sphere_eval.eval.metrics import score_query, summarize, bucket_rows
    return score_query, summarize, bucket_rows


def fingerprint(args):
    paths = [Path(__file__), REPO / "pyproject.toml", REPO / "uv.lock"]
    paths += sorted((REPO / "sentrysearch").glob("*.py"))
    paths += [args.sphere_root / "sphere_eval" / "eval" / "metrics.py", args.sphere_root / "sphere_eval" / "taxonomy.py"]
    archives = []
    for spec in DATASETS:
        d = load_dataset(spec, args.sphere_root, args.videos_root)
        paths += [Path(d["benchmark_path"]), Path(d["card_path"])]
        media_path = Path(d["card_path"]).parent / "media.json"
        if media_path.exists():
            paths.append(media_path)
        p = Path(d["archive"])
        stat = p.stat()
        archives.append({"path": str(p), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    options = {k: getattr(args, k) for k in ("backend", "model", "chunk_duration", "overlap", "dedupe", "skip_still", "threshold")}
    payload = {"files": {str(p): file_sha(p) for p in paths}, "archives": archives, "options": options}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(), payload


def installed_environment():
    names = ("sentrysearch", "chromadb", "torch", "torchvision", "transformers", "qwen-vl-utils", "av", "imageio-ffmpeg")
    return {"python": sys.version, "executable": sys.executable,
            "packages": {n: importlib.metadata.version(n) for n in names}}


def worker(args):
    """A fresh process per dataset releases Chroma handles before deletion."""
    from sentrysearch.chunker import (_get_video_duration, _get_ffmpeg_executable,
                                     chunk_video, preprocess_chunk, is_still_frame_chunk)
    from sentrysearch.embedder import get_embedder
    from sentrysearch.search import search_footage
    from sentrysearch.store import SentryStore
    score_query, summarize, bucket_rows = metrics_module(args.sphere_root)
    active = args.workspace / "active"
    tempfile.tempdir = str(active / "temporary")
    Path(tempfile.tempdir).mkdir()
    d = read_json(active / "input.json")
    result_path = args.workspace / "results" / (d["key"] + ".json")
    result = {k: d[k] for k in ("key", "release", "archive", "query_count", "video_count", "alignment", "cleanup")}
    result.update(status="running", started_at=now(), rows=[], index={"chunks": 0, "skipped_still": 0})
    write_json(result_path, result)
    try:
        env = installed_environment()
        for filename, key in (("runtime-checks.json", "runtime_checks"), ("validation.json", "test_validation")):
            if (args.workspace / filename).exists():
                env[key] = read_json(args.workspace / filename)
        import torch
        env.update(cuda=torch.cuda.is_available(), torch_cuda=torch.version.cuda,
                   gpu=torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
                   ffmpeg=_get_ffmpeg_executable())
        env["video_reader"] = os.environ.get("FORCE_QWENVL_VIDEO_READER", "auto")
        if args.backend == "local" and not torch.cuda.is_available():
            raise RuntimeError("Local evaluation requires the configured CUDA GPU")
        embedder = get_embedder(args.backend, model=args.model)
        # Text inference verifies that the real model can load and run.
        query_vec = embedder.embed_query("A person walking outdoors.")
        if len(query_vec) != 768 or not all(math.isfinite(v) for v in query_vec):
            raise RuntimeError("Invalid smoke-test query embedding")
        env["query_dimensions"] = len(query_vec)
        files, asset_map = {}, {}
        media_dir = active / "media"
        media_dir.mkdir()
        with zipfile.ZipFile(d["archive"]) as z:
            for row in d["alignment"]["matched"]:
                # Flat hash names prevent archive path traversal.
                target = media_dir / (row["sha256"] + ".mp4")
                with z.open(row["member"]) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                if file_sha(target) != row["sha256"]:
                    raise RuntimeError("Extraction checksum changed")
                files[row["asset_id"]] = str(target.resolve())
                asset_map[str(target.resolve())] = row["asset_id"]
        cards = {c["asset_id"]: c for c in d["cards"]}
        duration_checks = []
        for asset_id, path in files.items():
            duration = _get_video_duration(path)
            expected = cards[asset_id]["duration_s"]
            max_gt = max((h["t_end"] for q in d["benchmark"]["queries"] for h in q.get("expected_hits", []) if h["asset_id"] == asset_id), default=0)
            # Container/annotation rounding tolerance; no time rescaling.
            # QVHighlights CardSet duration may use the nominal clip length.
            # Exact hashes establish identity; only GT extending past the
            # actual media invalidates temporal alignment.
            if max_gt > duration + 0.5:
                raise AlignmentError(f"Duration/GT mismatch {asset_id}: media={duration}, card={expected}, max_GT={max_gt}")
            duration_checks.append({"asset_id": asset_id, "media_duration": duration, "card_duration": expected, "max_gt": max_gt})
        result["alignment"]["duration_checks"] = duration_checks
        store = SentryStore(db_path=active / "db", backend=args.backend, model=args.model)
        if store.collection.count():
            raise RuntimeError("Fresh dataset index is not empty")
        for i, (asset_id, path) in enumerate(files.items(), 1):
            print(f"[{d['key']}] indexing video {i}/{len(files)} ({asset_id})", flush=True)
            chunks = chunk_video(path, args.chunk_duration, args.overlap)
            try:
                for chunk in chunks:
                    if args.skip_still and is_still_frame_chunk(chunk["chunk_path"]):
                        result["index"]["skipped_still"] += 1
                        continue
                    processed = preprocess_chunk(chunk["chunk_path"], target_resolution=480, target_fps=5)
                    vector = embedder.embed_video_chunk(processed)
                    if len(vector) != len(query_vec) or not all(math.isfinite(v) for v in vector):
                        raise RuntimeError("Invalid video embedding")
                    store.add_chunk(store.make_chunk_id(path, chunk["start_time"]), vector,
                                    {"source_file": path, "start_time": chunk["start_time"], "end_time": chunk["end_time"]})
                    result["index"]["chunks"] += 1
                    if not env.get("verified_at"):
                        smoke = store.search(query_vec, n_results=1)
                        if not smoke:
                            raise RuntimeError("Real vector-store smoke query failed")
                        env.update(verified_at=now(), video_dimensions=len(vector), vector_store_smoke=True,
                                   model=args.model, backend=args.backend)
                        write_json(args.workspace / "environment.json", env)
            finally:
                for parent in {Path(c["chunk_path"]).parent for c in chunks}:
                    if not parent.resolve().is_relative_to(active.resolve()):
                        raise RuntimeError("Chunk directory outside evaluation workspace")
                    shutil.rmtree(parent)
            write_json(result_path, result)
        if not result["index"]["chunks"]:
            raise RuntimeError("No video chunks indexed")
        for i, query in enumerate(d["benchmark"]["queries"], 1):
            start = time.monotonic()
            raw = search_footage(query["request"]["query"], store, n_results=10, dedupe_threshold=args.dedupe)
            hits = [{"asset_id": asset_map[r["source_file"]], "t_start": r["start_time"],
                     "t_end": r["end_time"], "score": r["similarity_score"]} for r in raw]
            # CLI --no-trim returns low-confidence matches too. Preserve them
            # for ranking; report threshold-based rejection separately.
            row = {"query_id": query["query_id"], "query": query["request"]["query"],
                   "case": query["case"], "grain": query["grain"], "intent": query.get("intent", []),
                   "difficulty": query.get("difficulty"), "expected_hits": query.get("expected_hits", []),
                   "latency_ms": round((time.monotonic() - start) * 1000, 2),
                   **score_query(query, hits)}
            row["threshold_false_positive"] = query["case"] == "no_result" and bool(hits) and hits[0]["score"] >= args.threshold
            result["rows"].append(row)
            if i % 25 == 0 or i == d["query_count"]:
                print(f"[{d['key']}] queries {i}/{d['query_count']}", flush=True)
                write_json(result_path, result)
        result.update(status="completed", completed_at=now(), summary=summarize(result["rows"]), buckets=bucket_rows(result["rows"]))
        negative = [r for r in result["rows"] if r["case"] == "no_result"]
        result["threshold_false_positive_rate"] = sum(r["threshold_false_positive"] for r in negative) / len(negative) if negative else None
        result["cases"] = dict(Counter(q["case"] for q in d["benchmark"]["queries"]))
        result["grains"] = dict(Counter(q["grain"] for q in d["benchmark"]["queries"]))
        result["elapsed_seconds"] = round(time.monotonic() - args.worker_start, 2)
        write_json(result_path, result)
        return 0
    except AlignmentError as exc:
        result.update(status="skipped_alignment", reason=str(exc), completed_at=now())
        write_json(result_path, result)
        print(f"[{d['key']}] skipped: {exc}", flush=True)
        return 0
    except Exception as exc:
        result.update(status="failed", error=str(exc), traceback=traceback.format_exc(), failed_at=now())
        write_json(result_path, result)
        traceback.print_exc()
        return 1


def percent(value):
    return "—" if value is None else f"{value * 100:.2f}%"


def render_report(workspace, state):
    results = state.get("datasets", [])
    env_path = workspace / "environment.json"
    env = read_json(env_path) if env_path.exists() else {}
    lines = ["# SentrySearch 四数据集精度评估", "", f"更新时间（UTC）：{now()}", "",
             f"任务状态：**{state['status']}**。", "",
             "环境：Python " + env.get("python", "待验证").split("\n")[0] + "; GPU: " + str(env.get("gpu", "待验证")) + ".",
             f"后端 / 模型：`{state['options']['backend']}` / `{state['options']['model']}`。",
             "", "| 数据集 | 状态 | 视频 | 查询 | R@1 | R@10 | nDCG@10 | mean tIoU | 无结果误报率 |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        s = r.get("summary", {})
        lines.append(f"| {r['key']} | {r['status']} | {r.get('video_count', '—')} | {r.get('query_count', '—')} | {percent(s.get('recall_at_1'))} | {percent(s.get('recall_at_k'))} | {percent(s.get('ndcg_at_k'))} | {s.get('mean_tiou', '—')} | {percent(s.get('no_result', {}).get('false_positive_rate'))} |")
    lines += ["", "## 评估口径", "",
              "直接调用 sphere-eval 的 `sphere_eval/eval/metrics.py`，不使用模拟检索。Top-10；moment 要求资产一致且 tIoU ≥ 0.5，asset 只要求资产一致。返回窗口与 GT 一对一匹配。R@K 分母为 min(K, GT 数)，mean tIoU 对全部 GT 求均值，未命中计零；no_result 不进入排名指标。各查询等权。",
              f"视频按项目默认 {state['options']['chunk_duration']} 秒切片、{state['options']['overlap']} 秒重叠，预处理为 480p / 5 FPS；本地模型再按项目规则采样（1 FPS，最多 32 帧）。静止跳过：{state['options']['skip_still']}；检索去重相似度：{state['options']['dedupe']}。不使用重排。",
              "CLI 在 `--no-trim` 模式仍返回低置信结果，因此主表保留这些返回值。无结果误报率按任何返回命中计误报；另列默认相似度阈值下的误报率供参考，未用 GT 调参。",
              "严格校验视频 SHA-256、大小及 GT 时间范围；CardSet 时长仅记录，不用作容器时长相等性断言。改名视频可通过内容哈希对齐。视频或标注不一致则整套跳过，失败不计零分。Card/GT 文本不会送入模型。",
              "每个数据集在独立子进程中执行。开始前删除本评估工作区 active 内全部旧视频、Chroma 索引及临时切片；结束后再次清理。JSON 逐查询证据和 Markdown 报告保留用于审核。",
              "这些发布包均标注 unreviewed，分数对应提供的候选 GT。固定 30 秒窗口对很短或很长的 GT 有定位上限；报告不将检索和精确边界定位混为一项。", ""]
    if env.get("test_validation"):
        tests = env["test_validation"]
        lines += ["环境验证：真实 CUDA 运算、FFmpeg 编解码、文字和视频推理及 Chroma 写入/查询通过；",
                  f"自动测试 {tests['tests']} 项，失败 {tests['failures']}，错误 {tests['errors']}。详见 `environment.json`、`validation.json`。", ""]
    for r in results:
        lines += [f"## {r['key']} — {r.get('release', '')}", "", f"状态：{r['status']}。"]
        if r.get("reason") or r.get("error"):
            lines += ["", "原因：" + str(r.get("reason") or r.get("error"))]
        alignment = r.get("alignment", {})
        lines += ["", f"哈希对齐：{len(alignment.get('matched', []))}/{r.get('video_count', '—')}；不一致 {len(alignment.get('problems', []))}；多余视频 {len(alignment.get('extra_videos', []))}。"]
        for p in alignment.get("problems", []):
            lines += [f"- `{p['asset_id']}`：期望 SHA-256 `{p['expected_sha256']}`；候选：`{json.dumps(p['filename_candidates'], ensure_ascii=False)}`。"]
        if r["status"] == "completed":
            lines += ["", f"索引切片 {r['index']['chunks']}；静止跳过 {r['index']['skipped_still']}；用时 {r.get('elapsed_seconds')} 秒。",
                      f"Case：`{r['cases']}`；Grain：`{r['grains']}`。",
                      f"相似度 ≥ {state['options']['threshold']} 的 no_result 误报率：{percent(r.get('threshold_false_positive_rate'))}。",
                      "", "| 粒度 | 查询 | R@1 | R@10 | nDCG@10 | mean tIoU |", "|---|---:|---:|---:|---:|---:|"]
            for b in r.get("buckets", []):
                if b["kind"] == "grain":
                    lines.append(f"| {b['label']} | {b['n']} | {percent(b['recall_at_1'])} | {percent(b['recall_at_k'])} | {percent(b['ndcg_at_k'])} | {b['mean_tiou']} |")
        lines += ["", f"逐查询结果及对齐证据：[results/{r['key']}.json](results/{r['key']}.json)", ""]
    lines += ["## 可复现与完成检查", "", f"运行指纹：`{state.get('fingerprint')}`。",
              "输入 JSON、项目源码、uv.lock、评分源码及 ZIP 文件身份记录在 completion.json。下次执行先检查完成标记、环境、报告和全部结果；全部通过则直接退出。源码、配置、数据或资源发生变化时重新测试。",
              "", "```powershell", ".\\.venv\\Scripts\\python.exe scripts/evaluate_sphere.py", "```", ""]
    (workspace / "report.md").write_text("\n".join(lines), encoding="utf-8")


def acquire_lock(workspace):
    """OS lock is released on exit/crash, so periodic runs cannot overlap."""
    handle = (workspace / ".run.lock").open("a+b")
    # Windows denies reading another process's locked byte. Querying file
    # size avoids that read and lets LK_NBLCK report contention normally.
    if handle.seek(0, os.SEEK_END) == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def completed(workspace, identity):
    path = workspace / "completion.json"
    if not path.exists():
        return False
    try:
        state = read_json(path)
        if state["status"] != "completed" or state["fingerprint"] != identity or not (workspace / "report.md").exists():
            return False
        env = read_json(workspace / "environment.json")
        current = installed_environment()
        if not env.get("vector_store_smoke") or env["packages"] != current["packages"]:
            return False
        if len(state["datasets"]) != len(DATASETS):
            return False
        for r in state["datasets"]:
            stored = read_json(workspace / "results" / (r["key"] + ".json"))
            if stored != r or r["status"] not in ("completed", "skipped_alignment"):
                return False
            if r["status"] == "completed" and len(r["rows"]) != r["query_count"]:
                return False
        return True
    except (OSError, KeyError, ValueError, importlib.metadata.PackageNotFoundError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sphere-root", type=Path, default=DEFAULT_SPHERE)
    parser.add_argument("--videos-root", type=Path, default=DEFAULT_VIDEOS)
    parser.add_argument("--workspace", type=Path, default=REPO / "evaluation")
    parser.add_argument("--backend", choices=("local", "gemini", "litellm", "qwen-cloud"), default="local")
    parser.add_argument("--model", default="qwen2b")
    parser.add_argument("--chunk-duration", type=int, default=30)
    parser.add_argument("--overlap", type=int, default=5)
    parser.add_argument("--dedupe", type=float, default=0.9)
    parser.add_argument("--threshold", type=float, default=0.41)
    parser.add_argument("--no-skip-still", dest="skip_still", action="store_false")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.workspace = args.workspace.resolve()
    if args.chunk_duration <= 0 or not 0 <= args.overlap < args.chunk_duration:
        parser.error("Require 0 <= overlap < chunk-duration")
    if not 0 < args.dedupe <= 1:
        parser.error("Require 0 < dedupe <= 1")
    if args.worker:
        args.worker_start = time.monotonic()
        return worker(args)
    args.workspace.mkdir(parents=True, exist_ok=True)
    lock = acquire_lock(args.workspace)
    if lock is None:
        print("已有评估进程在运行，本次周期检查不重复启动。", flush=True)
        return 0
    identity, inputs = fingerprint(args)
    if not args.force and completed(args.workspace, identity):
        print("两个目标已经完成，输入与环境未变化；跳过重复安装和评估。", flush=True)
        print(args.workspace / "report.md")
        return 0
    state = {"status": "preflight" if args.preflight_only else "running", "started_at": now(),
             "fingerprint": identity, "inputs": inputs, "options": inputs["options"], "datasets": []}
    for spec in DATASETS:
        cleanup = safe_clear(args.workspace / "active", args.workspace)
        d = load_dataset(spec, args.sphere_root, args.videos_root)
        print(f"[{d['key']}] SHA-256 alignment ({d['video_count']} videos)", flush=True)
        alignment = align_archive(d)
        d.update(alignment=alignment, cleanup=cleanup)
        base = {k: d[k] for k in ("key", "release", "archive", "query_count", "video_count", "alignment", "cleanup")}
        result_path = args.workspace / "results" / (d["key"] + ".json")
        if not alignment["aligned"]:
            result = {**base, "status": "skipped_alignment", "reason": "提供的 ZIP 与 CardSet 媒体清单不能完全按 SHA-256 和大小一对一对齐，详见下方和 JSON。"}
            write_json(result_path, result)
        elif args.preflight_only:
            result = {**base, "status": "aligned_only"}
            write_json(result_path, result)
        else:
            write_json(args.workspace / "active" / "input.json", d)
            state["datasets"].append({**base, "status": "running"})
            write_json(args.workspace / "completion.json", state)
            render_report(args.workspace, state)
            state["datasets"].pop()
            command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--workspace", str(args.workspace),
                       "--sphere-root", str(args.sphere_root), "--backend", args.backend, "--model", args.model,
                       "--chunk-duration", str(args.chunk_duration), "--overlap", str(args.overlap),
                       "--dedupe", str(args.dedupe), "--threshold", str(args.threshold)]
            if not args.skip_still:
                command.append("--no-skip-still")
            code = subprocess.run(command, env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1", "TOKENIZERS_PARALLELISM": "false"}).returncode
            result = read_json(result_path) if result_path.exists() else {**base, "status": "failed", "error": f"Worker exited {code} before writing results"}
            if code != 0 and result["status"] == "running":
                result.update(status="failed", error=f"Worker exited {code}")
                write_json(result_path, result)
        safe_clear(args.workspace / "active", args.workspace)
        state["datasets"].append(result)
        write_json(args.workspace / "completion.json", state)
        render_report(args.workspace, state)
    if args.preflight_only:
        state["status"] = "preflight"
    elif all(r["status"] in ("completed", "skipped_alignment") for r in state["datasets"]) and (args.workspace / "environment.json").exists():
        state.update(status="completed", completed_at=now())
    else:
        state["status"] = "incomplete"
    write_json(args.workspace / "completion.json", state)
    render_report(args.workspace, state)
    print(f"Report: {args.workspace / 'report.md'} ({state['status']})", flush=True)
    return 0 if state["status"] in ("completed", "preflight") else 1


if __name__ == "__main__":
    raise SystemExit(main())
