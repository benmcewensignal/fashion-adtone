"""What the Wayback Machine holds for two histories Focal lacks: the houses' pages on Google's Ads Transparency
Center (Google keeps an ad only until a year after it was last shown, the minimum the Digital Services Act's
Article 39 asks) and the houses' YouTube films (the Data API gives today's totals only).

    python -m adtone.archiveprobe run --run <id> [--films 8]
    python -m adtone.archiveprobe calls --run <id>


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
import time
from collections import Counter
from pathlib import Path

import requests

from . import config, store
from .backcat import CDX, WAYBACK_RAW, Crawler, _cdx_rows

CREATIVE = re.compile(r"CR\d{20}")
ADVERTISER = re.compile(r"AR\d{20}")
VIEWS = re.compile(r'"viewCount":"(\d+)"')


def paths() -> dict[str, Path]:
    b = config.DATA / "archiveprobe"
    return {"ads": b / "ads.jsonl", "youtube": b / "youtube.jsonl", "state": config.STATE_DIR / "archiveprobe.json",
            "prov": config.PROV_DIR / "archiveprobe.jsonl"}


def views_in(html: str) -> int | None:
    """The view count a captured watch page shows (the player's videoDetails), or None."""
    m = VIEWS.search(html or "")
    return int(m.group(1)) if m else None


FAILED = Counter()
STREAK = [0]                     # calls failed in a row: ten mean the archive is down, and the probe stops


class ArchiveDown(Exception):
    pass


class OutOfTime(Exception):
    pass


DEADLINE = [float("inf")]        # the run's time budget: past it, each part stops and keeps what it has
TIMES: list[float] = []          # seconds each answered call took


def fetch(c: Crawler, url: str, params: dict | None = None, tries: int = 4):
    """The archive under load answers slowly or not at all: a timeout or a busy answer is retried after a longer
    pause, and after the last try the call is counted as failed rather than ending the probe."""
    if STREAK[0] >= 10:
        raise ArchiveDown("ten archive calls in a row went unanswered")
    for k in range(tries):
        if time.monotonic() > DEADLINE[0]:
            raise OutOfTime("the run's time budget ran out")
        c.calls += 1
        c.sleep(c.pause * (1 + 3 * k))
        t0 = time.monotonic()
        try:
            r = c.s.get(url, params=params, timeout=90)
        except requests.RequestException:
            continue
        if r.status_code not in (429, 500, 502, 503, 504):
            STREAK[0] = 0
            TIMES.append(time.monotonic() - t0)
            return r
    FAILED[url.split("/")[2]] += 1
    STREAK[0] += 1
    return None


def cdx(c: Crawler, url: str, prefix: bool = True, limit: int = 500) -> list[tuple[str, str]] | None:
    """The archive's captures of a page (status 200) as (timestamp, url), or None when the index did not answer."""
    q = {"url": url, "output": "json", "fl": "timestamp,original", "filter": "statuscode:200", "limit": str(limit)}
    if prefix:
        q["matchType"] = "prefix"
    r = fetch(c, CDX, q)
    return None if r is None else [(x[0], x[1]) for x in _cdx_rows(r)[1:] if len(x) >= 2]


def main_accounts() -> dict[str, str]:
    """Each house's verified account with the most ads at discovery."""
    best: dict[str, tuple[int, str]] = {}
    with (config.ROOT / "reference" / "google_advertisers.csv").open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["status"] == "house" and int(r["ads_found"] or 0) > best.get(r["house"], (-1, ""))[0]:
                best[r["house"]] = (int(r["ads_found"] or 0), r["advertiser_id"])
    return {h: v[1] for h, v in sorted(best.items())}


def ads_probe(c: Crawler) -> tuple[list[dict], dict]:
    rows, house_caps, site, rpc, read, with_ads, cut = [], [], [], [], [], 0, None
    try:
        site = cdx(c, "adstransparency.google.com/", limit=2000) or []
        rpc = cdx(c, "adstransparency.google.com/anji/", limit=500) or []
        for house, aid in main_accounts().items():
            caps = cdx(c, f"adstransparency.google.com/advertiser/{aid}")
            if caps is None:
                rows.append({"house": house, "advertiser_id": aid, "captures": None})
                continue
            house_caps += caps
            rows.append({"house": house, "advertiser_id": aid, "captures": len(caps),
                         "first": min((t for t, _ in caps), default=None), "last": max((t for t, _ in caps), default=None),
                         "pages": len({u for _, u in caps})})
        sample = (sorted(house_caps, reverse=True)[:3] + [(t, u) for t, u in site if "/advertiser/" in u][:3])[:4]
        for t, u in sample:                                     # does a captured advertiser page carry any ad?
            r = fetch(c, WAYBACK_RAW.format(ts=t, url=u))
            n = len(set(CREATIVE.findall(r.text))) if r is not None and r.status_code == 200 else None
            read.append({"timestamp": t, "status": None if r is None else r.status_code, "creative_ids": n})
            with_ads += 1 if n else 0
    except (OutOfTime, ArchiveDown) as e:
        cut = str(e)
    summary = {"cut_short": cut, "accounts_asked": len(rows), "site_captures": len(site), "site_pages": len({u for _, u in site}),
               "site_years": dict(sorted(Counter(t[:4] for t, _ in site).items())),
               "advertiser_pages": len({u for _, u in site if "/advertiser/" in u}),
               "data_call_captures": len(rpc), "pages_read": read, "pages_read_with_ads": with_ads,
               "house_accounts_captured": sum(1 for r in rows if r["captures"]),
               "house_accounts_unanswered": sum(1 for r in rows if r["captures"] is None)}
    return rows, summary


def films(per_house: int, seed: int = 12) -> list[dict]:
    """Per house, its most viewed films published from 2023 to August 2026, and half as many again at random."""
    out, rnd = [], random.Random(seed)
    for f in sorted((config.DATA / "youtube" / "videos").glob("*.jsonl")):
        vs = [v for v in store.read_jsonl(f) if "2023-01-01" <= (v.get("published") or "") <= "2026-08-31"]
        top = sorted(vs, key=lambda v: -(v.get("views") or 0))[:per_house]
        rest = [v for v in vs if v not in top]
        pick = [(v, "top") for v in top] + [(v, "random") for v in rnd.sample(rest, min(len(rest), per_house // 2))]
        out += [{"house": v["house_id"], "video_id": v["video_id"], "published": v["published"], "views_now": v.get("views"),
                 "why": why} for v, why in pick]
    return out


def youtube_probe(c: Crawler, per_house: int, reads: int = 30) -> tuple[list[dict], dict]:
    rows, cut = [], None
    try:
        picks = films(per_house)
        random.Random(3).shuffle(picks)                         # cut short, the films asked still spread across houses
        for v in picks:
            caps = cdx(c, f"youtube.com/watch?v={v['video_id']}")
            rows.append({**v, "captures": None if caps is None else sorted({t[:8] for t, _ in caps})})
        read = 0
        for v in sorted(rows, key=lambda r: -len(r["captures"] or [])):    # the views a capture shows, earliest first
            if read >= reads or not v["captures"]:
                continue
            ts = v["captures"][0]
            r = fetch(c, WAYBACK_RAW.format(ts=ts, url=f"https://www.youtube.com/watch?v={v['video_id']}"))
            v["read"] = {"date": ts, "status": None if r is None else r.status_code,
                         "views": views_in(r.text) if r is not None and r.status_code == 200 else None}
            read += 1
    except (OutOfTime, ArchiveDown) as e:
        cut = str(e)
    def share(xs, pred):
        return round(sum(1 for x in xs if pred(x)) / max(len(xs), 1), 3)
    summary = {"cut_short": cut, "films_asked": len(rows)}
    summary["unanswered"] = sum(1 for r in rows if r["captures"] is None)
    for why in ("top", "random"):
        xs = [r for r in rows if r["why"] == why and r["captures"] is not None]
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


def house_accounts() -> dict[str, str]:
    """Every account the reference list gives to a house (as its own, or in doubt): advertiser id to house."""
    with (config.ROOT / "reference" / "google_advertisers.csv").open(encoding="utf-8", newline="") as f:
        return {r["advertiser_id"]: r["house"] for r in csv.DictReader(f) if r["status"] in ("house", "doubt")}


def call_of(url: str) -> dict:
    """What a captured data call was: the service and method in its address, whether the archive kept it as a
    posted query (whose question then sits in the address it was filed under), and the advertisers it names."""
    path = url.split("adstransparency.google.com", 1)[-1]
    m = re.search(r"/rpc/([A-Za-z]+/[A-Za-z]+)", path)
    posted = "__wb_post_data" in url or "__wb_method=post" in url
    text = url
    if posted:
        import base64
        from urllib.parse import parse_qs, urlsplit
        for v in parse_qs(urlsplit(url).query).get("__wb_post_data", []):
            try:
                text += " " + base64.b64decode(v + "=" * (-len(v) % 4)).decode("utf-8", "ignore")
            except Exception:
                pass
    return {"call": m.group(1) if m else path.split("?")[0][:60], "posted": posted, "advertisers": sorted(set(ADVERTISER.findall(text)))}


def calls_probe(c: Crawler, open_max: int = 80) -> tuple[list[dict], dict]:
    """Every capture the archive holds of the Transparency Center's data calls, the questions behind the
    advertiser pages: which call, when, and whether it names one of the houses' accounts; then a sample of the
    stored answers opened, for the ads and advertisers each carries. Only counts and the houses' own ids are
    kept, never another advertiser's id and never an ad's words."""
    houses = house_accounts()
    q = {"url": "adstransparency.google.com/anji/", "matchType": "prefix", "output": "json",
         "fl": "timestamp,original,mimetype,statuscode,digest", "limit": "50000"}
    r = fetch(c, CDX, q)
    caps = [] if r is None else [x for x in _cdx_rows(r)[1:] if len(x) >= 5]
    rows, seen = [], set()
    for ts, url, mime, status, digest in caps:
        info = call_of(url)
        rows.append({"timestamp": ts, "call": info["call"], "status": status, "posted": info["posted"],
                     "names_advertiser": bool(info["advertisers"]),
                     "house": sorted({houses[a] for a in info["advertisers"] if a in houses}) or None, "digest": digest, "url": url})
    # open: first every capture that names a house, then distinct answers spread over calls and years
    order = [x for x in rows if x["house"] and x["status"] == "200"]
    rest = [x for x in rows if not x["house"] and x["status"] == "200"]
    by = {}
    for x in rest:
        by.setdefault((x["call"], x["timestamp"][:4]), []).append(x)
    while any(by.values()) and len(order) < open_max * 3:
        for k in sorted(by):
            if by[k]:
                order.append(by[k].pop(0))
    opened, cut = [], None
    try:
        for x in order:
            if len(opened) >= open_max:
                break
            if x["digest"] in seen:
                continue
            seen.add(x["digest"])
            resp = fetch(c, WAYBACK_RAW.format(ts=x["timestamp"], url=x["url"]))
            body = resp.text if resp is not None and resp.status_code == 200 else ""
            advs = set(ADVERTISER.findall(body))
            x["opened"] = {"status": None if resp is None else resp.status_code, "creatives": len(set(CREATIVE.findall(body))),
                           "advertisers": len(advs), "houses": sorted({houses[a] for a in advs if a in houses}) or None}
            opened.append(x)
    except (OutOfTime, ArchiveDown) as e:
        cut = str(e)
    for x in rows:
        x.pop("url", None)                     # the address can carry another advertiser's query: kept out
    calls = Counter(x["call"] for x in rows)
    summary = {"captures": len(rows), "cut_short": cut, "calls": dict(calls.most_common(12)),
               "years": dict(sorted(Counter(x["timestamp"][:4] for x in rows).items())),
               "status_200": sum(1 for x in rows if x["status"] == "200"), "posted": sum(1 for x in rows if x["posted"]),
               "naming_an_advertiser": sum(1 for x in rows if x["names_advertiser"]),
               "naming_a_house": sum(1 for x in rows if x["house"]),
               "houses_named": sorted({h for x in rows if x["house"] for h in x["house"]}),
               "opened": len(opened), "opened_with_ads": sum(1 for x in opened if x["opened"]["creatives"]),
               "opened_with_a_house": sum(1 for x in opened if x["opened"]["houses"]),
               "houses_in_answers": sorted({h for x in opened if x["opened"]["houses"] for h in x["opened"]["houses"]}),
               "creatives_in_house_answers": sum(x["opened"]["creatives"] for x in opened if x["opened"]["houses"])}
    return rows, summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.archiveprobe")
    ap.add_argument("stage", choices=["run", "calls"])
    ap.add_argument("--run", default=config.run_id())
    ap.add_argument("--films", type=int, default=4)
    ap.add_argument("--budget-min", type=float, default=36)
    a = ap.parse_args(argv)
    P = paths()
    c = Crawler(pause=1.5)
    start = time.monotonic()
    if a.stage == "calls":
        DEADLINE[0] = start + a.budget_min * 60
        try:
            rows, summary = calls_probe(c)
            store.write_jsonl(config.DATA / "archiveprobe" / "calls.jsonl", rows)
        except Exception as e:
            summary = {"stopped": f"{type(e).__name__}: {str(e)[:300]}"}
        state = json.loads(P["state"].read_text()) if P["state"].exists() else {}
        ts = sorted(TIMES)
        state["data_calls"] = {**summary, "run": a.run, "archive_calls": c.calls, "answered": len(ts), "updated_at": store.utc_now()}
        store.write_state(P["state"], state)
        store.append_jsonl(P["prov"], [{"run": a.run, "data_calls": summary}])
        print(json.dumps(state["data_calls"], indent=1)[:4000])
        return 0
    DEADLINE[0] = start + a.budget_min * 60 * 0.3              # the Google part gets under a third of the time
    out = {"run": a.run}
    for name, fn in (("ads", lambda: ads_probe(c)), ("youtube", lambda: youtube_probe(c, a.films))):
        STREAK[0] = 0
        try:
            rows, summary = fn()
            store.write_jsonl(P[name], rows)
            out[name] = summary
        except Exception as e:                                    # one part failing leaves the other's result
            out[name] = {"stopped": f"{type(e).__name__}: {str(e)[:300]}"}
        DEADLINE[0] = start + a.budget_min * 60                   # the films get the rest
    ts = sorted(TIMES)
    out.update({"archive_calls": c.calls, "answered": len(ts), "failed_calls": dict(FAILED),
                "seconds_per_answer": {"median": round(ts[len(ts) // 2], 1) if ts else None,
                                       "slowest": round(ts[-1], 1) if ts else None},
                "updated_at": store.utc_now()})
    store.write_state(P["state"], out)
    store.append_jsonl(P["prov"], [out])
    print(json.dumps(out, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
