"""What the Wayback Machine holds for two histories Focal lacks: the houses' pages on Google's Ads Transparency
Center (Google keeps an ad only until a year after it was last shown, the minimum the Digital Services Act's
Article 39 asks) and the houses' YouTube films (the Data API gives today's totals only).

    python -m adtone.archiveprobe run --run <id> [--films 8]

A probe, run by hand. For the Transparency Center: how many captures the archive has of each house's main
advertiser page and of the site's data calls, and whether a captured page carries any ad (a creative id). For
YouTube: for each house's most viewed films since 2023 and a few chosen at random, how many captures of the
film's page the archive has and when, and the view count a few of those captures show. It stores counts, dates
and view figures only: no page, no picture.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

from . import config, store
from .backcat import CDX, WAYBACK_RAW, Crawler, _cdx_rows

CREATIVE = re.compile(r"CR\d{20}")
VIEWS = re.compile(r'"viewCount":"(\d+)"')


def paths() -> dict[str, Path]:
    b = config.DATA / "archiveprobe"
    return {"ads": b / "ads.jsonl", "youtube": b / "youtube.jsonl", "state": config.STATE_DIR / "archiveprobe.json",
            "prov": config.PROV_DIR / "archiveprobe.jsonl"}


def views_in(html: str) -> int | None:
    """The view count a captured watch page shows (the player's videoDetails), or None."""
    m = VIEWS.search(html or "")
    return int(m.group(1)) if m else None


def cdx(c: Crawler, url: str, prefix: bool = True, limit: int = 500) -> list[tuple[str, str]]:
    q = {"url": url, "output": "json", "fl": "timestamp,original", "filter": "statuscode:200", "limit": str(limit)}
    if prefix:
        q["matchType"] = "prefix"
    return [(r[0], r[1]) for r in _cdx_rows(c.get(CDX, q))[1:] if len(r) >= 2]


def main_accounts() -> dict[str, str]:
    """Each house's verified account with the most ads at discovery."""
    best: dict[str, tuple[int, str]] = {}
    with (config.ROOT / "reference" / "google_advertisers.csv").open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["status"] == "house" and int(r["ads_found"] or 0) > best.get(r["house"], (-1, ""))[0]:
                best[r["house"]] = (int(r["ads_found"] or 0), r["advertiser_id"])
    return {h: v[1] for h, v in sorted(best.items())}


def ads_probe(c: Crawler) -> tuple[list[dict], dict]:
    rows, house_caps = [], []
    for house, aid in main_accounts().items():
        caps = cdx(c, f"adstransparency.google.com/advertiser/{aid}")
        house_caps += caps
        rows.append({"house": house, "advertiser_id": aid, "captures": len(caps),
                     "first": min((t for t, _ in caps), default=None), "last": max((t for t, _ in caps), default=None),
                     "pages": len({u for _, u in caps})})
    site = cdx(c, "adstransparency.google.com/", limit=5000)
    rpc = cdx(c, "adstransparency.google.com/anji/", limit=500)
    read, with_ads = [], 0
    sample = (sorted(house_caps, reverse=True)[:3] + [(t, u) for t, u in site if "/advertiser/" in u][:3])[:4]
    for t, u in sample:                                         # does a captured advertiser page carry any ad?
        r = c.get(WAYBACK_RAW.format(ts=t, url=u))
        n = len(set(CREATIVE.findall(r.text))) if r.status_code == 200 else None
        read.append({"timestamp": t, "status": r.status_code, "creative_ids": n})
        with_ads += 1 if n else 0
    summary = {"site_captures": len(site), "site_pages": len({u for _, u in site}),
               "site_years": dict(sorted(Counter(t[:4] for t, _ in site).items())),
               "advertiser_pages": len({u for _, u in site if "/advertiser/" in u}),
               "data_call_captures": len(rpc), "pages_read": read, "pages_read_with_ads": with_ads,
               "house_accounts_captured": sum(1 for r in rows if r["captures"])}
    return rows, summary


def films(per_house: int, seed: int = 12) -> list[dict]:
    """Per house, its most viewed films published since 2023 and as many again chosen at random."""
    out, rnd = [], random.Random(seed)
    for f in sorted((config.DATA / "youtube" / "videos").glob("*.jsonl")):
        vs = [v for v in store.read_jsonl(f) if "2023-01-01" <= (v.get("published") or "") <= "2026-08-31"]
        top = sorted(vs, key=lambda v: -(v.get("views") or 0))[:per_house]
        rest = [v for v in vs if v not in top]
        pick = [(v, "top") for v in top] + [(v, "random") for v in rnd.sample(rest, min(len(rest), per_house // 2))]
        out += [{"house": v["house_id"], "video_id": v["video_id"], "published": v["published"], "views_now": v.get("views"),
                 "why": why} for v, why in pick]
    return out


def youtube_probe(c: Crawler, per_house: int, reads: int = 40) -> tuple[list[dict], dict]:
    rows = []
    for v in films(per_house):
        caps = cdx(c, f"youtube.com/watch?v={v['video_id']}")
        rows.append({**v, "captures": sorted({t[:8] for t, _ in caps})})
    read = 0
    for v in sorted(rows, key=lambda r: -len(r["captures"])):    # the views a capture shows, earliest capture first
        if read >= reads or not v["captures"]:
            continue
        ts = v["captures"][0]
        r = c.get(WAYBACK_RAW.format(ts=ts, url=f"https://www.youtube.com/watch?v={v['video_id']}"))
        v["read"] = {"date": ts, "status": r.status_code, "views": views_in(r.text) if r.status_code == 200 else None}
        read += 1
    def share(xs, pred):
        return round(sum(1 for x in xs if pred(x)) / max(len(xs), 1), 3)
    summary = {}
    for why in ("top", "random"):
        xs = [r for r in rows if r["why"] == why]
        summary[why] = {"films": len(xs), "any_capture": share(xs, lambda r: r["captures"]),
                        "two_or_more_days": share(xs, lambda r: len(r["captures"]) >= 2),
                        "captured_in_first_30_days": share(xs, lambda r: any(
                            0 <= (_d(t) - _d(r["published"].replace("-", ""))).days <= 30 for t in r["captures"]))}
    got = [r["read"] for r in rows if r.get("read")]
    summary["captures_read"] = len(got)
    summary["captures_with_views"] = sum(1 for g in got if g["views"] is not None)
    return rows, summary


def _d(t: str):
    from datetime import date
    return date(int(t[:4]), int(t[4:6]), int(t[6:8]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.archiveprobe")
    ap.add_argument("stage", choices=["run"])
    ap.add_argument("--run", default=config.run_id())
    ap.add_argument("--films", type=int, default=8)
    a = ap.parse_args(argv)
    P = paths()
    c = Crawler(pause=1.5)
    out = {"run": a.run}
    for name, fn in (("ads", lambda: ads_probe(c)), ("youtube", lambda: youtube_probe(c, a.films))):
        try:
            rows, summary = fn()
            store.write_jsonl(P[name], rows)
            out[name] = summary
        except Exception as e:                                    # one part failing leaves the other's result
            out[name] = {"stopped": f"{type(e).__name__}: {str(e)[:300]}"}
    out.update({"archive_calls": c.calls, "updated_at": store.utc_now()})
    store.write_state(P["state"], out)
    store.append_jsonl(P["prov"], [out])
    print(json.dumps(out, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
