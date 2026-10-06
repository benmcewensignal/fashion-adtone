"""Attention: daily Wikipedia page views for every house, as a consistent outside measure.

    python -m adtone.attention collect --run <id>
    python -m adtone.attention probe --run <id>

Success in the broad sense has no clean house-level series. Revenue is reported by house for only a
few of the eighteen. The Lyst Index changed its method in the first quarter of 2026 and counted
Chanel and Dior for the first time that quarter, so its ranks before and after the debuts do not
compare. Page views are counted the same way throughout and cover every house, every day. They
measure attention, which a debut produces whether or not it works.

English Wikipedia, all access methods, human users only. Each house has candidate article titles;
the first that exists, after redirects and skipping disambiguation pages, is used. If none does,
Wikipedia's own search supplies candidates that start with the house's name. Each title is recorded
with how it was found, and the probe flags search-found ones for a glance. The Wikimedia APIs are
free and need no key, only a descriptive User-Agent.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

from . import config, registry, store

PROJECT = "en.wikipedia"
START = date(2015, 7, 1)   # the first day of Wikimedia's per-article page views: a decade of shows for adtone.runway
API = "https://en.wikipedia.org/w/api.php"
VIEWS = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{project}/all-access/user/{title}/daily/{start}/{end}"
UA = "fashion-adtone research (attention series; contact via the repository)"
MIN_DAYS = 300             # the probe expects at least this many days per resolved house

# Candidates in order of preference. The first existing, non-disambiguation page wins.
ARTICLES = {
    "chanel": ["Chanel"],
    "dior": ["Dior", "Christian Dior (fashion house)"],
    "gucci": ["Gucci"],
    "balenciaga": ["Balenciaga"],
    "loewe": ["Loewe (fashion brand)", "Loewe (company)", "Loewe (brand)"],
    "bottega_veneta": ["Bottega Veneta"],
    "celine": ["Celine (brand)", "Céline (brand)", "Celine (fashion house)"],
    "fendi": ["Fendi"],
    "jil_sander": ["Jil Sander (brand)", "Jil Sander (company)", "Jil Sander"],
    "margiela": ["Maison Margiela"],
    "hermes": ["Hermès"],
    "louis_vuitton": ["Louis Vuitton"],
    "prada": ["Prada"],
    "miu_miu": ["Miu Miu"],
    "saint_laurent": ["Yves Saint Laurent (brand)", "Saint Laurent (brand)", "Saint Laurent Paris"],
    "burberry": ["Burberry"],
    "valentino": ["Valentino (fashion house)", "Valentino S.p.A."],
    "loro_piana": ["Loro Piana"],
    "versace": ["Versace"],
    "givenchy": ["Givenchy"],
    "tom_ford": ["Tom Ford (brand)", "Tom Ford (fashion house)"],
    "dries_van_noten": ["Dries Van Noten (brand)", "Dries Van Noten"],
    "alaia": ["Alaïa (brand)", "Alaïa"],
    "dolce_gabbana": ["Dolce & Gabbana"],
    "max_mara": ["Max Mara"],
    "brunello_cucinelli": ["Brunello Cucinelli (company)", "Brunello Cucinelli"],
    "zegna": ["Zegna (company)", "Ermenegildo Zegna"],
    "chloe": ["Chloé"],
}


def paths() -> dict[str, Path]:
    return {"dir": config.DATA / "attention", "state": config.STATE_DIR / "attention.json",
            "prov": config.PROV_DIR / "attention.jsonl"}


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s


def resolve(sess, wanted: dict[str, list[str]]) -> dict[str, str | None]:
    """House id -> the canonical title of its first existing, non-disambiguation candidate."""
    titles = sorted({t for ts in wanted.values() for t in ts})
    r = sess.get(API, params={"action": "query", "format": "json", "formatversion": "2", "redirects": "1",
                              "prop": "pageprops", "ppprop": "disambiguation", "titles": "|".join(titles)}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"title lookup failed with HTTP {r.status_code}")
    q = r.json().get("query", {})
    hop = {n["from"]: n["to"] for n in q.get("normalized", [])}
    hop.update({n["from"]: n["to"] for n in q.get("redirects", [])})
    good = {p["title"] for p in q.get("pages", []) if not p.get("missing") and not p.get("invalid")
            and "disambiguation" not in (p.get("pageprops") or {})}
    out: dict[str, str | None] = {}
    for house, cands in wanted.items():
        out[house] = None
        for t in cands:
            seen, cur = set(), t
            while cur in hop and cur not in seen:   # normalisation, then any redirect chain
                seen.add(cur)
                cur = hop[cur]
            if cur in good:
                out[house] = cur
                break
    return out


def search_candidates(sess, name: str, limit: int = 5) -> list[str]:
    """Wikipedia's own search, for a house whose listed titles all miss: titles that start with its name."""
    r = sess.get(API, params={"action": "query", "format": "json", "formatversion": "2", "list": "search",
                              "srsearch": f"{name} fashion house", "srnamespace": "0", "srlimit": str(limit)}, timeout=30)
    if r.status_code != 200:
        return []
    first = name.split()[0].lower()
    return [h["title"] for h in r.json().get("query", {}).get("search", []) if h["title"].lower().startswith(first)]


def resolve_all(sess, reg: registry.Registry) -> tuple[dict[str, str | None], dict[str, str]]:
    """Listed candidates first; for houses they miss, Wikipedia search. Returns titles and how each was found."""
    wanted = {h.id: ARTICLES.get(h.id, [h.name]) for h in reg.houses}
    titles = resolve(sess, wanted)
    how = {h: "listed" for h, t in titles.items() if t}
    missing = {h.id: h.name for h in reg.houses if not titles.get(h.id)}
    if missing:
        found = resolve(sess, {h: search_candidates(sess, name) for h, name in missing.items()})
        for h, t in found.items():
            if t:
                titles[h], how[h] = t, "search"
    return titles, how


def daily_views(sess, title: str, start: date, end: date) -> dict[str, int]:
    url = VIEWS.format(project=PROJECT, title=quote(title.replace(" ", "_"), safe=""),
                       start=start.strftime("%Y%m%d00"), end=end.strftime("%Y%m%d00"))
    r = sess.get(url, timeout=60)
    if r.status_code == 404:
        return {}
    if r.status_code != 200:
        raise RuntimeError(f"page views for {title!r} failed with HTTP {r.status_code}")
    out = {}
    for it in r.json().get("items", []):
        ts = it["timestamp"]
        out[f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"] = int(it["views"])
    return out


def collect(sess, reg: registry.Registry, run: str, start: date = START, end: date | None = None) -> dict:
    """Full refresh of every house's series (small: one request per house). Idempotent."""
    end = end or (datetime.now(timezone.utc).date() - timedelta(days=1))
    P = paths()
    titles, how = resolve_all(sess, reg)
    counts, failed = {}, {}
    for house, title in titles.items():
        if not title:
            continue
        try:
            series = daily_views(sess, title, start, end)
        except RuntimeError as e:
            failed[house] = str(e)
            continue
        store.write_jsonl(P["dir"] / f"{house}.jsonl",
                          [{"date": d, "views": v, "article": title, "project": PROJECT} for d, v in sorted(series.items())])
        counts[house] = len(series)
    unresolved = sorted(h for h, t in titles.items() if not t)
    state = {"run": run, "updated_at": store.utc_now(), "project": PROJECT, "start": start.isoformat(),
             "end": end.isoformat(), "titles": titles, "found_by": how, "unresolved": unresolved, "failed": failed,
             "days": counts}
    store.write_state(P["state"], state)
    store.append_jsonl(P["prov"], [{k: state[k] for k in ("run", "updated_at", "end", "unresolved")} | {"houses": len(counts)}])
    return state


def load_series(house: str) -> dict[date, int]:
    return {date.fromisoformat(r["date"]): int(r["views"]) for r in store.read_jsonl(paths()["dir"] / f"{house}.jsonl")}


def probe(run: str) -> list[str]:
    st = store.read_state(paths()["state"])
    if st.get("run") != run:
        return [f"no attention run {run} on file"]
    errs = [f"{h}: no Wikipedia article found, listed or by search" for h in st.get("unresolved", [])]
    errs += [f"{h}: {msg}" for h, msg in st.get("failed", {}).items()]
    errs += [f"{h}: only {n} days of page views" for h, n in st.get("days", {}).items() if n < MIN_DAYS]
    if not st.get("days"):
        errs.append("no house has any page views on file")
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.attention")
    ap.add_argument("stage", choices=["collect", "probe"])
    ap.add_argument("--run", default=config.run_id())
    args = ap.parse_args(argv)
    if args.stage == "collect":
        st = collect(session(), registry.load(), args.run)
        print({k: st[k] for k in ("end", "unresolved", "failed")} | {"houses": len(st["days"])})
        return 0
    errs = probe(args.run)
    st = store.read_state(paths()["state"])
    for h, way in sorted(st.get("found_by", {}).items()):
        if way == "search":   # worth a glance: chosen by Wikipedia's search, not from the list
            print(f"::warning::{h}: article found by search: {st['titles'][h]}")
    for e in errs:
        print(f"::error::{e}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
