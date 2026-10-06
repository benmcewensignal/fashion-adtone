"""Independent completeness checks, run after each workflow's main step.

    python -m adtone.probe collect --run <run_id> --mode backfill
    python -m adtone.probe process --run <run_id>

A step that counts its own output is the same self-report that once marked a
signal-sonic job done after 300 of 18,617 records. These probes count what is on disk.
"""
from __future__ import annotations

import argparse
import sys

from . import config, registry, store

REQUIRED = ("ad_id", "house_id", "page_id", "first_collected", "last_collected", "copy")


def probe_collect(run: str, mode: str) -> list[str]:
    errs = []
    rows, leaks = 0, 0
    this_run = 0
    for p in sorted(config.ADS_DIR.glob("*.jsonl")):
        text = p.read_text(encoding="utf-8")
        if "access_token" in text:
            leaks += 1
        for r in store.read_jsonl(p):
            rows += 1
            missing = [k for k in REQUIRED if k not in r]
            if missing:
                errs.append(f"{p.name}: ad {r.get('ad_id')} missing {missing}")
                break
            if r["last_collected"] == run:
                this_run += 1
    if leaks:
        errs.append(f"{leaks} ads shard(s) contain 'access_token'")
    resolved = registry.load().resolved
    if mode == "backfill" and resolved and this_run == 0:
        errs.append(f"backfill touched no ads for {len(resolved)} resolved houses")
    print(f"probe collect: {rows} ads on disk, {this_run} touched by run {run}")
    return errs


def probe_process(run: str, instrument: str, embed_tag: str) -> list[str]:
    from .embed import VectorStore
    errs = []
    media = [r for p in sorted(config.MEDIA_DIR.glob("*.jsonl")) for r in store.read_jsonl(p)
             if r.get("processed_run") == run]
    if not media:
        print(f"probe process: run {run} processed no ads")
        return errs
    status = {}
    for r in media:
        status[r["status"]] = status.get(r["status"], 0) + 1
    shas = {i["sha"] for r in media if r["status"] == "resolved" for i in r.get("images", [])}
    rubric_version = instrument.split("@", 1)[0]
    scored = {r["sha"] for r in store.read_jsonl(config.OBS_DIR / f"{rubric_version}.jsonl")
              if r["instrument"] == instrument}
    vecs = VectorStore(embed_tag).vecs
    no_obs = [s for s in shas if s not in scored]
    no_vec = [s for s in shas if s not in vecs]
    print(f"probe process: {len(media)} ads this run {status}; {len(shas)} images, "
          f"{len(shas) - len(no_obs)} with rows for {instrument}, {len(shas) - len(no_vec)} with vectors")
    if len(media) >= 20 and status.get("resolved", 0) == 0:
        errs.append(f"none of {len(media)} ads resolved to an image. If the resolver was auto, the browser "
                    f"fallback found nothing either and the render page itself needs inspecting; if it was "
                    f"static, set ADTONE_RESOLVER=auto or browser")
    if shas and len(no_obs) > 0.1 * len(shas):
        errs.append(f"{len(no_obs)} of {len(shas)} images from this run have no observation row")
    if shas and len(no_vec) > 0.1 * len(shas):
        errs.append(f"{len(no_vec)} of {len(shas)} images from this run have no vector")
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.probe")
    ap.add_argument("what", choices=["collect", "process"])
    ap.add_argument("--run", required=True)
    ap.add_argument("--mode", default="incremental")
    ap.add_argument("--instrument", default=config.instrument())
    ap.add_argument("--embedder", default=config.EMBED_TAG)
    a = ap.parse_args(argv)
    errs = probe_collect(a.run, a.mode) if a.what == "collect" else probe_process(a.run, a.instrument, a.embedder)
    for e in errs:
        print(f"::error::{e}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
