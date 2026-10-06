"""Homepage history: what each brand put on its front page, month by month, from the Wayback Machine.

    python -m adtone.homepages collect --run <id> [--max-captures 600] [--budget-min 90]
    python -m adtone.homepages probe --run <id>

A brand's homepage carries the image it chose for the moment: the season's campaign, a show, a film
still. The Wayback Machine has captured the homepages of these houses several times a month for
years, which gives a long and regular history of each brand's chosen image that no ad library holds.

For each brand and month this takes the first capture of the month, reads the page's share image
(og:image, the picture the brand chose to represent the page) and its largest images, fetches them
from the archive as it held them then, and sends each image not seen before through the reader and
the fingerprint. Images are held in memory only; the reader's answers and the fingerprint's numbers
are kept. Months are worked newest first and across every brand before going further back, so a
run cut short by its time budget leaves an even history; the next run resumes.

Modern luxury sites draw much of the page with scripts, so an archived copy often lacks its pictures
except the share image. A month with no usable image is recorded as such and not retried.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests

from . import config, registry, store
from .backcat import CDX, Crawler, _cdx_rows, archived_image_urls
from .media import MediaError, download, jpeg_for_model
from .score import ScoreError

FIRST_MONTH = "2014-01"
REPLAY = "https://web.archive.org/web/{ts}/{url}"
SITES_FILE = config.ROOT / "reference" / "brand_sites.csv"
KEEP_PER_CAPTURE = 4
MAX_ATTEMPTS = 3        # a month that errors three times is left: the archive does not have it
REWRITE = re.compile(r"(?:https?:)?(?://web\.archive\.org)?/web/\d{1,14}(?:[a-z]{2}_)?/(?=https?://|//)")


def paths() -> dict[str, Path]:
    b = config.DATA / "homepages"
    return {"captures": b / "captures", "obs": b / "obs", "vectors": b / "vectors",
            "state": config.STATE_DIR / "homepages.json", "prov": config.PROV_DIR / "homepages.jsonl"}


def sites(path: Path = SITES_FILE) -> dict[str, list[str]]:
    with path.open(encoding="utf-8") as f:
        return {r["house"]: [d.strip() for d in r["domains"].split(";") if d.strip()] for r in csv.DictReader(f)}


def unrewrite(html: str) -> str:
    """Archive replay rewrites every link to point into the archive; put the original addresses back."""
    return REWRITE.sub("", html)


def monthly_captures(c: Crawler, domain: str, first: str = FIRST_MONTH) -> dict[str, tuple[str, str]]:
    """Month -> (timestamp, original url): the first capture of each month of the domain's homepage."""
    r = c.get(CDX, {"url": domain, "output": "json", "fl": "timestamp,original,statuscode",
                    "from": first.replace("-", ""), "collapse": "timestamp:6",
                    "filter": "statuscode:(200|301|302|307|308)"})
    out: dict[str, tuple[str, str]] = {}
    for row in _cdx_rows(r)[1:]:
        ts, original = row[0], row[1]
        m = f"{ts[:4]}-{ts[4:6]}"
        out.setdefault(m, (ts, original))
    return out


def final_url(replayed: str, requested: str) -> str:
    """The page's own address after the archive followed its redirects (for resolving relative links)."""
    m = re.search(r"/web/\d{1,14}(?:[a-z]{2}_)?/(https?://.+)$", replayed)
    return m.group(1) if m else requested


def page_images(html: str, page_url: str, ts: str) -> list[str]:
    """The share image first, then the page's other large images, as archive addresses at `ts`."""
    return archived_image_urls(unrewrite(html), page_url, ts)


def _key(house: str, month: str) -> str:
    return f"{house}:{month}"


def plan(caps: dict[str, dict[str, tuple[str, str]]], done: set[str]) -> list[tuple[str, str, str, str]]:
    """(house, month, ts, url) still to read: newest month first, every house before going further back."""
    months = sorted({m for per in caps.values() for m in per}, reverse=True)
    out = []
    for m in months:
        for house in sorted(caps):
            if m in caps[house] and _key(house, m) not in done:
                ts, url = caps[house][m]
                out.append((house, m, ts, url))
    return out


def collect(c: Crawler, reg: registry.Registry, embedder, scorer, rid: str, max_captures: int = 600,
            budget_s: float = 90 * 60, clock=time.monotonic) -> dict:
    from .embed import VectorStore
    P = paths()
    table = store.ShardedTable(P["captures"], "key")
    vectors = VectorStore(embedder.tag, root=P["vectors"])
    obs_path = P["obs"] / f"{scorer.rubric.version}.jsonl"
    scored = {(r["sha"], r["instrument"]) for r in store.read_jsonl(obs_path)}
    known = {h.id for h in reg.houses}
    domains = {h: ds for h, ds in sites().items() if h in known}
    t0 = clock()
    counts = {"captures": 0, "resolved": 0, "no_images": 0, "error": 0, "images": 0, "new_images": 0, "scored_ok": 0,
              "invalid": 0}
    index_errors: dict[str, str] = {}

    caps: dict[str, dict[str, tuple[str, str]]] = {}
    for house, ds in sorted(domains.items()):
        merged: dict[str, tuple[str, str]] = {}
        for d in ds:   # the current domain first; an older one fills months the current one lacks
            try:
                for m, v in monthly_captures(c, d).items():
                    merged.setdefault(m, v)
            except (requests.RequestException, ValueError) as e:
                index_errors[f"{house}:{d}"] = f"{e.__class__.__name__}: {str(e)[:160]}"
        caps[house] = merged
    done = {k for k, r in table.rows.items()
            if r.get("status") in ("resolved", "no_images") or r.get("attempts", 0) >= MAX_ATTEMPTS}
    todo = plan(caps, done)
    new_obs: list[dict] = []
    stopped = None
    for house, month, ts, url in todo[:max_captures]:
        if clock() - t0 > budget_s:
            stopped = "budget"
            break
        prev = table.get(_key(house, month)) or {}
        row = {"key": _key(house, month), "house_id": house, "month": month, "capture": ts, "url": url, "run": rid,
               "attempts": prev.get("attempts", 0) + 1}
        try:
            r = c.get(REPLAY.format(ts=ts, url=url))
            ctype = (getattr(r, "headers", None) or {}).get("Content-Type", "text/html")
            if r.status_code != 200 or "html" not in ctype:
                row["status"], row["error"] = "error", f"HTTP {r.status_code} {ctype[:40]}"
            else:
                urls = page_images(r.text, final_url(getattr(r, "url", "") or "", url), ts)
                got = download(urls, c.s, keep=KEEP_PER_CAPTURE) if urls else []
                if not got:
                    row["status"] = "no_images"
                else:
                    imgs = []
                    for f in got:
                        counts["images"] += 1
                        if f.sha not in vectors:
                            vectors.add(f.sha, embedder.embed(f.image), month[:4])
                            counts["new_images"] += 1
                        if (f.sha, scorer.instrument) not in scored:
                            ob = {"sha": f.sha, "instrument": scorer.instrument, "rubric_version": scorer.rubric.version,
                                  "run_id": rid, "scored_at": store.utc_now(), "house_id": house, "month": month}
                            try:
                                ob["output"], ob["status"] = scorer.score(jpeg_for_model(f.image)), "ok"
                                counts["scored_ok"] += 1
                            except ScoreError as e:
                                if str(e).startswith("API:"):
                                    raise
                                ob["status"], ob["error"] = "invalid", str(e)[:300]
                                counts["invalid"] += 1
                            new_obs.append(ob)
                            scored.add((f.sha, scorer.instrument))
                        imgs.append({"sha": f.sha, "phash": f.phash, "w": f.w, "h": f.h})
                        f.image.close()
                    row["status"], row["images"] = "resolved", imgs
        except ScoreError as e:
            stopped = f"reader: {str(e)[:200]}"
            break
        except (requests.RequestException, MediaError, ValueError) as e:
            row["status"], row["error"] = "error", f"{e.__class__.__name__}: {str(e)[:200]}"
        counts[row["status"]] += 1
        counts["captures"] += 1
        table.upsert(row, shard=house)
        if counts["captures"] % 50 == 0:   # a run cut off by its job timeout keeps most of its work
            table.save()
            store.append_jsonl(obs_path, new_obs)
            new_obs = []
            vectors.save()
    table.save()
    store.append_jsonl(obs_path, new_obs)
    vectors.save()
    months_known = {h: len(v) for h, v in caps.items()}
    out = {"run_id": rid, "finished_at": store.utc_now(), **counts, "remaining": max(0, len(todo) - counts["captures"]),
           "months_in_archive": months_known, "index_errors": index_errors, "stopped": stopped, "requests": c.calls}
    st = store.read_state(P["state"])
    st.update({"last_run": rid, "finished_at": out["finished_at"], "remaining": out["remaining"],
               "months_in_archive": months_known})
    store.write_state(P["state"], st)
    return out


def probe(run: str) -> list[str]:
    P = paths()
    rows = [r for p in sorted(P["captures"].glob("*.jsonl")) for r in store.read_jsonl(p) if r.get("run") == run]
    if not rows:
        return [f"homepages: run {run} read no captures"]
    status: dict[str, int] = {}
    for r in rows:
        status[r["status"]] = status.get(r["status"], 0) + 1
    print(f"probe homepages: {len(rows)} captures {status}")
    errs = []
    if len(rows) >= 30 and not status.get("resolved"):
        errs.append(f"none of {len(rows)} captures yielded an image: check the page parsing")
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.homepages")
    ap.add_argument("stage", choices=["collect", "probe"])
    ap.add_argument("--run", default=None)
    ap.add_argument("--max-captures", type=int, default=600)
    ap.add_argument("--budget-min", type=float, default=90)
    a = ap.parse_args(argv)
    rid = a.run or config.run_id()
    if a.stage == "probe":
        errs = probe(rid)
        for e in errs:
            print(f"::error::{e}")
        return 1 if errs else 0
    c = Crawler()
    try:
        from .embed import OpenClipEmbedder
        from .score import load_rubric, make_scorer
        out = collect(c, registry.load(), OpenClipEmbedder(), make_scorer(load_rubric()), rid, a.max_captures,
                      a.budget_min * 60)
    except Exception as e:   # leave a record whatever happens
        out = {"run_id": rid, "finished_at": store.utc_now(), "crashed": f"{e.__class__.__name__}: {str(e)[:300]}",
               "requests": c.calls}
    store.append_jsonl(paths()["prov"], [out])
    print({k: v for k, v in out.items() if k != "months_in_archive"})
    if out.get("crashed"):
        print(f"::error::homepages crashed, recorded in provenance: {out['crashed']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
