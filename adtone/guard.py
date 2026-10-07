"""Refuse to persist anything a workflow should not write.

    python -m adtone.guard <workflow> <path> [<path> ...]

The repo is public for the Actions minutes. So: no image or video file anywhere, each
workflow writes only the files it owns, nothing over 50 MB, and no file containing
the access token. Exit 1 lists every violation.
"""
from __future__ import annotations

import fnmatch
import os
import sys
from pathlib import Path

ALLOW = {
    "collect": ["data/ads/*.jsonl", "data/state/collect.json", "data/provenance/collect.jsonl",
                "data/registry/page_candidates.json"],
    "process": ["data/media/*.jsonl", "data/obs/*.jsonl", "data/vectors/*/*.npz", "data/state/process.json",
                "data/provenance/process.jsonl"],
    "confirm": ["registry/houses.yml"],
    "backcat": ["data/backcat/campaigns/*.jsonl", "data/backcat/media/*.jsonl", "data/backcat/obs/*.jsonl",
                "data/backcat/vectors/*/*.npz", "data/state/backcat.json", "data/provenance/backcat.jsonl"],
    "attention": ["data/attention/*.jsonl", "data/state/attention.json", "data/provenance/attention.jsonl",
                  "data/wikiviews/*/*.jsonl", "data/state/wikiviews.json", "data/provenance/wikiviews.jsonl",
                  "data/wikidata/houses.json", "data/state/wikidata.json", "data/provenance/wikidata.jsonl"],
    "press": ["data/press/*.jsonl", "data/state/press.json", "data/provenance/press.jsonl"],
    "homepages": ["data/homepages/captures/*.jsonl", "data/homepages/index/*.json", "data/homepages/obs/*.jsonl",
                  "data/homepages/vectors/*/*.npz", "data/state/homepages.json", "data/provenance/homepages.jsonl"],
    "youtube": ["data/youtube/*.jsonl", "data/youtube/videos/*.jsonl", "data/youtube/obs/*.jsonl",
                "data/youtube/vectors/*/*.npz", "data/state/youtube.json", "data/provenance/youtube.jsonl"],
    "tiktok": ["data/tiktok/ads/*.jsonl", "data/tiktok/obs/*.jsonl", "data/tiktok/vectors/*/*.npz",
               "data/state/tiktok.json", "data/provenance/tiktok.jsonl"],
    "reader": ["data/state/reader.json", "data/provenance/reader.jsonl"],
    "statements": ["data/statements/*.jsonl", "data/state/statements.json", "data/provenance/statements.jsonl"],
    "bakeoff": ["data/bakeoff/*.json", "data/bakeoff/*.jsonl", "data/bakeoff/readings/*.jsonl",
                "data/bakeoff/vectors/*.npz", "data/bakeoff/positions/*.jsonl", "data/results/bakeoff.json",
                "data/provenance/bakeoff.jsonl"],
    "analyse": ["data/results/summary.json", "data/results/report.md", "data/results/forward-*.json",
                "data/results/calibration.json", "data/results/crews.json", "data/results/success.json",
                "data/results/family.json", "data/results/runway.json", "data/results/runway_events.csv",
                "data/results/character.json", "data/results/alignment.json", "data/results/readings.json",
                "data/provenance/analyse.jsonl",
                "data/human_check/sample-*.csv"],
}
DENY_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".avif",
            ".mp4", ".mov", ".webm", ".m4v"}
MAX_BYTES = 50 * 1024 * 1024


def violations(workflow: str, paths: list[str], root: Path = Path("."), secrets: list[str] | None = None) -> list[str]:
    if workflow not in ALLOW:
        return [f"unknown workflow {workflow!r}"]
    secrets = [s for s in (secrets or []) if s and len(s) >= 12]
    out = []
    for p in paths:
        p = p.strip().strip('"')
        if not p:
            continue
        if Path(p).suffix.lower() in DENY_EXT:
            out.append(f"{p}: image or video files never reach a commit")
            continue
        if not any(fnmatch.fnmatch(p, pat) for pat in ALLOW[workflow]):
            out.append(f"{p}: not owned by the {workflow} workflow")
            continue
        f = root / p
        if f.exists():
            if f.stat().st_size > MAX_BYTES:
                out.append(f"{p}: larger than {MAX_BYTES // 2**20} MB")
            elif secrets and f.suffix in (".json", ".jsonl", ".md", ".csv"):
                text = f.read_text(encoding="utf-8", errors="ignore")
                if any(s in text for s in secrets):
                    out.append(f"{p}: contains a secret")
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 1:
        print("usage: python -m adtone.guard <workflow> <path>...")
        return 2
    secrets = [os.environ.get(k, "") for k in ("META_AD_LIBRARY_TOKEN", "ANTHROPIC_API_KEY", "META_APP_SECRET",
                                                "YOUTUBE_API_KEY", "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET")]
    v = violations(argv[0], argv[1:], secrets=secrets)
    for line in v:
        print(f"::error::{line}")
    return 1 if v else 0


if __name__ == "__main__":
    sys.exit(main())
