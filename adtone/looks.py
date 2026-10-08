"""Runway looks: whether the houses' own collection pages, as the Wayback Machine holds them, carry the looks
of a show. A probe, before any collector is built.

    python -m adtone.looks probe [--season "2026 SS"] [--budget-min 22]

The Thread's shared description of the clothes needs the looks of each show, read the way the homepages are
read: pictures held in memory, answers and numbers kept, no picture stored. NOWFASHION's galleries sit
behind a bot check that turns the project's machines away, so the source would be the houses' own sites as
the archive captured them. Whether that works is not known, so this asks first.

For one season, on each house's domains (reference/brand_sites.csv), the archive's index is searched for
pages captured from the month of the shows to three months after whose address names the season. The
likeliest show or collection pages are opened (three per house), the pictures on each are counted, and
three are fetched to see that the archive kept them; their bytes are dropped at once. What is kept is the
addresses, the counts and a verdict per house, in data/thread/looks_probe.json.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date

import requests

from . import config, store
from .backcat import CDX, Crawler, _cdx_rows
from .homepages import REPLAY, final_url, page_images, sites

OUT = config.DATA / "thread" / "looks_probe.json"
PROV = config.PROV_DIR / "thread.jsonl"
PAGES_PER_HOUSE = 3
CHECKS_PER_PAGE = 3
PICTURE_CAP = 300          # picture addresses counted on one page
LOOKS_MIN = 15             # pictures on a page before it counts as carrying the looks
CDX_LIMIT = 3000

# words in an address that point to a show or a collection, and to what is not one
SHOW_WORDS = {"runway": 4, "fashion-show": 4, "fashion-shows": 4, "show": 3, "shows": 3, "defile": 4, "defiles": 4,
              "sfilata": 4, "catwalk": 4, "look": 3, "looks": 3, "lookbook": 3, "collection": 2, "collections": 2,
              "ready-to-wear": 2, "pret-a-porter": 2, "rtw": 2, "women": 1, "womens": 1, "woman": 1, "men": 1}
NOT_SHOW = ("product", "/p/", "/pd/", "shop", "cart", "checkout", "account", "search", "store", "careers", "press",
            "beauty", "fragrance", "parfum", "perfume", "makeup", "make-up", "skincare", "gift", "sitemap", "login")
LOCALE_HEAD = re.compile(r"^/(?:[a-z]{2,3}(?:[-_][a-z]{2,4})?/){1,2}", re.I)


def season_pattern(season: str, year: int) -> str:
    """A regular expression for an address that names the season, in the ways the houses write it:
    spring-summer-2026, ss26, printemps-ete-2026, fall-winter-2025, fw25, automne-hiver-2025 and so on."""
    yy = f"{year % 100:02d}"
    if season == "SS":
        words = r"spring[-_]?summer|summer|spring|printemps[-_]?ete|primavera[-_]?estate|ss|pe"
    else:
        words = r"(?:fall|autumn)[-_]?winter|winter|fall|autumn|automne[-_]?hiver|autunno[-_]?inverno|aw|fw|ah|ai"
    return rf"(?i).*(?:{words})[-_]?(?:20)?{yy}(?![0-9]).*"


def capture_window(season: str, year: int) -> tuple[date, date]:
    """From the month of the shows to three months after, when a show's pages are made and first captured:
    spring-summer is shown the September and October before its year, autumn-winter in February and March
    of its year. A short window keeps the archive's index quick to search."""
    if season == "SS":
        return date(year - 1, 9, 1), date(year - 1, 12, 31)
    return date(year, 2, 1), date(year, 5, 31)


def score(url: str) -> float:
    """How likely an address is a show or collection page: show words count for, shop and beauty words
    against, and a British, American or English page is preferred, as on the homepages."""
    from urllib.parse import urlparse
    from .homepages import _locale_rank
    path = urlparse(url).path.lower()
    if any(w in path for w in NOT_SHOW):
        return -1.0
    words = {t for t in re.split(r"[^a-z0-9]+", path) if t}
    s = float(sum(v for w, v in SHOW_WORDS.items() if "-" not in w and w in words))
    s += sum(v for w, v in SHOW_WORDS.items() if "-" in w and w in path)
    if re.search(r"\d{6,}", path):          # a long run of digits is usually a product code
        s -= 2
    return s + _locale_rank(path) / 4


def _key(url: str) -> str:
    from urllib.parse import urlparse
    p = urlparse(url)
    return LOCALE_HEAD.sub("/", p.path.lower()).rstrip("/")


def candidates(rows: list[list[str]], k: int = PAGES_PER_HOUSE) -> list[dict]:
    """The k likeliest pages, one per address once the country and language are set aside, each with its
    latest capture in the window (the most complete gallery)."""
    best: dict[str, dict] = {}
    for row in rows:
        ts, url = row[0], row[1]
        s = round(score(url), 2)
        if s <= 0:
            continue
        key = _key(url)
        cur = best.get(key)
        if cur is None or (s, ts) > (cur["score"], cur["ts"]):
            best[key] = {"url": url, "ts": ts, "score": s}
    return sorted(best.values(), key=lambda c: (-c["score"], c["url"]))[:k]


def index(c: Crawler, domain: str, pattern: str, start: date, end: date) -> tuple[list | None, str | None]:
    """The archive's index of the domain's pages in the window whose address matches, or None and what went
    wrong. A slow index is asked again month by month."""
    q = {"url": domain, "matchType": "domain", "output": "json", "fl": "timestamp,original",
         "filter": ["statuscode:200", "mimetype:text/html", f"original:{pattern}"], "collapse": "urlkey",
         "limit": str(CDX_LIMIT)}
    try:
        r = c.get(CDX, {**q, "from": start.strftime("%Y%m%d"), "to": end.strftime("%Y%m%d")})
        if r.status_code == 200:
            return _cdx_rows(r)[1:], None
        why = f"HTTP {r.status_code}: {(r.text or '')[:120]}"
    except requests.RequestException as e:
        why = type(e).__name__
    rows, errors = [], 0
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        nxt = (y + (m == 12), m % 12 + 1)
        try:
            r = c.get(CDX, {**q, "from": f"{y}{m:02d}01", "to": f"{nxt[0]}{nxt[1]:02d}01"})
            if r.status_code == 200:
                rows += _cdx_rows(r)[1:]
            else:
                errors += 1
        except requests.RequestException:
            errors += 1
        y, m = nxt
    if errors and not rows:
        return None, why
    return rows, None


def check_page(c: Crawler, ts: str, url: str, checks: int = CHECKS_PER_PAGE) -> dict:
    """Open one capture, count its pictures and fetch a few to see that the archive kept them."""
    try:
        r = c.get(REPLAY.format(ts=ts, url=url))
    except requests.RequestException as e:
        return {"url": url, "ts": ts, "status": type(e).__name__}
    ctype = (getattr(r, "headers", None) or {}).get("Content-Type", "")
    if r.status_code != 200 or "html" not in ctype:
        return {"url": url, "ts": ts, "status": f"HTTP {r.status_code}"}
    page = final_url(getattr(r, "url", "") or "", url)
    pics = page_images(r.text, page, ts, cap=PICTURE_CAP)
    title = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.I | re.S)
    out = {"url": url, "ts": ts, "status": "ok", "title": (title.group(1).strip()[:100] if title else ""),
           "pictures": len(pics), "checked": 0, "kept": 0}
    for u in pics[1:1 + checks]:                      # the share image is often the campaign; the gallery follows
        out["checked"] += 1
        try:
            g = c.get(u)
            if g.status_code == 200 and (g.headers.get("Content-Type", "").startswith("image")) and len(g.content) > 2000:
                out["kept"] += 1
        except requests.RequestException:
            pass
    return out


def verdict(h: dict) -> str:
    if h.get("index_error"):
        return "the index did not answer"
    if not h.get("candidates"):
        return "no page naming the season"
    pages = [p for p in h.get("pages", []) if p.get("status") == "ok"]
    if any(p["pictures"] >= LOOKS_MIN and p["kept"] >= 2 for p in pages):
        return "looks found"
    if pages:
        return "pages found, few pictures kept"
    return "pages found, none opened"


FINAL = ("looks found", "no page naming the season", "pages found, few pictures kept")


def probe_house(c: Crawler, house: str, domains: list[str], pattern: str, start: date, end: date, deadline: float,
                clock=time.monotonic) -> dict:
    h: dict = {"domains": domains, "window": [start.isoformat(), end.isoformat()], "candidates": [], "pages": []}
    rows: list = []
    for d in domains:
        if clock() > deadline:
            return {"verdict": "not reached in the time budget"}
        got, why = index(c, d, pattern, start, end)
        if got is None:
            h.setdefault("index_errors", {})[d] = why
            continue
        rows += got
    if not rows and h.get("index_errors") and len(h["index_errors"]) == len(domains):
        h["index_error"] = True
    h["season_pages"] = len({_key(r[1]) for r in rows})
    h["candidates"] = candidates(rows)
    for cand in h["candidates"]:
        if clock() > deadline:
            break
        h["pages"].append(check_page(c, cand["ts"], cand["url"]))
    h["verdict"] = verdict(h)
    return h


def probe(season: str = "2026 SS", houses: list[str] | None = None, budget_min: float = 22, c: Crawler | None = None,
          clock=time.monotonic, workers: int = 4) -> dict:
    """Each house on its own, several at a time, each with its own polite crawler. A run that stops at its
    time budget is continued by the next: houses with a final verdict for the same season are kept."""
    from concurrent.futures import ThreadPoolExecutor
    import threading
    year, s = int(season.split()[0]), season.split()[1]
    start, end = capture_window(s, year)
    pattern = season_pattern(s, year)
    deadline = clock() + budget_min * 60
    doms = sites()
    prev = json.loads(OUT.read_text()) if OUT.exists() else {}
    kept = {h: v for h, v in prev.get("houses", {}).items() if v.get("verdict") in FINAL} if prev.get("season") == season else {}
    out = {"generated_at": store.utc_now(), "season": season, "window": [start.isoformat(), end.isoformat()],
           "pattern": pattern, "houses": dict(kept), "note": "addresses and counts only; no picture is kept or read"}
    todo = [h for h in (houses or sorted(doms)) if h not in kept]
    lock, calls = threading.Lock(), []

    def one(house):
        cc = c or Crawler(pause=2.0)
        h = probe_house(cc, house, doms.get(house, []), pattern, start, end, deadline, clock)
        with lock:
            out["houses"][house] = h
            calls.append(cc.calls)
            _write(out)

    with ThreadPoolExecutor(max_workers=1 if c is not None else workers) as ex:
        list(ex.map(one, todo))
    out["houses"] = dict(sorted(out["houses"].items()))
    out["summary"] = dict(sorted(_count(v.get("verdict") for v in out["houses"].values()).items()))
    _write(out)
    store.append_jsonl(PROV, [{"at": store.utc_now(), "event": "looks probe", "season": season,
                               "summary": out["summary"], "requests": c.calls if c is not None else sum(calls)}])
    return out


def _count(xs) -> dict:
    d: dict = {}
    for x in xs:
        d[x] = d.get(x, 0) + 1
    return d


def _write(out: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m adtone.looks")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--season", default="2026 SS")
    p.add_argument("--budget-min", type=float, default=22)
    p.add_argument("--houses", nargs="*")
    p.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    out = probe(a.season, a.houses, a.budget_min, workers=a.workers)
    print(f"looks probe {a.season}: {out.get('summary')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
