"""Optional parallel, hash-verified downloads for slow single HTTP streams.

Downloads exactly the locked Windows torch wheel or official Qwen weights.
Partial ranges are resumable; never install or expose unsigned/unverified data.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        while b := f.read(4 * 1024 * 1024):
            h.update(b)
    return h.hexdigest()


def download(url, target, size, sha, workers=16, block=8 * 1024 * 1024):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size == size and digest(target) == sha:
        print(f"Verified cached {target.name}", flush=True)
        return
    partial = target.with_suffix(target.suffix + ".parallel-partial")
    marker = target.with_suffix(target.suffix + ".ranges.json")
    done = set(json.loads(marker.read_text())) if marker.exists() and partial.exists() else set()
    if not partial.exists():
        with partial.open("wb") as f:
            f.truncate(size)
    lock = threading.Lock()
    count = (size + block - 1) // block
    started = time.monotonic()
    last_print = [0.0]

    def part(index):
        start, end = index * block, min(size, (index + 1) * block) - 1
        for attempt in range(5):
            try:
                # Separate cache identities avoid CDN caches reusing one Range.
                request_url = url + ("&" if "?" in url else "?") + f"range_start={start}"
                request = urllib.request.Request(request_url, headers={"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"})
                with urllib.request.urlopen(request, timeout=60) as response:
                    expected = f"bytes {start}-{end}/{size}"
                    if response.status != 206 or response.headers.get("Content-Range") != expected:
                        raise RuntimeError("Server returned an unexpected byte range")
                    data = response.read(end - start + 2)
                if len(data) != end - start + 1:
                    raise RuntimeError("Incomplete byte range")
                with partial.open("r+b") as f:
                    f.seek(start)
                    f.write(data)
                with lock:
                    done.add(index)
                    temp = marker.with_suffix(".tmp")
                    temp.write_text(json.dumps(sorted(done)))
                    temp.replace(marker)
                    elapsed = time.monotonic() - started
                    if elapsed - last_print[0] >= 20 or len(done) == count:
                        last_print[0] = elapsed
                        print(f"{target.name}: {len(done)}/{count} ranges ({100 * len(done) / count:.1f}%), elapsed {elapsed:.0f}s", flush=True)
                return
            except Exception as exc:
                if attempt == 4:
                    raise RuntimeError(f"Range {index} failed: {type(exc).__name__}") from exc
                time.sleep(min(2 ** attempt, 8))

    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        list(pool.map(part, [i for i in range(count) if i not in done]))
    if digest(partial) != sha:
        raise RuntimeError("Downloaded file SHA-256 differs from trusted metadata")
    partial.replace(target)
    marker.unlink(missing_ok=True)
    print(f"Verified {target.name}: {sha}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("what", choices=("torch", "model"))
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--model-source", choices=("huggingface", "modelscope"), default="modelscope")
    parser.add_argument("--torch-source", choices=("pytorch", "sjtu"), default="sjtu")
    parser.add_argument("--model-size", choices=("2B", "8B"), default="2B")
    args = parser.parse_args()
    if args.what == "torch":
        # Read the exact URL and digest from this repository's locked dependency.
        import tomllib
        lock = tomllib.loads((ROOT / "uv.lock").read_text())
        package = next(p for p in lock["package"] if p["name"] == "torch" and p["version"] == "2.6.0+cu124")
        wheel = next(w for w in package["wheels"] if "cp312-cp312-win_amd64" in w["url"])
        with urllib.request.urlopen(urllib.request.Request(wheel["url"], headers={"Range": "bytes=0-0"}), timeout=30) as response:
            size = int(response.headers["Content-Range"].split("/")[-1])
        target = ROOT / "evaluation" / "download-cache" / "torch-2.6.0+cu124-cp312-cp312-win_amd64.whl"
        url = wheel["url"]
        if args.torch_source == "sjtu":
            url = "https://mirror.sjtu.edu.cn/pytorch-wheels/cu124/" + url.rsplit("/", 1)[1]
        download(url, target, size, wheel["hash"].split(":")[1], args.workers)
        print(target)
    else:
        from huggingface_hub import hf_hub_download, get_hf_file_metadata, hf_hub_url, constants, HfApi, snapshot_download
        repo = f"Qwen/Qwen3-VL-Embedding-{args.model_size}"
        config_path = Path(hf_hub_download(repo, "config.json"))
        revision = config_path.parent.name
        snapshot_download(repo, revision=revision, allow_patterns=["*.json", "*.txt", "*.model", "*.jinja", "*.tiktoken"], max_workers=4)
        files = HfApi().list_repo_files(repo, revision=revision)
        for filename in sorted(f for f in files if f.endswith(".safetensors")):
            metadata = get_hf_file_metadata(hf_hub_url(repo, filename, revision=revision))
            cache = Path(constants.HF_HUB_CACHE) / ("models--" + repo.replace("/", "--"))
            target = cache / "blobs" / metadata.etag
            # The resolve URL redirects normally and refreshes signed CDN URLs.
            url = hf_hub_url(repo, filename, revision=metadata.commit_hash)
            if args.model_source == "modelscope":
                # Official Qwen mirror; the digest remains HF's commit-specific
                # SHA-256, so a different revision cannot be silently accepted.
                url = f"https://modelscope.cn/models/{repo}/resolve/master/{filename}"
            download(url, target, metadata.size, metadata.etag, args.workers)
            # Build the canonical snapshot pointer (Windows may copy the blob).
            hf_hub_download(repo, filename, revision=metadata.commit_hash)
        print(f"Official {args.model_size} weights downloaded and verified", flush=True)


if __name__ == "__main__":
    main()
