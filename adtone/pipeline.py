"""Process collected ads: find their images, embed and score them, keep the derived rows.

    python -m adtone.pipeline --max-ads 600 --budget-min 100

Order matters. An ad leaves the repository a year after it last ran, and with it the
only copy of its images this project can reach. So ads are processed in order of how
soon they will disappear: earliest stop date first, still-running ads last.

An ad whose images have rows for the current rubric and embedder is done. One that
lacks either (a new rubric version, say) is refetched while it is still in the
repository, which is the only way a new instrument can ever see old ads.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime, timezone
from typing import Callable

from . import config, store
from .media import FetchedImage, MediaError, download, jpeg_for_model
from .score import ScoreError

log = logging.getLogger("adtone.pipeline")
MAX_ATTEMPTS = 3


def _expiry_key(ad: dict, today: date) -> str:
    stop = ad.get("stop")
    return stop[:10] if stop else today.isoformat() + "~"   # running ads sort after every stopped one


def pending(ads: store.ShardedTable, media: store.ShardedTable, scored: set[tuple[str, str]], vec_shas: set[str],
            instrument: str, resolver_name: str, today: date) -> list[dict]:
    out = []
    for ad in ads.rows.values():
        m = media.get(ad["ad_id"])
        if m is None:
            out.append(ad)
        elif m["status"] == "error":
            if m.get("attempts", 0) < MAX_ATTEMPTS:
                out.append(ad)
        elif m["status"] in ("no_candidates", "no_usable_image"):
            if m.get("resolver") != resolver_name:
                out.append(ad)
        elif m["status"] == "resolved":
            shas = [i["sha"] for i in m.get("images", [])]
            if any((s, instrument) not in scored or s not in vec_shas for s in shas):
                out.append(ad)
    out.sort(key=lambda a: (_expiry_key(a, today), a["ad_id"]))
    return out


def process(ads: store.ShardedTable, media: store.ShardedTable, obs_path, vectors, resolver,
            fetch: Callable[[list[str]], list[FetchedImage]], embedder, scorer, rid: str,
            max_ads: int = 600, budget_s: float = 6000, today: date | None = None, clock=time.monotonic) -> dict:
    today = today or date.today()
    month = today.strftime("%Y-%m")
    instrument = scorer.instrument
    scored = {(r["sha"], r["instrument"]) for r in store.read_jsonl(obs_path)}
    todo = pending(ads, media, scored, set(vectors.vecs), instrument, resolver.name, today)
    t0 = clock()
    counts = {"pending_at_start": len(todo), "processed": 0, "resolved": 0, "no_candidates": 0,
              "no_usable_image": 0, "error": 0, "images": 0, "embedded": 0, "scored_ok": 0, "scored_invalid": 0}
    new_obs: list[dict] = []
    api_failure: ScoreError | None = None
    for ad in todo[:max_ads]:
        if clock() - t0 > budget_s:
            log.info("time budget reached after %d ads", counts["processed"])
            break
        prev = media.get(ad["ad_id"]) or {}
        row = {"ad_id": ad["ad_id"], "house_id": ad["house_id"], "resolver": resolver.name,
               "processed_run": rid, "processed_at": store.utc_now(), "attempts": prev.get("attempts", 0) + 1}
        try:
            urls = resolver.resolve(ad["ad_id"])
            row["n_candidates"] = len(urls)
            images = fetch(urls) if urls else []
            if not urls:
                row["status"] = "no_candidates"
            elif not images:
                row["status"] = "no_usable_image"
            else:
                for f in images:
                    counts["images"] += 1
                    if f.sha not in vectors:
                        vectors.add(f.sha, embedder.embed(f.image), month)
                        counts["embedded"] += 1
                    if (f.sha, instrument) not in scored:
                        ob = {"sha": f.sha, "instrument": instrument, "rubric_version": scorer.rubric.version,
                              "run_id": rid, "scored_at": store.utc_now()}
                        try:
                            ob["output"] = scorer.score(jpeg_for_model(f.image))
                            ob["status"] = "ok"
                            counts["scored_ok"] += 1
                        except ScoreError as e:
                            if str(e).startswith("API:"):
                                raise
                            ob["status"], ob["error"] = "invalid", str(e)[:300]
                            counts["scored_invalid"] += 1
                        new_obs.append(ob)
                        scored.add((f.sha, instrument))
                    f.image.close()
                row["status"] = "resolved"
                row["images"] = [{"sha": f.sha, "phash": f.phash, "w": f.w, "h": f.h} for f in images]
        except ScoreError as e:
            # The scoring API itself failed (auth, outage). Stop here: recording an error
            # against every remaining ad would burn their retry attempts for nothing.
            api_failure = e
            break
        except MediaError as e:
            row["status"], row["error"] = "error", str(e)[:300]
        counts[row["status"]] += 1
        counts["processed"] += 1
        media.upsert(row, shard=month)
    store.append_jsonl(obs_path, new_obs)
    counts["media_shards"] = media.save()
    counts["vector_shards"] = vectors.save()
    counts["pending_after"] = len(pending(ads, media, scored, set(vectors.vecs), instrument, resolver.name, today))
    counts["elapsed_s"] = round(clock() - t0, 1)
    if api_failure is not None:
        counts["stopped_on"] = str(api_failure)
    return counts


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(prog="adtone.pipeline")
    ap.add_argument("--max-ads", type=int, default=600)
    ap.add_argument("--budget-min", type=float, default=100)
    ap.add_argument("--resolver", default=os.environ.get("ADTONE_RESOLVER", "auto"), choices=["auto", "static", "browser"])
    args = ap.parse_args(argv)

    import requests
    from .embed import OpenClipEmbedder, VectorStore
    from .media import AutoResolver, BrowserResolver, StaticResolver
    from .score import ClaudeScorer, load_rubric

    token = os.environ.get("META_AD_LIBRARY_TOKEN", "")
    if not token:
        print("::error::META_AD_LIBRARY_TOKEN is not set")
        return 3
    rubric = load_rubric()
    scorer = ClaudeScorer(rubric)
    embedder = OpenClipEmbedder()
    vectors = VectorStore(embedder.tag)
    session = requests.Session()
    resolver = {"browser": lambda: BrowserResolver(token), "static": lambda: StaticResolver(token, session),
                "auto": lambda: AutoResolver(token, session)}[args.resolver]()
    ads = store.ShardedTable(config.ADS_DIR, "ad_id")
    media = store.ShardedTable(config.MEDIA_DIR, "ad_id")
    rid = config.run_id()
    try:
        counts = process(ads, media, config.OBS_DIR / f"{rubric.version}.jsonl", vectors, resolver,
                         lambda urls: download(urls, session), embedder, scorer, rid,
                         max_ads=args.max_ads, budget_s=args.budget_min * 60)
    finally:
        resolver.close()
    summary = {"run_id": rid, "finished_at": store.utc_now(), "instrument": scorer.instrument,
               "embedder": embedder.tag, "resolver": resolver.name, "resolver_used": getattr(resolver, "used", None),
               "claude_calls": scorer.calls, **counts}
    store.append_jsonl(config.PROV_DIR / "process.jsonl", [summary])
    state = store.read_state(config.STATE_DIR / "process.json")
    state.update({"last_run": rid, "last_finished": summary["finished_at"], "pending_after": counts["pending_after"],
                  "instrument": scorer.instrument, "embedder": embedder.tag})
    store.write_state(config.STATE_DIR / "process.json", state)
    print(f"processed {counts['processed']} ads: {counts['resolved']} resolved, {counts['no_candidates']} without "
          f"candidates, {counts['no_usable_image']} without a usable image, {counts['error']} errors; "
          f"{counts['scored_ok']} images scored, {counts['pending_after']} ads still pending")
    if counts.get("stopped_on"):
        print(f"::error::scoring API failed, run stopped early with progress saved: {counts['stopped_on']}")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
