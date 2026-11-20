"""Run only repaired dataset 3, proving all other evaluation inputs unchanged."""
from argparse import Namespace
import copy
from pathlib import Path
import os
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import evaluate_sphere as runner


def main():
    workspace = REPO / "evaluation"
    lock = runner.acquire_lock(workspace)
    if lock is None:
        raise RuntimeError("Another evaluation is running")
    old = runner.read_json(workspace / "completion.json")
    args = Namespace(sphere_root=runner.DEFAULT_SPHERE, videos_root=runner.DEFAULT_VIDEOS,
                     workspace=workspace, **old["options"])
    identity, inputs = runner.fingerprint(args)
    if runner.completed(workspace, identity):
        print("All datasets already completed with current inputs", flush=True)
        return 0
    spec = next(s for s in runner.DATASETS if s[0] == "dataset3")
    target_release = args.sphere_root / "datasets/releases" / spec[1]
    changed = {p for p, checksum in inputs["files"].items() if old["inputs"]["files"].get(p) != checksum}
    allowed = {str(target_release / name) for name in ("media.json", "card-set.json", "benchmark.json")}
    assert changed == allowed, changed
    assert inputs["archives"] == old["inputs"]["archives"]
    assert inputs["options"] == old["inputs"]["options"]
    assert runner.installed_environment()["packages"] == runner.read_json(workspace / "environment.json")["packages"]
    for previous in old["datasets"]:
        assert previous == runner.read_json(workspace / "results" / (previous["key"] + ".json"))
        if previous["key"] != "dataset3":
            assert previous["status"] == "completed"
    cleanup = runner.safe_clear(workspace / "active", workspace)
    dataset = runner.load_dataset(spec, args.sphere_root, args.videos_root)
    print("[dataset3] validating repaired media identities", flush=True)
    alignment = runner.align_archive(dataset)
    assert alignment["aligned"] and len(alignment["matched"]) == 74
    dataset.update(alignment=alignment, cleanup=cleanup)
    state = copy.deepcopy(old)
    state.update(status="running", fingerprint=identity, inputs=inputs, resumed_at=runner.now())
    state.pop("completed_at", None)
    state["provenance"]["media_correction"] = str(target_release / "media-correction.json")
    state["provenance"]["reused_dataset_keys"] = ["dataset1", "dataset2", "dataset4"]
    state["provenance"]["dataset_source_sha256"]["dataset3"] = runner.file_sha(Path(runner.__file__))
    base = {k: dataset[k] for k in ("key", "release", "archive", "query_count", "video_count", "alignment", "cleanup")}
    position = next(i for i, d in enumerate(state["datasets"]) if d["key"] == "dataset3")
    state["datasets"][position] = {**base, "status": "running"}
    runner.write_json(workspace / "results/dataset3.json", state["datasets"][position])
    runner.write_json(workspace / "completion.json", state)
    runner.render_report(workspace, state)
    runner.write_json(workspace / "active/input.json", dataset)
    command = [sys.executable, str(Path(runner.__file__)), "--worker", "--workspace", str(workspace),
               "--sphere-root", str(args.sphere_root), "--backend", args.backend, "--model", args.model,
               "--chunk-duration", str(args.chunk_duration), "--overlap", str(args.overlap),
               "--dedupe", str(args.dedupe), "--threshold", str(args.threshold)]
    if not args.skip_still:
        command.append("--no-skip-still")
    code = subprocess.run(command, env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1", "TOKENIZERS_PARALLELISM": "false"}).returncode
    result = runner.read_json(workspace / "results/dataset3.json")
    if code != 0 and result["status"] == "running":
        result.update(status="failed", error=f"Worker exited {code}")
        runner.write_json(workspace / "results/dataset3.json", result)
    runner.safe_clear(workspace / "active", workspace)
    state["datasets"][position] = result
    state["status"] = "completed" if code == 0 and result["status"] == "completed" else "incomplete"
    if state["status"] == "completed":
        state["completed_at"] = runner.now()
    runner.write_json(workspace / "completion.json", state)
    runner.render_report(workspace, state)
    print(f"Final status: {state['status']}; {workspace / 'report.md'}", flush=True)
    return 0 if state["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
