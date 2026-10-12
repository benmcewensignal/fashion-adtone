"""What the Wayback Machine holds for two histories Focal lacks: the houses' pages on Google's Ads Transparency
Center (Google keeps an ad only until a year after it was last shown, the minimum the Digital Services Act's
Article 39 asks) and the houses' YouTube films (the Data API gives today's totals only).

    python -m adtone.archiveprobe run --run <id> [--films 8]
    python -m adtone.archiveprobe calls --run <id>
    python -m adtone.archiveprobe pages --run <id>
    python -m adtone.archiveprobe accounts --run <id>


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


CALL_PREFIXES = ("adstransparency.google.com/anji/_/rpc/SearchService/", "adstransparency.google.com/anji/_/rpc/LookupService/GetCreative")


def calls_probe(c: Crawler, open_max: int = 80) -> tuple[list[dict], dict]:
    """Every capture the archive holds of the Transparency Center's data calls, the questions behind the
    advertiser pages: which call, when, and whether it names one of the houses' accounts; then a sample of the
    stored answers opened, for the ads and advertisers each carries. Only counts and the houses' own ids are
    kept, never another advertiser's id and never an ad's words."""
    houses = house_accounts()
    # the index sorts captures by address, so one query over all the calls fills up on the region and advertiser
    # lookups before reaching the calls that list ads: each call that can carry ads is asked for on its own
    caps, per_prefix = [], {}
    for pre in CALL_PREFIXES:
        q = {"url": pre, "matchType": "prefix", "output": "json", "fl": "timestamp,original,mimetype,statuscode,digest", "limit": "50000"}
        r = fetch(c, CDX, q)
        got = None if r is None else [x for x in _cdx_rows(r)[1:] if len(x) >= 5]
        per_prefix[pre.split("/rpc/")[1]] = None if got is None else len(got)
        caps += got or []
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
    summary = {"captures": len(rows), "asked": per_prefix, "cut_short": cut, "calls": dict(calls.most_common(12)),
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


PAGES = "adstransparency.google.com/advertiser/"
SITE = "adstransparency.google.com/"
DATE = "%Y%m%d%H%M%S"


def page_of(url: str) -> dict:
    """What a saved Transparency Center page was: the advertiser its address names, and whether it is one ad's
    page (its address then carries the ad's id) or the advertiser's list of ads."""
    path = url.split("adstransparency.google.com", 1)[-1]
    a = ADVERTISER.search(path)
    return {"advertiser": a.group(0) if a else None, "ad": bool(re.search(r"/creative/CR\d{20}", path))}


def house_sites() -> dict[str, str]:
    """Each house's websites, as domain to house."""
    out = {}
    with (config.ROOT / "reference" / "brand_sites.csv").open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            out.update({d.strip().lower(): r["house"] for d in r["domains"].split(";") if d.strip()})
    return out


def site_named(url: str, sites: dict[str, str]) -> str | None:
    """The house whose website a search by website asks about (the address's domain=), or None."""
    from urllib.parse import parse_qs, urlsplit
    for v in parse_qs(urlsplit(url).query).get("domain", []):
        d = v.lower().strip().removeprefix("www.")
        for s, h in sites.items():
            if d == s or d.endswith("." + s):
                return h
    return None


def paired(a: list[str], b: list[str], within: int = 120, shift_s: int = 0) -> int:
    """How many of the archive times in a can each be matched to a different time in b no more than `within`
    seconds apart, b moved by `shift_s` seconds (a shift of a day gives the matches chance alone would make)."""
    from datetime import datetime, timedelta
    ta = sorted(datetime.strptime(t[:14], DATE) for t in a)
    tb = sorted(datetime.strptime(t[:14], DATE) + timedelta(seconds=shift_s) for t in b)
    i = j = n = 0
    while i < len(ta) and j < len(tb):
        d = (tb[j] - ta[i]).total_seconds()
        if abs(d) <= within:
            n, i, j = n + 1, i + 1, j + 1
        elif d < 0:
            j += 1
        else:
            i += 1
    return n


def cdx_all(c: Crawler, params: dict, pages: int = 6) -> tuple[list[list[str]], list[dict]]:
    """Every row the index gives for a query, followed across its resume keys (at most `pages` queries), and
    for each query asked the archive's answer status and the rows it gave."""
    out, asked, key = [], [], None
    for _ in range(pages):
        q = {**params, "showResumeKey": "true", **({"resumeKey": key} if key else {})}
        r = fetch(c, CDX, q)
        rows = [] if r is None else _cdx_rows(r)
        width = len(rows[0]) if rows else 0
        got = [x for x in rows[1:] if len(x) == width]
        asked.append({"status": None if r is None else r.status_code, "rows": len(got)})
        out += got
        key = rows[-1][0] if len(rows) >= 2 and len(rows[-1]) == 1 and rows[-2] == [] else None
        if not key:
            break
    return out, asked


def pages_probe(c: Crawler) -> tuple[list[dict], dict]:
    """The Transparency Center pages the archive saved, whose addresses do name their advertiser: how many, of
    which kind (one ad's page, an advertiser's list of ads, a search by website), when, and whether any is a
    house's. Then how many of the saved single-ad lookups and ad lists (the calls stage) were saved within two
    minutes of such a page, as a page's own calls are, set against the matches a day's shift gives by chance.
    Only counts and the houses' own ids are kept, never another advertiser's id or website."""
    houses, sites = house_accounts(), house_sites()
    fl = {"output": "json", "fl": "timestamp,original,statuscode", "limit": "50000"}
    got, asked = cdx_all(c, {"url": PAGES, "matchType": "prefix", **fl})
    srch, asked_s = cdx_all(c, {"url": SITE, "matchType": "prefix", "filter": "original:.*domain=.*", **fl}, pages=3)
    srch2, asked_s2 = cdx_all(c, {"url": SITE + "?d", "matchType": "prefix", **fl}, pages=2)

    def status(s):
        return "200" if s == "200" else "copy of an earlier save" if s == "-" else "redirect" if s.startswith("3") else "other"

    kinds, months, advs, rows = Counter(), Counter(), set(), []
    ad_times, list_times = [], []
    for ts, url, st in got:
        p = page_of(url)
        kind = "ad page" if p["ad"] else "advertiser page" if p["advertiser"] else "other"
        kinds[f"{kind}, {status(st)}"] += 1
        months[(kind, ts[:6])] += 1
        (ad_times if kind == "ad page" else list_times).append(ts)
        if p["advertiser"]:
            advs.add(p["advertiser"])
        if p["advertiser"] in houses:
            rows.append({"house": houses[p["advertiser"]], "advertiser_id": p["advertiser"], "kind": kind,
                         "timestamp": ts, "status": st})
    seen_search, search_times = set(), []
    for ts, url, st in srch + srch2:
        if (ts, url) in seen_search or "domain=" not in url:
            continue
        seen_search.add((ts, url))
        search_times.append(ts)
        h = site_named(url, sites)
        if h:
            rows.append({"house": h, "advertiser_id": None, "kind": "search by website", "timestamp": ts, "status": st})
    calls = store.read_jsonl(config.DATA / "archiveprobe" / "ad_calls.jsonl")
    lookups = [x["timestamp"] for x in calls if x["call"] == "LookupService/GetCreativeById"]
    lists = [x["timestamp"] for x in calls if x["call"] == "SearchService/SearchCreatives"]
    lk_months = Counter(t[:6] for t in lookups)
    by_month = {m: {"single-ad lookups": lk_months.get(m, 0), "ad pages": months.get(("ad page", m), 0)}
                for m in sorted(set(lk_months) | {m for k, m in months if k == "ad page"})}
    day = 86400
    summary = {
        "asked": {"pages": asked, "searches by website (filtered)": asked_s, "searches by website (domain first)": asked_s2},
        "page_captures": len(got), "kinds": dict(kinds.most_common()), "advertisers_named": len(advs),
        "searches_by_website": len(search_times),
        "house_pages": len([r for r in rows if r["kind"] != "search by website"]),
        "house_searches": len([r for r in rows if r["kind"] == "search by website"]),
        "houses_found": sorted({r["house"] for r in rows}),
        "lookups_with_an_ad_page_within_2_min": paired(lookups, ad_times),
        "lookups_by_chance_a_day_apart": paired(lookups, ad_times, shift_s=day),
        "lookups": len(lookups),
        "ad_lists_with_a_page_within_2_min": paired(lists, list_times + search_times),
        "ad_lists_by_chance_a_day_apart": paired(lists, list_times + search_times, shift_s=day),
        "ad_lists": len(lists),
        "by_month": by_month,
    }
    return rows, summary


def accounts_probe(c: Crawler, open_max: int = 60, within: int = 120) -> tuple[list[dict], dict]:
    """Each of the houses' accounts asked for on its own (the pages stage reads the index in address order and
    stops at 300,000 rows, short of most accounts): the ad pages and ad lists the archive saved under it, when,
    and which of the house's ads they show. Then the single-ad lookups and ad lists saved within two minutes of a
    house's page, opened to see whether they hold that house's ads. Kept: per account its counts and dates, and
    per saved page of a house its date, kind and the house's own ad id. Never another advertiser's id."""
    with (config.ROOT / "reference" / "google_advertisers.csv").open(encoding="utf-8", newline="") as f:
        accounts = [r for r in csv.DictReader(f) if r["status"] in ("house", "doubt")]
    per, pages, cut = [], [], None
    try:
        for r in accounts:
            aid = r["advertiser_id"]
            got, asked = cdx_all(c, {"url": PAGES + aid, "matchType": "prefix", "output": "json",
                                     "fl": "timestamp,original,statuscode", "limit": "10000"}, pages=2)
            mine = [(ts, u, st) for ts, u, st in got if page_of(u)["advertiser"] == aid]
            for ts, u, st in mine:
                m = CREATIVE.search(u.split("/creative/", 1)[-1]) if page_of(u)["ad"] else None
                pages.append({"house": r["house"], "account_status": r["status"], "advertiser_id": aid,
                              "kind": "ad page" if m else "advertiser page", "ad_id": m.group(0) if m else None,
                              "timestamp": ts, "status": st})
            per.append({"house": r["house"], "account_status": r["status"], "advertiser_id": aid,
                        "answered": asked[0]["status"] if asked else None, "saved": len(mine),
                        "saved_200": sum(1 for x in mine if x[2] == "200"),
                        "first": min((x[0] for x in mine), default=None), "last": max((x[0] for x in mine), default=None)})
    except (OutOfTime, ArchiveDown) as e:
        cut = str(e)
    # the data calls saved near a house's page: a page's own calls are saved within seconds of it
    opened = []
    if pages and not cut:
        try:
            houses = house_accounts()
            near = []
            for pre, kind in ((CALL_PREFIXES[1], "ad page"), (CALL_PREFIXES[0] + "SearchCreatives", "advertiser page")):
                got, _ = cdx_all(c, {"url": pre, "matchType": "prefix", "output": "json",
                                     "fl": "timestamp,original,statuscode,digest", "limit": "50000"}, pages=2)
                from datetime import datetime
                when = [(datetime.strptime(p["timestamp"][:14], DATE), p) for p in pages if p["kind"] == kind]
                for ts, u, st, dg in got:
                    t = datetime.strptime(ts[:14], DATE)
                    hit = [p for tp, p in when if abs((t - tp).total_seconds()) <= within]
                    if hit and st == "200":
                        near.append((ts, u, dg, hit[0]))
            seen = set()
            for ts, u, dg, p in near:
                if len(opened) >= open_max:
                    break
                if dg in seen:
                    continue
                seen.add(dg)
                resp = fetch(c, WAYBACK_RAW.format(ts=ts, url=u))
                body = resp.text if resp is not None and resp.status_code == 200 else ""
                advs = set(ADVERTISER.findall(body))
                opened.append({"house": p["house"], "kind": p["kind"], "page_timestamp": p["timestamp"], "call_timestamp": ts,
                               "status": None if resp is None else resp.status_code,
                               "ads_in_answer": len(set(CREATIVE.findall(body))),
                               "holds_the_house": bool({a for a in advs if houses.get(a) == p["house"]}),
                               "holds_that_ad": bool(p["ad_id"] and p["ad_id"] in body)})
        except (OutOfTime, ArchiveDown) as e:
            cut = str(e)
    by_house = {}
    for p in pages:
        b = by_house.setdefault(p["house"], {"ad pages": 0, "advertiser pages": 0, "ads": set(), "first": None, "last": None})
        b["ad pages" if p["kind"] == "ad page" else "advertiser pages"] += 1
        if p["ad_id"]:
            b["ads"].add(p["ad_id"])
        b["first"] = min(filter(None, [b["first"], p["timestamp"][:8]]))
        b["last"] = max(filter(None, [b["last"], p["timestamp"][:8]]))
    summary = {"cut_short": cut, "accounts": len(per), "accounts_answered": sum(1 for x in per if x["answered"] == 200),
               "accounts_with_saved_pages": sum(1 for x in per if x["saved"]),
               "by_house": {h: {**v, "ads": len(v["ads"])} for h, v in sorted(by_house.items())},
               "calls_opened": len(opened), "calls_holding_the_house": sum(1 for x in opened if x["holds_the_house"]),
               "calls_holding_that_ad": sum(1 for x in opened if x["holds_that_ad"]),
               "houses_with_ads_in_saved_calls": sorted({x["house"] for x in opened if x["holds_the_house"]}),
               "accounts_table": per, "opened": opened}
    return pages, summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.archiveprobe")
    ap.add_argument("stage", choices=["run", "calls", "pages", "accounts"])
    ap.add_argument("--run", default=config.run_id())
    ap.add_argument("--films", type=int, default=4)
    ap.add_argument("--budget-min", type=float, default=36)
    a = ap.parse_args(argv)
    P = paths()
    c = Crawler(pause=1.5)
    start = time.monotonic()
    if a.stage in ("calls", "pages", "accounts"):
        DEADLINE[0] = start + a.budget_min * 60
        name = {"calls": "ad_calls", "pages": "ad_pages", "accounts": "house_pages"}[a.stage]
        fn = {"calls": calls_probe, "pages": pages_probe, "accounts": accounts_probe}[a.stage]
        try:
            rows, summary = fn(c)
            store.write_jsonl(config.DATA / "archiveprobe" / f"{name}.jsonl", rows)
        except Exception as e:
            summary = {"stopped": f"{type(e).__name__}: {str(e)[:300]}"}
        state = json.loads(P["state"].read_text()) if P["state"].exists() else {}
        ts = sorted(TIMES)
        key = name if a.stage != "calls" else "ad_calls" if "data_calls" in state else "data_calls"
        state[key] = {**summary, "run": a.run, "archive_calls": c.calls, "answered": len(ts), "updated_at": store.utc_now()}
        store.write_state(P["state"], state)
        store.append_jsonl(P["prov"], [{"run": a.run, key: summary}])
        print(json.dumps(state[key], indent=1)[:6000])
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
