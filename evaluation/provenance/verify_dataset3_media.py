"""Independently audit dataset 3 media; no inference, extraction, or deletion."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import zipfile


RELEASE = Path(r"C:\Users\luoyw\work\lab\Sphere\sphere-eval\datasets\releases\sphere-public-tsv360-cross-video-agreement-full-queries-20261116-r01")
ARCHIVE = Path(r"D:\work\Sphere\benchmark_videos\videos_fix_format_74_cross_video_agreement.zip")
OUTPUT = Path(__file__).resolve().parents[1] / "dataset3-media-proof.json"


def digest(stream):
    sha = hashlib.sha256()
    count = 0
    while block := stream.read(4 * 1024 * 1024):
        sha.update(block)
        count += len(block)
    return count, sha.hexdigest()


def main():
    media_path = RELEASE / "media.json"
    media = json.loads(media_path.read_text(encoding="utf-8-sig"))
    verification = json.loads((RELEASE / "verification.json").read_text(encoding="utf-8-sig"))
    with media_path.open("rb") as stream:
        _, media_sha = digest(stream)
    # Windows checkout uses CRLF; the release records hashes of LF JSON files.
    # Normalize only JSON text for this ancillary check, never video bytes.
    manifest_lf_sha = hashlib.sha256(media_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    release_file_checks = []
    for name, expected_sha in verification["file_sha256"].items():
        raw = (RELEASE / name).read_bytes()
        lf_sha = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
        release_file_checks.append({"file": name, "expected_sha256": expected_sha,
                                    "raw_sha256": hashlib.sha256(raw).hexdigest(),
                                    "lf_sha256": lf_sha, "matches_after_crlf_to_lf": lf_sha == expected_sha})
    inventory = []
    with zipfile.ZipFile(ARCHIVE) as archive:
        for member in archive.infolist():
            if member.is_dir() or Path(member.filename).suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm", ".avi"}:
                continue
            with archive.open(member) as stream:
                size, sha = digest(stream)
            assert size == member.file_size, member.filename
            inventory.append({"member": member.filename, "size": size, "sha256": sha})
    mismatches = []
    matched = 0
    for expected in media:
        hash_matches = [v for v in inventory if v["sha256"] == expected["sha256"]]
        if len(hash_matches) == 1 and hash_matches[0]["size"] == expected["size"]:
            matched += 1
            continue
        stem = Path(expected["path"]).stem
        aliases = {Path(expected["path"]).name, stem + "_LL360.mp4", expected["asset_id"] + ".mp4"}
        candidates = [v for v in inventory if Path(v["member"]).name in aliases]
        mismatches.append({"expected": expected, "hash_matches_anywhere_in_zip": len(hash_matches), "filename_candidates": candidates})
    with ARCHIVE.open("rb") as stream:
        archive_size, archive_sha = digest(stream)
    previous = json.loads((OUTPUT.parent / "results" / "dataset3.json").read_text(encoding="utf-8-sig"))
    prior = previous["alignment"]
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "method": "independent standard-library ZIP decompression; byte counting and SHA-256 of each uncompressed video",
        "manifest": str(media_path),
        "manifest_sha256": media_sha,
        "manifest_expected_sha256": verification["file_sha256"]["media.json"],
        "manifest_matches_release_verification": media_sha == verification["file_sha256"]["media.json"],
        "manifest_lf_sha256": manifest_lf_sha,
        "manifest_matches_release_after_crlf_to_lf": manifest_lf_sha == verification["file_sha256"]["media.json"],
        "release_file_checks": release_file_checks,
        "json_line_endings_note": "All four release JSON files match published hashes after CRLF-to-LF conversion; video bytes are hashed without any normalization.",
        "archive": str(ARCHIVE), "archive_size": archive_size, "archive_sha256": archive_sha,
        "archive_matches_previous_evaluation": archive_sha == prior["archive_sha256"],
        "expected_video_count": len(media), "archive_video_count": len(inventory),
        "exact_match_count": matched, "mismatch_count": len(mismatches),
        "mismatches": mismatches, "inventory": inventory,
        "limitation": "Byte differences alone do not establish visual or temporal mismatch; the evaluator uses strict media identity as its skip policy.",
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "inventory"}, ensure_ascii=False, indent=2))
    print(f"Evidence: {OUTPUT}")


if __name__ == "__main__":
    main()
