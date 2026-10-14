"""Focal's own record of each house's homepage, captured once a day from the house's own site.

    python -m adtone.live collect --run <id>
    python -m adtone.live probe --run <id>

The homepage history comes from the public web archive, whose captures of ten houses' sites thinned or
stopped in 2026: nothing for Louis Vuitton since October 2024, four pictures of Chanel since October 2025.
This keeps a record of Focal's own from now on. Once a day each house's homepage is asked for as an
identified, polite visitor (the crawler's own user agent, robots.txt honoured for the page and for every
picture, a pause between requests). A site that declines (robots.txt, a refusal, a challenge page) is
recorded as declining and left there: nothing is done to get round it.

Pictures are held in memory, fingerprinted and read like the archive's, then let go. What is kept is
derived: the page's address, each picture's hash, perceptual hash and size, the reader's answers and the
fingerprint, under data/homepages/live/. A live capture is a different sensor from the archive (another
crawler, another hour, sometimes another country's page), so the two are never pooled in one comparison.
The forward template reads the archive only; a later version can read this record once it holds a year.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests

from . import config, homepages, registry, store
from .backcat import Crawler
from .media import MediaError, download, jpeg_for_model
from .score import ScoreError, score_batch

KEEP = homepages.KEEP_PER_CAPTURE
CANDIDATES = homepages.IMAGE_CANDIDATES
PAUSE_S = 2.0
TIMEOUT_S = 20.0
BUDGET_S = 20 * 60
PICTURE_PAUSE_S = 0.5
REFUSED = (401, 403, 405, 406, 429, 451)
BATCH = homepages.BATCH
NO_TS = "20000101000000"     # page_images wraps addresses for the archive; the wrapping is taken off again

# what a page that is a gate rather than a homepage says of itself
WALL = re.compile(r"access denied|pardon our interruption|are you a robot|verify you are human|captcha|"
                  r"_incapsula_resource|request unsuccessful|cf-chl|challenge-platform|px-captcha|datadome|"
                  r"perimeterx|enable javascript and cookies|attention required", re.I)


def paths() -> dict[str, Path]:
    b = config.DATA / "homepages" / "live"
    return {"captures": b / "captures", "obs": b / "obs", "vectors": b / "vectors",
            "state": config.STATE_DIR / "live.json", "prov": config.PROV_DIR / "live.jsonl"}


def homepage(domain: str) -> str:
    d = domain.strip().lower()
    return f"https://{d}/" if d.startswith("www.") or d.count(".") >= 2 else f"https://www.{d}/"


def _live(urls: list[str]) -> list[str]:
    return [homepages._original(u) for u in urls]


def _wall(r) -> bool:
    return bool(WALL.search((r.text or "")[:20000]))


def read_page(c: Crawler, url: str) -> tuple[dict, list]:
    """One house's homepage today: the page, its pictures, and one step onwards (a refresh, a script
    redirect, a country chooser) when the page itself has none. Returns (row, pictures)."""
    if not c.allowed(url):
        return {"status": "declined", "why": "robots.txt", "page": url}, []
    r = c.get(url, retries=0)
    ctype = (getattr(r, "headers", None) or {}).get("Content-Type", "text/html")
    if r.status_code != 200 or "html" not in ctype:
        return {"status": "declined" if r.status_code in REFUSED else "error", "why": f"HTTP {r.status_code}", "page": url}, []
    if _wall(r):
        return {"status": "declined", "why": "a challenge page instead of the homepage", "page": url}, []
    page = getattr(r, "url", "") or url
    for attempt in range(2):
        cands = [u for u in _live(homepages.page_images(r.text, page, NO_TS, cap=CANDIDATES + 4)) if c.allowed(u)]
        got = download(cands, homepages._Watch(c.s, PICTURE_PAUSE_S if c.pause else 0.0), keep=KEEP,
                       max_candidates=CANDIDATES) if cands else []
        if got:
            row = {"status": "resolved", "page": page, "candidates": len(cands)}
            if attempt:
                row["via"] = via
            return row, got
        step = homepages.next_page(r.text, page) if attempt == 0 else None
        if not step:
            break
        nxt, via = step
        if not c.allowed(nxt):
            return {"status": "declined", "why": f"robots.txt, on the {via}", "page": page}, []
        r = c.get(nxt, retries=0)
        if r.status_code != 200:
            return {"status": "declined" if r.status_code in REFUSED else "error",
                    "why": f"HTTP {r.status_code} on the {via}", "page": page}, []
        if _wall(r):
            return {"status": "declined", "why": f"a challenge page on the {via}", "page": page}, []
        page = getattr(r, "url", "") or nxt
    return {"status": "no_images", "page": page}, []


class Timed:
    """A session that gives a live site at most TIMEOUT_S to answer, so a site that stalls costs a run seconds."""

    def __init__(self, s=None, timeout: float = None):
        self.s = s or requests.Session()
        self.timeout = timeout or TIMEOUT_S
        self.headers = self.s.headers

    def get(self, url, **kw):
        kw["timeout"] = min(kw.get("timeout") or self.timeout, self.timeout)
        return self.s.get(url, **kw)


def collect(c: Crawler, reg: registry.Registry, embedder, scorer, rid: str, today: str | None = None,
            only: list[str] | None = None, budget_s: float = BUDGET_S, clock=time.monotonic) -> dict:
    """Every house once today, each filed as soon as it is read, so a run cut short keeps what it did. A house
    already captured today is not asked again. A reader failure stops the run before that house is filed."""
    from .embed import VectorStore
    P = paths()
    day = today or date.today().isoformat()
    table = store.ShardedTable(P["captures"], "key")
    vectors = VectorStore(embedder.tag, root=P["vectors"])
    obs_path = P["obs"] / f"{scorer.rubric.version}.jsonl"
    scored = {(r["sha"], r["instrument"]) for r in store.read_jsonl(obs_path)}
    known = {h.id for h in reg.houses}
    domains = {h: ds for h, ds in homepages.sites().items() if h in known and ds and (not only or h in only)}
    counts = {"houses": 0, "resolved": 0, "declined": 0, "no_images": 0, "error": 0, "images": 0, "read": 0, "invalid": 0}
    declined, stopped, t0 = {}, None, clock()
    for house, ds in sorted(domains.items()):
        key = f"{house}:{day}"
        if table.get(key):
            continue
        if clock() - t0 > budget_s:
            stopped = "budget"
            break
        url = homepage(ds[0])
        try:
            row, got = read_page(c, url)
        except (requests.RequestException, MediaError, ValueError) as e:
            row, got = {"status": "error", "why": f"{e.__class__.__name__}: {str(e)[:160]}", "page": url}, []
        row.update({"key": key, "house_id": house, "date": day, "month": day[:7], "run": rid,
                    "images": [{"sha": f.sha, "phash": f.phash, "w": f.w, "h": f.h} for f in got]})
        new = [f for f in got if (f.sha, scorer.instrument) not in scored]
        obs = []
        try:
            for i in range(0, len(new), BATCH):
                part = new[i:i + BATCH]
                answers = score_batch(scorer, [jpeg_for_model(f.image) for f in part])
                for f, ans in zip(part, answers):
                    ob = {"sha": f.sha, "instrument": scorer.instrument, "rubric_version": scorer.rubric.version,
                          "run_id": rid, "house_id": house, "month": day[:7], "date": day, "scored_at": store.utc_now()}
                    if isinstance(ans, ScoreError):
                        ob["status"], ob["error"] = "invalid", str(ans)[:300]
                        counts["invalid"] += 1
                    else:
                        ob["output"], ob["status"] = ans, "ok"
                        counts["read"] += 1
                    obs.append(ob)
        except ScoreError as e:
            stopped = f"reader: {str(e)[:200]}"      # this house is not filed, so the next run reads it again
            for f in got:
                f.image.close()
            break
        for f in got:
            if f.sha not in vectors:
                vectors.add(f.sha, embedder.embed(f.image), day[:4])
            f.image.close()
        scored.update((ob["sha"], scorer.instrument) for ob in obs)
        counts["houses"] += 1
        counts["images"] += len(got)
        counts[row["status"]] += 1
        if row["status"] == "declined":
            declined[house] = row.get("why")
        table.upsert(row, shard=house)
        table.save()
        store.append_jsonl(obs_path, obs)
        vectors.save()
    out = {"run_id": rid, "date": day, "finished_at": store.utc_now(), **counts, "declined": declined,
           "stopped": stopped, "requests": c.calls}
    st = store.read_state(P["state"])
    st.update({"last_run": rid, "date": day, "declined": declined, "finished_at": out["finished_at"]})
    store.write_state(P["state"], st)
    return out


def probe(run: str) -> list[str]:
    P = paths()
    rows = [r for p in sorted(P["captures"].glob("*.jsonl")) for r in store.read_jsonl(p) if r.get("run") == run]
    status: dict[str, int] = {}
    for r in rows:
        status[r["status"]] = status.get(r["status"], 0) + 1
    print(f"probe live: {len(rows)} houses {status}")
    if rows and not status.get("resolved"):
        return [f"none of {len(rows)} houses gave a picture today: check the page parsing or the network"]
    return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.live")
    ap.add_argument("stage", choices=["collect", "probe"])
    ap.add_argument("--run", default=None)
    ap.add_argument("--only", nargs="*", default=None)
    a = ap.parse_args(argv)
    rid = a.run or config.run_id()
    if a.stage == "probe":
        errs = probe(rid)
        for e in errs:
            print(f"::error::{e}")
        return 1 if errs else 0
    c = Crawler(Timed(), pause=PAUSE_S)
    try:
        from .embed import OpenClipEmbedder
        from .score import load_rubric, make_scorer
        out = collect(c, registry.load(), OpenClipEmbedder(), make_scorer(load_rubric()), rid, only=a.only)
    except Exception as e:   # leave a record whatever happens
        out = {"run_id": rid, "finished_at": store.utc_now(), "crashed": f"{e.__class__.__name__}: {str(e)[:300]}",
               "requests": c.calls}
    store.append_jsonl(paths()["prov"], [out])
    print(out)
    if out.get("crashed"):
        print(f"::error::live capture crashed, recorded in provenance: {out['crashed']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
