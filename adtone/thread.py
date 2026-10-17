"""The Thread: every show of the panel's houses, one row each, from 2015, with how much attention it drew and
how the news wrote about it, each set against the other shows of the same season, so that a house's line
shows where it stood season by season.

    python -m adtone.thread calendar     # the shows, from NOWFASHION's brand listings   -> data/thread/shows.jsonl
    python -m adtone.thread ingest --house dior --file dior.txt --read 2026-10-08
                                         # a listing read another way, merged in
    python -m adtone.thread build        # heat and tone for every show                   -> data/results/thread.json

The calendar on file was read on 8 October 2026 through Claude's own fetcher, which the site's terms admit;
its bot check turns GitHub's machines away, so `calendar` from a workflow mostly finds nothing and keeps what
is on file. Its dates match the 86 verified shows in reference/shows.csv on the day for 77 and within a day
for 80; 5 of those shows are not listed. A listed date outside the weeks its kind of show takes place (4 of
900, mostly the day a collection was added to the site) is flagged and left out, unless a verified show of
the house falls within a day of it, as Gucci's Love Parade in Los Angeles in November 2021 does. A verified show the
listings do not hold at all (The Row's Paris shows since 2022, Jacquemus's away from the calendar, 23 on 17 October
2026) joins the calendar as the verified record gives it, marked as verified rather than listed.

The strands, all from sources the project already holds:
  heat       the jump in English Wikipedia views around the show: the peak from the day before to three days
             after, over the mean from 60 to 10 days before (log views), the windows of Amendment 2, section 13,
             on the views with a renamed article's earlier titles joined (adtone/wikiviews); no heat where
             that mean is under 20 views a day, too few to read a jump from (Zegna before 2021: its
             English article draws the brand's traffic only from 2021, and "Ermenegildo Zegna" is now a
             disambiguation page, so no earlier title can be joined);
  surprise   that jump less the mean jump of the house's earlier shows of the same kind, once it has two;
  press      the jump in the number of news articles (GDELT), same windows;
  tone       the news tone on the show day and the three days after, less its mean from 60 to 10 days before
             (GDELT);
  lasting    the attention that lasts: mean log views from 30 to 120 days after the show, over the same
             baseline, less the median house's change over the same days; none until the 120 days are on file;
  momentum   the trend the house brought into the show: mean log views from 90 to 61 days before, less the
             mean from 150 to 121 days before. It stops short of the baseline weeks that lasting attention is
             measured from, so a baseline that happens to run high cannot raise one and lower the other.
Each is also given as a z-score among the shows of the same season (category, season and year). Each
show's collection is then followed through the stages that come after it, on fixed windows, in
data/thread/collections.jsonl (see `collections` below). Descriptive throughout.

NOWFASHION's terms (nowfashion.com/llms.txt and robots.txt) allow reading and citing its pages and forbid
using them for training; the Thread reads only titles and dates from its brand listings and keeps the link
to each show's page. Its write-ups are not used: they carry no byline and were updated in bulk in
September 2026, so they cannot stand for how a show was received at the time.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import date

import numpy as np

from . import config, registry, store

SITE = "https://nowfashion.com"
UA = "Focal research (https://github.com/benmcewensignal/fashion-adtone)"
PAUSE = 2.0                         # seconds between requests to one site
LISTING_CAP = 100                   # a brand listing that returns exactly this many may be cut short
DIR = config.DATA / "thread"
RESULTS = config.RESULTS_DIR / "thread.json"
PROV = config.PROV_DIR / "thread.jsonl"
SHOWS_REF = config.ROOT / "reference" / "shows.csv"
FIRST_YEAR = 2015                   # English Wikipedia daily views start in July 2015
MIN_SEASON = 5                      # shows with a value before a season's z-scores are given
MIN_BASE_VIEWS = 20                 # mean daily views from 60 to 10 days before a show, below which no heat
MOMENTUM_FROM = (-150, -121)        # momentum: these days to the next, both before the baseline weeks (-60 to -10)
MOMENTUM_LATE = (-90, -61)

# NOWFASHION's brand page names for each house, tried in order
SLUGS = {
    "chanel": ["chanel"], "dior": ["dior", "christian-dior"], "gucci": ["gucci"], "balenciaga": ["balenciaga"],
    "loewe": ["loewe"], "bottega_veneta": ["bottega-veneta"], "celine": ["celine"], "fendi": ["fendi"],
    "jil_sander": ["jil-sander"], "margiela": ["maison-margiela", "maison-martin-margiela"], "hermes": ["hermes"],
    "louis_vuitton": ["louis-vuitton"], "prada": ["prada"], "miu_miu": ["miu-miu"],
    "saint_laurent": ["saint-laurent", "yves-saint-laurent"], "burberry": ["burberry", "burberry-prorsum"],
    "valentino": ["valentino"], "loro_piana": ["loro-piana"], "chloe": ["chloe"], "versace": ["versace"],
    "givenchy": ["givenchy"], "tom_ford": ["tom-ford"], "dries_van_noten": ["dries-van-noten"],
    "alaia": ["alaia", "azzedine-alaia"], "dolce_gabbana": ["dolce-gabbana", "dolce-and-gabbana"],
    "max_mara": ["max-mara"], "brunello_cucinelli": ["brunello-cucinelli"], "zegna": ["zegna", "ermenegildo-zegna"],
    # the wider field, 15 October 2026
    "armani": ["giorgio-armani"], "moncler": ["moncler"], "ferragamo": ["ferragamo", "salvatore-ferragamo"],
    "jacquemus": ["jacquemus"], "the_row": ["the-row"], "mcqueen": ["alexander-mcqueen", "mcqueen"],
    "balmain": ["balmain"], "schiaparelli": ["schiaparelli"], "lanvin": ["lanvin"],
    "stella_mccartney": ["stella-mccartney"], "comme_des_garcons": ["comme-des-garcons"], "issey_miyake": ["issey-miyake"],
}
# a line of the house that the site lists on a brand page of its own, read as well as the house's page
ALSO = {"armani": ["giorgio-armani-prive"]}
CATEGORIES = [("ready to wear", "rtw"), ("men women", "rtw"), ("couture", "couture"), ("menswear", "men"),
              ("pre fall", "prefall"), ("resort", "resort"), ("cruise", "resort")]   # a co-ed show counts as ready-to-wear
SEASONS = [("spring summer", "SS"), ("fall winter", "AW"), ("autumn winter", "AW"), ("pre fall", "PF"),
           ("resort", "RE"), ("cruise", "RE")]
LINK = re.compile(r"\[(?P<title>[^\]]+)\]\((?P<url>[^)\s]+)\)")
DATE = re.compile(r"\b(?P<date>(?:19|20)\d{2}-\d{2}-\d{2})\b")


def _log(event: str, **kw) -> None:
    store.append_jsonl(PROV, [{"at": store.utc_now(), "event": event, **kw}])


def _plain(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def parse_listing(text: str) -> list[dict]:
    """A brand listing's show entries: '- [Title](url) — YYYY-MM-DD'."""
    out = []
    for line in (text or "").splitlines():
        m, d = LINK.search(line), DATE.search(line)
        if not (m and d) or not re.search(r"\b(19|20)\d{2}\b", m["title"]):
            continue
        url = m["url"]
        if url.startswith("/"):
            url = SITE + url
        if "/brand/" in url or "/media/" in url:
            continue
        out.append({"title": m["title"].strip(), "url": url, "date": d["date"]})
    return out


def parse_title(title: str) -> dict:
    """Category, season, year and city from a title such as 'Dior Ready To Wear Spring Summer 2018 Paris'."""
    t = _plain(title)
    cat = next((c for k, c in CATEGORIES if f" {k} " in f" {t} "), "other")
    season = next((s for k, s in SEASONS if f" {k} " in f" {t} "), None)
    if cat in ("prefall", "resort"):
        season = {"prefall": "PF", "resort": "RE"}[cat]
    m = re.search(r"\b(19|20)\d{2}\b", t)
    year = int(m.group(0)) if m else None
    city = re.sub(r"\s+\d+$", "", t[m.end():].strip()).title() if m else ""     # 'milan 2': the site's second page
    return {"category": cat, "season": season, "year": year, "city": city or None}


def _get(sess, url: str, sleep=time.sleep):
    for attempt in range(3):
        try:
            r = sess.get(url, timeout=30, headers={"User-Agent": UA, "Accept": "text/markdown, text/plain, */*"})
        except Exception as e:      # a dropped connection is tried again; anything else is reported by the caller
            if attempt == 2:
                return None, f"{e.__class__.__name__}"
            sleep(5 * (attempt + 1))
            continue
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            sleep(10 * (attempt + 1))
            continue
        return r, None
    return None, "no answer"


def vouched(row: dict, ref: list[dict]) -> dict | None:
    """The verified show of the same house within a day of a listed date, if there is one."""
    try:
        d = date.fromisoformat(row["date"])
    except ValueError:
        return None
    near = [v for v in ref if v["house"] == row["house"] and abs((date.fromisoformat(v["date"]) - d).days) <= 1]
    return near[0] if near else None


SEASON_REDATE_DAYS = 45


def season_label(row: dict) -> str | None:
    """A listing's season as the verified shows write it: SS26, AW25, SS27 men, AW26 couture."""
    if not row.get("year") or not row.get("season") or not str(row["year"]).isdigit():
        return None
    lab = f"{row['season']}{int(row['year']) % 100:02d}"
    return lab if row.get("category") == "rtw" else f"{lab} {row.get('category')}"


def redated(row: dict, ref: list[dict], days: int = 7) -> dict:
    """A listing a few days off a verified show of the same house takes the verified date: the listing's date is
    then a gallery timestamp, not the show (NOWFASHION dates Celine's SS26 page 30 September 2025; the show was on
    5 October, and a jump read on the 30th misses it). Left alone within a day of a verified show, or more than
    `days` from any; the listed date is kept as `listed_date`. A verified show of the same house and season held
    away from the usual week takes the listing's place up to SEASON_REDATE_DAYS off, with its city (NOWFASHION
    files Zegna's summer 2026 show, held at the Dubai Opera on 11 June 2025, under Milan on 23 June)."""
    try:
        d = date.fromisoformat(row["date"])
    except (KeyError, ValueError):
        return row
    lab = season_label(row)
    same = sorted((abs((date.fromisoformat(v["date"]) - d).days), v["date"], v.get("city") or "") for v in ref
                  if lab and v["house"] == row.get("house") and (v.get("season") or "").strip() == lab
                  and (v.get("kind") or "show") in ("show", "debut"))
    if same and days < same[0][0] <= SEASON_REDATE_DAYS:
        out = {**row, "date": same[0][1], "listed_date": row["date"]}
        if same[0][2] and same[0][2] != row.get("city"):
            out.update(city=same[0][2], listed_city=row.get("city"))
        return out
    gaps = sorted((abs((date.fromisoformat(v["date"]) - d).days), v["date"]) for v in ref if v["house"] == row.get("house"))
    if not gaps or gaps[0][0] <= 1 or gaps[0][0] > days:
        return row
    return {**row, "date": gaps[0][1], "listed_date": row["date"]}


REF_SEASON = re.compile(r"^(SS|AW)(\d{2})(?: (men|couture))?$")


def unlisted(rows: list[dict], ref: list[dict], days: int = 7) -> list[dict]:
    """The verified shows the listings do not hold: none of the house's listings within `days` of it, and none of the
    same season and kind at any date (The Row's Paris shows since 2022, Jacquemus's shows away from the calendar). Each
    joins the calendar as the verified record gives it, marked as such; a verified season the code cannot read (a
    presentation filed under the nearest season) is left out."""
    by = defaultdict(list)
    for r in rows:
        by[r.get("house")].append(r)
    out = []
    for v in ref:
        m = REF_SEASON.match((v.get("season") or "").strip())
        try:
            d = date.fromisoformat(v["date"])
        except (KeyError, ValueError):
            continue
        if not m or (v.get("kind") or "show") not in ("show", "debut"):
            continue
        season, year, kind = m.group(1), 2000 + int(m.group(2)), m.group(3) or "rtw"
        mine = by.get(v["house"], [])
        near = any(abs((date.fromisoformat(r["date"]) - d).days) <= days for r in mine if len(r.get("date") or "") == 10)
        same = any(str(r.get("year")) == str(year) and r.get("season") == season and r.get("category") == kind for r in mine)
        if near or same:
            continue
        row = {"house": v["house"], "date": v["date"], "year": year, "season": season, "category": kind,
               "city": v.get("city") or "", "title": f"{v['house']} {kind} {season.lower()} {year} (a checked show the listings do not hold)",
               "url": (v.get("source") or "").split(" | ")[0].strip(), "source": "verified", "read": None, "date_doubtful": False}
        out.append(row)
        by[v["house"]].append(row)
    return out


def doubtful(row: dict, ref: list[dict]) -> bool:
    """A listed date outside the weeks its kind of show takes place, unless a verified show of the house falls
    within a day of it: a show held off the calendar (Gucci's Love Parade in Los Angeles in November 2021) is
    kept once its date is verified."""
    return not in_window(row) and vouched(row, ref) is None


def _rows(house: str, entries: list[dict], read: str) -> list[dict]:
    rows, seen, ref = [], set(), _reference()
    for e in entries:
        url = e["url"].removesuffix(".md")
        if url in seen:
            continue
        seen.add(url)
        row = {"house": house, "date": e["date"], **parse_title(e["title"]), "title": e["title"], "url": url,
               "source": "nowfashion", "read": read}
        row["date_doubtful"] = doubtful(row, ref)
        rows.append(row)
    return rows


def _merge(new: dict[str, list[dict]]) -> list[dict]:
    """New listings replace a house's rows; a house with nothing new keeps what is on file."""
    path = DIR / "shows.jsonl"
    old = store.read_jsonl(path) if path.exists() else []
    keep = [r for r in old if r["house"] not in new]
    ref = _reference()
    rows = [{**r, "date_doubtful": doubtful(r, ref)} for r in keep + [r for rs in new.values() for r in rs]]
    rows.sort(key=lambda r: (r["house"], r["date"], r["category"], r["url"]))
    DIR.mkdir(parents=True, exist_ok=True)
    store.write_jsonl(path, rows)
    check = check_calendar(rows)
    (DIR / "calendar_check.json").write_text(json.dumps(check, indent=1) + "\n", encoding="utf-8")
    return rows


def calendar(sess=None, sleep=time.sleep, houses: list[str] | None = None) -> dict:
    """Every show NOWFASHION lists for each house, from its brand page, kept once per page. Fetched from here
    (GitHub's machines meet the site's bot check, so this mostly finds nothing); a house the fetch misses
    keeps the rows on file, which may have been read through Claude's own fetcher (ingest)."""
    import requests
    sess = sess or requests.Session()
    reg = registry.load()
    new, per, notes = {}, {}, []
    for h in reg.houses:
        if houses and h.id not in houses:
            continue
        found = None
        for slug in SLUGS.get(h.id, [_plain(h.name).replace(" ", "-")]):
            r, err = _get(sess, f"{SITE}/brand/{slug}.md", sleep)
            sleep(PAUSE)
            if r is not None and r.status_code == 200:
                entries = parse_listing(r.text)
                if entries:
                    found = (slug, entries)
                    break
            if err:
                notes.append(f"{h.id}: {slug}: {err}")
            elif r is not None:     # what came back instead of a listing, so a failure says why
                kind = (getattr(r, "headers", None) or {}).get("content-type", "?")
                head = re.sub(r"\s+", " ", (r.text or "")[:160])
                notes.append(f"{h.id}: {slug}: HTTP {r.status_code}, {kind}, {len(r.text or '')} chars: {head}")
        if not found:
            per[h.id] = {"slug": None, "listed": 0}
            continue
        slug, entries = found
        cut = len(entries) == LISTING_CAP
        for extra in ALSO.get(h.id, []):     # a line listed on a page of its own: its shows join the house's
            r, err = _get(sess, f"{SITE}/brand/{extra}.md", sleep)
            sleep(PAUSE)
            more = parse_listing(r.text) if r is not None and r.status_code == 200 else []
            if not more:     # without the line's page the house's rows on file are kept whole, not cut short
                notes.append(f"{h.id}: {extra}: {err or 'no listing'}; the rows on file are kept")
                found = None
                break
            seen = {e["url"] for e in entries}
            entries = entries + [e for e in more if e["url"] not in seen]
            cut = cut or len(more) == LISTING_CAP
        if not found:
            per[h.id] = {"slug": None, "listed": 0}
            continue
        per[h.id] = {"slug": slug, "listed": len(entries), "possibly_cut": cut}
        new[h.id] = _rows(h.id, entries, store.utc_now()[:10])
    rows = _merge(new)
    check = check_calendar(rows)
    out = {"shows": len(rows), "fetched_houses": len(new), "houses": len({r["house"] for r in rows}), "per_house": per,
           "notes": notes, "check": {k: v for k, v in check.items() if k != "rows"}}
    _log("calendar", **out)
    return out


def ingest(house: str, text: str, read: str, how: str) -> dict:
    """A house's listing read some other way (lines of 'YYYY-MM-DD | Title | URL'), merged in like a fetch."""
    entries = []
    for line in (text or "").splitlines():
        parts = [x.strip() for x in line.split("|")]
        if len(parts) >= 3 and DATE.fullmatch(parts[0]):
            entries.append({"date": parts[0], "title": parts[1], "url": parts[2]})
        elif len(parts) == 2 and DATE.fullmatch(parts[0]) and re.fullmatch(r"[a-z0-9-]+", parts[1]):
            # 'YYYY-MM-DD | slug': the page's address names the house, kind, season, year and city
            entries.append({"date": parts[0], "title": parts[1].replace("-", " "), "url": f"{SITE}/{parts[1]}"})
    rows = _rows(house, entries, read)
    for r in rows:
        r["how"] = how
    _merge({house: rows})
    _log("ingest", house=house, rows=len(rows), doubtful=sum(r["date_doubtful"] for r in rows), how=how)
    return {"house": house, "rows": len(rows), "doubtful": sum(r["date_doubtful"] for r in rows)}


# The weeks in which each kind of show takes place, as (first, last) month-day, and the year relative to the
# season's year. A listed date outside them is most likely the day a collection was added to the site.
WINDOWS = {("rtw", "SS"): ((8, 15), (10, 31), -1), ("rtw", "AW"): ((1, 15), (4, 15), 0),
           ("couture", "SS"): ((1, 1), (2, 28), 0), ("couture", "AW"): ((6, 15), (7, 31), 0),
           ("men", "SS"): ((5, 15), (7, 20), -1), ("men", "AW"): ((1, 1), (2, 28), 0)}


def in_window(r: dict) -> bool:
    """Is the listed date inside the weeks this kind of show takes place? Kinds without fixed weeks (pre-fall,
    resort, other) are not judged."""
    w = WINDOWS.get((r.get("category"), r.get("season")))
    if not w or not r.get("year"):
        return True
    (m0, d0), (m1, d1), off = w
    y = r["year"] + off
    try:
        d = date.fromisoformat(r["date"])
    except ValueError:
        return False
    return date(y, m0, d0) <= d <= date(y, m1, d1)


def _reference(path=None) -> list[dict]:
    with open(path or SHOWS_REF, encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("verified", "").strip().lower() == "true"]


def check_calendar(rows: list[dict], ref: list[dict] | None = None) -> dict:
    """NOWFASHION's dates against the shows verified from official calendars: for each verified show, the
    nearest listing of the same house within a week, and how many days apart they are."""
    ref = _reference() if ref is None else ref
    by = defaultdict(list)
    for r in rows:
        by[r["house"]].append(date.fromisoformat(r["date"]))
    out = []
    for v in ref:
        d = date.fromisoformat(v["date"])
        near = sorted(by.get(v["house"], []), key=lambda x: abs((x - d).days))
        gap = (near[0] - d).days if near and abs((near[0] - d).days) <= 7 else None
        out.append({"house": v["house"], "date": v["date"], "season": v.get("season"), "gap_days": gap})
    n = len(out)
    gaps = Counter(o["gap_days"] for o in out)
    return {"verified_shows": n, "same_day": gaps.get(0, 0), "one_day_apart": gaps.get(1, 0) + gaps.get(-1, 0),
            "further": sum(c for g, c in gaps.items() if g is not None and abs(g) > 1),
            "not_listed": gaps.get(None, 0), "rows": out}


def season_key(r: dict) -> str | None:
    if not r.get("year") or not r.get("season"):
        return None
    return f"{r['year']} {r['season']} {r['category']}"


def main_category(rows: list[dict]) -> dict[str, str]:
    """Each house's main kind of show since 2015: the category it shows most, of ready-to-wear, menswear
    and couture (ready-to-wear on a tie)."""
    c = defaultdict(Counter)
    for r in rows:
        if r["category"] in ("rtw", "men", "couture") and int(r["date"][:4]) >= FIRST_YEAR:
            c[r["house"]][r["category"]] += 1
    order = {"rtw": 0, "men": 1, "couture": 2}
    return {h: sorted(cc.items(), key=lambda kv: (-kv[1], order[kv[0]]))[0][0] for h, cc in c.items()}


def _z(values: dict[int, float]) -> dict[int, float]:
    if len(values) < MIN_SEASON:
        return {}
    v = np.array(list(values.values()), float)
    sd = v.std()
    return {k: (round(float((x - v.mean()) / sd), 3) if sd > 0 else 0.0) for k, x in values.items()}


def build(shows: list[dict] | None = None, attention=None, press=None, sources: dict | None = None) -> dict:
    """Heat, surprise, press, tone, lasting attention and momentum for every show from 2015, each also as a
    z-score within its season."""
    from datetime import timedelta
    from . import press as press_mod, wikiviews
    from .runway import Panel, PressPanel, logged
    if shows is None:
        # the flags are judged again against the verified shows on file now, which may have grown since the read
        ref = _reference()
        shows = [redated(s, ref) for s in store.read_jsonl(DIR / "shows.jsonl")]
        shows = [{**s, "date_doubtful": doubtful(s, ref)} if s.get("year") else s for s in shows]
        # a show kept off the calendar takes its place from the verified record: the listing's title can
        # carry the usual city (NOWFASHION files the Love Parade under Milan)
        for s in shows:
            v = None if not s.get("year") or in_window(s) else vouched(s, ref)
            if v and v.get("city"):
                s["city"] = v["city"]
        # the verified shows the listings never held join the calendar as the verified record gives them
        shows += unlisted([s for s in shows if s.get("year")], ref)
    n_doubtful = sum(1 for s in shows if s.get("date_doubtful"))
    shows = [s for s in shows if s.get("year") and int(s["date"][:4]) >= FIRST_YEAR and not s.get("date_doubtful")]
    houses = sorted({s["house"] for s in shows})
    # English views with a renamed article's earlier titles joined, so a house whose article moved keeps its past
    attention = attention if attention is not None else {h: wikiviews.load_series(h, "en") for h in houses}
    series = {h: logged(v) for h, v in attention.items() if v}
    if not series:
        return {"note": "no page views on file"}
    panel = Panel(series)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)   # empty windows at the edges of the record
        momentum = panel._mean(MOMENTUM_LATE) - panel._mean(MOMENTUM_FROM)
    press = press if press is not None else {h: press_mod.load_series(h) for h in houses}
    pp = PressPanel(panel, {h: v for h, v in press.items() if v})
    main = main_category(shows)
    rows = []
    def whole_peak(series: dict, d: date) -> bool:
        """Every day from the day before to three days after is on file: a show the data has not yet caught
        up with gets no value rather than a peak taken over part of its window."""
        return all(d + timedelta(days=k) in series for k in range(-1, 4))
    def base_views(series: dict, d: date) -> float:
        """Mean daily views from 60 to 10 days before, the baseline's own days, in views rather than logs."""
        b = [series[d + timedelta(days=k)] for k in range(-60, -9) if d + timedelta(days=k) in series]
        return sum(b) / len(b) if b else 0.0
    thin = 0
    for s in sorted(shows, key=lambda s: (s["house"], s["date"])):
        d = date.fromisoformat(s["date"])
        i, t = panel.row.get(s["house"]), (d - panel.start).days
        heat, missing, lasting, mom = None, None, None, None
        views = attention.get(s["house"]) or {}
        if i is not None and 0 <= t < len(panel.days) and whole_peak(views, d):
            v = panel.spike[i, t]
            heat = None if np.isnan(v) else round(float(v), 4)
            if heat is not None and base_views(views, d) < MIN_BASE_VIEWS:
                heat, thin, missing = None, thin + 1, "thin views"
        if i is not None and 0 <= t < len(panel.days) and missing != "thin views" and base_views(views, d) >= MIN_BASE_VIEWS:
            la, mo = panel.lasting[i, t], momentum[i, t]
            lasting = None if np.isnan(la) else round(float(la), 4)
            mom = None if np.isnan(mo) else round(float(mo), 4)
        if heat is None and missing is None:
            # why there is no heat: no views, the days after the show not yet on file, views that start too
            # late for the baseline, or days missing in between
            missing = ("no views" if not views else "not yet on file" if d + timedelta(days=3) > max(views)
                       else "before the views" if d - timedelta(days=60) < min(views) else "days missing")
        p = pp.at(s["house"], d) if i is not None and whole_peak(press.get(s["house"]) or {}, d) else {}
        rows.append({"house": s["house"], "date": s["date"], "season": season_key(s), "category": s["category"],
                     "city": s.get("city"), "main": s["category"] == main.get(s["house"]), "url": s.get("url"),
                     "heat": heat, "heat_missing": missing, "lasting": lasting, "momentum": mom,
                     "press": None if p.get("press_spike") is None else round(p["press_spike"], 4),
                     "tone": None if p.get("reception") is None else round(p["reception"], 4)})
    # surprise: the jump less the mean jump of the house's earlier shows of the same kind, once it has two
    prior = defaultdict(list)
    for r in rows:
        past = prior[(r["house"], r["category"])]
        r["surprise"] = round(r["heat"] - float(np.mean(past)), 4) if r["heat"] is not None and len(past) >= 2 else None
        if r["heat"] is not None:
            past.append(r["heat"])
    # z-scores within each season: among every show of that kind, season and year, whichever house's main kind
    # it is, so a house that mainly shows menswear is set against the other menswear shows of its season
    by_season = defaultdict(list)
    for k, r in enumerate(rows):
        if r["season"]:
            by_season[r["season"]].append(k)
    for strand in ("heat", "surprise", "press", "tone", "lasting"):
        for ks in by_season.values():
            z = _z({k: rows[k][strand] for k in ks if rows[k][strand] is not None})
            for k in ks:
                rows[k][f"{strand}_z"] = z.get(k)
    for r in rows:
        for strand in ("heat", "surprise", "press", "tone", "lasting"):
            r.setdefault(f"{strand}_z", None)
    lines = defaultdict(list)
    for r in rows:
        if r["main"]:
            lines[r["house"]].append({k: r[k] for k in ("date", "season", "heat_z", "surprise_z", "press_z", "tone_z",
                                                        "lasting_z")})
    cover = {strand: sum(1 for r in rows if r["main"] and r[strand] is not None)
             for strand in ("heat", "surprise", "press", "tone", "lasting", "momentum")}
    out = {"generated_at": store.utc_now(),
           "status": "descriptive",
           "shows": len(rows), "left_out_doubtful_dates": n_doubtful, "left_out_thin_views": thin,
           "main_shows": sum(r["main"] for r in rows), "houses": len(lines),
           "seasons": len(by_season), "with_value": cover, "main_category": main,
           "first": min((r["date"] for r in rows), default=None), "last": max((r["date"] for r in rows), default=None),
           "lines": dict(lines), "rows": rows}
    out["collections"] = collections(rows, main, sources)
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(out, indent=1, default=float) + "\n", encoding="utf-8")
    DIR.mkdir(parents=True, exist_ok=True)
    with open(DIR / "thread.csv", "w", newline="", encoding="utf-8") as f:
        fields = ["house", "date", "season", "category", "city", "main", "heat", "surprise", "press", "tone",
                  "lasting", "momentum", "heat_z", "surprise_z", "press_z", "tone_z", "lasting_z", "heat_missing", "url"]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    _log("build", shows=out["shows"], main=out["main_shows"], houses=out["houses"], with_value=cover,
         complete_chains=out["collections"]["complete_chains"])
    return out


# ---------- collections: each show's collection followed through every stage ----------
#
# The Thread's unit is the collection: a house's show of one kind in one season. Each is followed through
# the stages on windows fixed here, counted in days from the show, and every source joins it on house and
# date or on the season a title names. Each stage keeps its own measures; the shared description of the
# clothes, which would let the stages be compared, is not built yet. Nothing registered is computed: the
# attention that lasts after a show is Amendment 2's outcome and waits for the amendment to be frozen.

STAGES = {"scene": (-180, -1),          # ambassador appointments
          "show": (-1, 3),              # heat, surprise, press, tone
          "shop_window": (1, 182),      # the homepages
          "show_period_ads": (-7, 21)}  # ads first seen around the show
# The campaign that follows a show is advertised from about six weeks after it until the house's next main
# show, so its window ends a week before that show rather than on a fixed day. Fixed at 45 to 150 days it
# caught 13 of the 22 dated autumn-winter campaigns; ended at the next main show it catches all 22, and never
# reaches into the next collection. Ended at the next show of any kind (menswear, pre-collections) it would
# catch 7. Where the next main show is not on file, its usual gap stands in: a median 210 days after an
# autumn-winter show and 154 after a spring-summer one, less the week.
CAMPAIGN_FROM = 45
CAMPAIGN_STOP = 7
CAMPAIGN_USUAL_END = {"AW": 203, "SS": 147}
AMBASSADORS_FROM = date(2019, 1, 1)     # where reference/ambassadors.csv starts
SHOP_FULL = 4                           # months with homepage readings, of the six or seven after a show
OFFERING_DAYS = 183                     # a campaign naming no season joins the house's last main show this close
# The search and video ads (adtone/adstransparency.py) join the advertising stage beside the paid social archive's.
# Their dataset starts on 1 March 2023, and an ad first shown before then is dated to that day, so such an ad has no
# day of its own and is left out of every window. It keeps an ad only while the ad has run within the past year: from
# a year before the first read the record holds every ad first shown, and before that only those that kept running
# into that year, the survivors.
SEARCH_ADS = config.DATA / "adstransparency" / "creatives"
SEARCH_PROV = config.PROV_DIR / "adstransparency.jsonl"
SEARCH_FLOOR = date(2023, 3, 1)
SEARCH_KEEP_DAYS = 365
BEAUTY_TOPICS = {"Beauty & Personal Care", "Health & Beauty"}
SEARCH_ACCOUNTS = config.ROOT / "reference" / "google_advertisers.csv"


def search_accounts(path=None) -> set[str]:
    """The houses with an account of their own on the checked list (house, or doubt while Google has not verified
    it), the accounts the collector reads. A house with none has no record to count from: no ads on file for it
    says nothing about whether it advertised."""
    p = path or SEARCH_ACCOUNTS
    if not p.exists():
        return set()
    with p.open(encoding="utf-8", newline="") as f:
        return {r["house"].strip() for r in csv.DictReader(f)
                if r.get("house", "").strip() and r.get("advertiser_id", "").strip()
                and ((r.get("status") or "house").strip() or "house") in ("house", "doubt")}


def search_record(prov=None) -> dict:
    """The days the search and video ads were first and last read, from the collector's provenance."""
    days = []
    for r in store.read_jsonl(prov or SEARCH_PROV) if (prov or SEARCH_PROV).exists() else []:
        if "collect" in (r.get("stage") or "") and len(r.get("updated_at") or "") >= 10:
            days.append(r["updated_at"][:10])
    return {"first_read": min(days), "last_read": max(days)} if days else {}


def advertised(adv: dict, period: str) -> bool:
    """Ads on file for a collection's window: the paid social archive's, or search and video ads where their record
    covers the whole window."""
    s = (adv.get("search_video") or {}).get(period) or {}
    return bool(adv.get(period)) or (s.get("record") == "complete" and s.get("ads", 0) > 0)


def search_window(ads: list[tuple], d0: date, d1: date, record: dict, account: bool = True) -> dict:
    """The search and video ads first shown from d0 to d1, by format and how many advertise beauty, and how much of
    the window the record covers: complete from a year before the first read, survivors only before that, partly
    survivors across it; an open window runs on past the last read. A house with no account of its own on the
    list has no record at all, which is said rather than counted as none."""
    if not record.get("first_read"):
        return {"record": "not read"}
    if d1 <= SEARCH_FLOOR:
        return {"record": "before the dataset"}
    if not account:
        return {"record": "no account found"}
    from datetime import timedelta
    keep_from = max(date.fromisoformat(record["first_read"]) - timedelta(days=SEARCH_KEEP_DAYS),
                    SEARCH_FLOOR + timedelta(days=1))
    rows = [(fmt, topic) for d, fmt, topic in ads if d0 <= d <= d1]
    out = {"ads": len(rows), "video": sum(f == "VIDEO" for f, _ in rows), "image": sum(f == "IMAGE" for f, _ in rows),
           "text": sum(f == "TEXT" for f, _ in rows), "beauty": sum(t in BEAUTY_TOPICS for _, t in rows),
           "record": "complete" if d0 >= keep_from else "survivors" if d1 < keep_from else "partly survivors"}
    if record.get("last_read") and d1 > date.fromisoformat(record["last_read"]):
        out["open"] = True
    return out


def campaign_window(show: date, next_main: date | None = None, half: str | None = None) -> tuple[int, int]:
    """Days after a show in which its campaign is advertised: from six weeks after it to a week before the
    house's next main show, or, with that show not on file, to the season's usual end."""
    if next_main is not None and next_main > show:
        end = (next_main - show).days - CAMPAIGN_STOP
    else:
        end = CAMPAIGN_USUAL_END.get((half or "").upper(), 150)
    return CAMPAIGN_FROM, max(CAMPAIGN_FROM, end)
TITLE_SEASON = re.compile(
    r"(?P<w>pre[-\s]?fall|pre[-\s]?collection|resort|cruise|s/s|ss|spring(?:[-/\s]summer)?|summer|f/w|fw|a/w|aw|"
    r"fall(?:[-/\s]winter)?|autumn(?:[-/\s]winter)?|winter)\W{0,3}(?:pre[-\s]?collection\W{0,3})?'?"
    r"(?P<y>(?:19|20)\d{2}|\d{2})(?!\d)", re.I)
TITLE_CATEGORY = [("couture", re.compile(r"couture", re.I)),
                  ("men", re.compile(r"\bmen\b|\bmen's\b|menswear|\bhomme\b", re.I)),
                  ("prefall", re.compile(r"pre[-\s]?fall|pre[-\s]?collection", re.I)),
                  ("prespring", re.compile(r"pre[-\s]?spring", re.I)),
                  ("resort", re.compile(r"cruise|resort", re.I))]
LINES = [("beauty", re.compile(r"fragrance|parfum|perfume|beauty|make[-\s]?up|skincare|lipstick|cosmetic", re.I)),
         ("jewellery_watches", re.compile(r"jewel|joaill|watch", re.I)),
         ("accessories", re.compile(r"handbag|\bbags?\b|\bshoes?\b|footwear|eyewear|sunglass|leather goods|accessor|"
                                    r"sneaker|charms", re.I)),
         ("capsule", re.compile(r"capsule|collab", re.I))]
CAMPAIGN_TITLE = re.compile(r"campaign|advertising", re.I)     # the season's own advertising, for its timing


def title_season(title: str) -> str | None:
    """The collection a campaign's title names, as a season key: 'Dior F/W 2023 Campaign' is '2023 AW rtw',
    'Chanel Cruise 2014 Press Kit' '2014 RE resort', 'Dior Couture Spring 2007 Show' '2007 SS couture'."""
    m = TITLE_SEASON.search(title or "")
    if not m:
        return None
    w = re.sub(r"[^a-z]", "", m.group("w").lower())
    y = int(m.group("y"))
    if y < 100:
        y += 2000 if y <= 40 else 1900
    cat = next((c for c, rx in TITLE_CATEGORY if rx.search(title)), "rtw")
    if cat == "prespring":
        return f"{y} PS prespring"           # a pre-collection the calendar does not list: joined by date
    if cat == "prefall" or w.startswith("pre"):
        return f"{y} PF prefall"
    if cat == "resort" or w in ("resort", "cruise"):
        return f"{y} RE resort"
    return f"{y} {'AW' if w[0] in 'fwa' else 'SS'} {cat}"


def title_line(title: str, named: bool) -> str:
    """What a campaign sells: beauty, jewellery and watches, or accessories, by its title; a collection when
    it names a season and none of those; otherwise other (a film, an event, a face)."""
    for line, rx in LINES:
        if rx.search(title or ""):
            return line
    return "collection" if named else "other"


# An advertisement's own text, sorted as campaign titles are (Amendment 3, section 2). The text itself is never kept
# (adtone/copyfeat.py), so the sorting happens at collection time, while the text is in hand. Meta's EU archive holds
# ads in many languages: each line's words in English, French, Italian, German and Spanish. "Watch" before "the",
# "now" or "live" is how a show is advertised, not a watch.
AD_LINES = [
    ("beauty", re.compile(r"fragrance|parfum|perfume|profum|fragranz|\bduft|beauty|beaut[eé]\b|bellezza|belleza|"
                          r"sch[öo]nheit|make[-\s]?up|maquillage|trucco|maquillaje|skin\s?care|\bsoins?\b|lipstick|"
                          r"rossetto|lippenstift|labial|cosm[eé]tic|kosmetik|eau de", re.I)),
    ("jewellery_watches", re.compile(r"jewel|joaill|bijou|gioiell|schmuck|joyer|\bjoyas?\b|watches|watchmak|timepiece|"
                                     r"\bwatch\b(?!\s+(?:the|now|live|it|our|more|here|full|video|film|on)\b)|"
                                     r"horlog|orolog|\buhren?\b|reloj", re.I)),
    ("accessories", re.compile(r"handbag|\bbags?\b|\bsacs?\b|\bborse\b|\bborsa\b|tasche|\bbolsos?\b|\bshoes?\b|chaussure|"
                               r"\bscarpe\b|schuh|zapato|footwear|sneaker|eyewear|sunglass|lunettes|occhiali|sonnenbrille|"
                               r"gafas|leather goods|maroquinerie|pelletteria|lederwaren|marroquiner|accessor|accessoire|"
                               r"accesori|charms", re.I)),
    ("capsule", re.compile(r"capsule|kapsel|c[aá]psula|collab", re.I)),
    ("holiday", re.compile(r"holiday|\bgifts?\b|gifting|christmas|festive|cadeau|no[eë]l\b|\bf[eê]tes\b|\bregal[oi]\b|"
                           r"natale|natalizi|geschenk|weihnacht|navidad|navide[nñ]", re.I)),
]
AD_SEASON = re.compile(r"(?:printemps|\b[eé]t[eé]\b|automne|hiver|primavera|estate|autunno|inverno|fr[uü]hling|"
                       r"fr[uü]hjahr|sommer|herbst|verano|oto[nñ]o|invierno)[^\d\n]{0,15}(?:19|20)?\d{2}(?!\d)", re.I)
AD_COLLECTION = re.compile(r"collection|collezion|kollektion|colecci[oó]n|campaign|campagn|kampagne|campa[nñ]a|"
                           r"runway|d[eé]fil[eé]|sfilat|laufsteg|desfile|modenschau|\bshow\b", re.I)
AD_TITLE_SEASON = re.compile(r"\b" + TITLE_SEASON.pattern, re.I)   # whole words only: "Express 24h" names no season


def ad_line(texts: list[str] | None) -> str:
    """What an advertisement sells, from its own text (body, link title and description together): beauty,
    jewellery and watches, accessories, a capsule or the holidays by their words; the season's collection when
    the text names a season, a collection, a campaign or a show and none of those; otherwise other."""
    t = "\n".join(x for x in (texts or []) if x)
    for line, rx in AD_LINES:
        if rx.search(t):
            return line
    named = AD_TITLE_SEASON.search(t) or AD_SEASON.search(t) or AD_COLLECTION.search(t)
    return "collection" if named else "other"


def _entry_kind(r: dict) -> str:
    t = (r.get("title") or "").lower()
    if "lookbook" in t:
        return "lookbook"
    if "press kit" in t:
        return "press kit"
    return r.get("kind") or "other"


def _months(d0: date, d1: date) -> list[str]:
    out, y, m = [], d0.year, d0.month
    while (y, m) <= (d1.year, d1.month):
        out.append(f"{y}-{m:02d}")
        y, m = y + (m == 12), m % 12 + 1
    return out


def _load_sources() -> dict:
    """What the other modules have on file, read without changing it."""
    from . import backcat, homepages, readings
    amb = []
    if readings.AMBASSADORS_FILE.exists():
        with readings.AMBASSADORS_FILE.open(encoding="utf-8", newline="") as f:
            amb = [r for r in csv.DictReader(f) if r.get("category") in readings.FASHION and len(r.get("announced_date") or "") >= 7]
    hp = []
    for p in sorted(homepages.paths()["obs"].glob("*.jsonl")):
        hp += [{"house_id": r.get("house_id"), "month": r.get("month")} for r in store.read_jsonl(p) if r.get("status", "ok") == "ok"]
    camps = []
    for p in sorted(backcat.paths()["campaigns"].glob("*.jsonl")):
        camps += store.read_jsonl(p)
    read = set()
    for p in sorted(backcat.paths()["obs"].glob("*.jsonl")):
        read |= {r.get("campaign_id") for r in store.read_jsonl(p)}
    ads = []
    for p in sorted(config.ADS_DIR.glob("*.jsonl")) if config.ADS_DIR.exists() else []:
        ads += [{"house_id": r.get("house_id"), "start": (r.get("start") or "")[:10]} for r in store.read_jsonl(p)]
    from . import adlibs
    ads += adlibs.paid_social()           # Meta through the vendor, Snapchat, Pinterest and the rest, as they come
    search = []
    for p in sorted(SEARCH_ADS.glob("*.jsonl")) if SEARCH_ADS.exists() else []:
        search += [{"house_id": r.get("house"), "first": r.get("first"), "fmt": r.get("fmt"), "topic": r.get("topic")}
                   for r in store.read_jsonl(p)]
    return {"ambassadors": amb, "homepages": hp, "campaigns": camps, "campaigns_read": read, "ads": ads,
            "search_ads": search, "search_record": search_record(), "search_accounts": search_accounts()}


def collections(rows: list[dict], main: dict[str, str], sources: dict | None = None) -> dict:
    """One record per collection, each stage on its window, and how far each stage is filled. Written to
    data/thread/collections.jsonl; the summary returned goes into thread.json."""
    from datetime import timedelta
    src = sources if sources is not None else _load_sources()
    # the collections: one per house and season key; a second show of the same kind in a season is listed
    col: dict[tuple, dict] = {}
    for r in sorted((r for r in rows if r["season"]), key=lambda r: r["date"]):
        k = (r["house"], r["season"])
        if k in col:
            col[k]["show"]["other_dates"].append(r["date"])
            continue
        col[k] = {"id": f"{r['house']}:{r['season'].replace(' ', ':')}", "house": r["house"], "season": r["season"],
                  "category": r["category"], "main": r["main"], "date": r["date"],
                  "show": {**{s: r.get(s) for s in ("heat", "surprise", "press", "tone", "lasting", "momentum", "heat_z",
                                                     "surprise_z", "press_z", "tone_z", "lasting_z")}, "other_dates": []}}
    by_house = defaultdict(list)
    for c in col.values():
        by_house[c["house"]].append(c)

    def window(c, stage):
        d = date.fromisoformat(c["date"])
        a, b = STAGES[stage] if stage in STAGES else c["campaign_window"]
        return d + timedelta(days=a), d + timedelta(days=b)

    for cs in by_house.values():
        cs.sort(key=lambda c: c["date"])
        for i, c in enumerate(cs):
            nxt = next((x for x in cs[i + 1:] if x["main"] and x["date"] > c["date"]), None)
            c["campaign_window"] = list(campaign_window(date.fromisoformat(c["date"]),
                                                        date.fromisoformat(nxt["date"]) if nxt else None,
                                                        c["season"].split()[1] if len(c["season"].split()) > 1 else None))

    amb = defaultdict(list)
    for r in src.get("ambassadors", []):
        d = r["announced_date"]
        amb[r["house_id"]].append((date.fromisoformat(d if len(d) == 10 else f"{d[:7]}-01"), r.get("verified") == "true"))
    hp = defaultdict(Counter)
    for r in src.get("homepages", []):
        if r.get("house_id") and r.get("month"):
            hp[r["house_id"]][r["month"]] += 1
    ads = defaultdict(list)
    made = defaultdict(list)              # the made pictures' ads: the dynamic and catalogue units set apart
    for r in src.get("ads", []):
        if r.get("house_id") and len(r.get("start") or "") == 10:
            ads[r["house_id"]].append(date.fromisoformat(r["start"]))
            if not r.get("dynamic"):
                made[r["house_id"]].append(date.fromisoformat(r["start"]))
    have_ads = any(ads.values())
    ads_from = min((d for ds in ads.values() for d in ds), default=None)     # the libraries hold a year: earlier windows are blind
    record = src.get("search_record") or {}
    accounts = src.get("search_accounts")          # None: not known, every house taken to have its account
    search = defaultdict(list)
    for r in src.get("search_ads", []):
        f = r.get("first") or ""
        if r.get("house_id") and len(f) == 10 and f > SEARCH_FLOOR.isoformat():   # dated to the floor: no day of its own
            search[r["house_id"]].append((date.fromisoformat(f), r.get("fmt"), r.get("topic")))

    for c in col.values():
        h = c["house"]
        a0, a1 = window(c, "scene")
        hits = [v for d, v in amb.get(h, []) if a0 <= d <= a1]
        c["scene"] = {"appointments": len(hits), "verified": sum(hits), "covered": a0 >= AMBASSADORS_FROM}
        s0, s1 = window(c, "shop_window")
        months = _months(s0, s1)
        c["shop_window"] = {"months": len(months), "months_read": sum(1 for m in months if hp[h].get(m)),
                            "pictures_read": sum(hp[h].get(m, 0) for m in months)}
        p0, p1 = window(c, "show_period_ads")
        q0, q1 = window(c, "campaign_ads")
        # show_period and campaign_period are the paid social archive's ads, as before; the search and video ads
        # are counted beside them on the same windows, with how much of each window their record covers
        c["advertising"] = {"show_period": sum(1 for d in ads.get(h, []) if p0 <= d <= p1),
                            "campaign_period": sum(1 for d in ads.get(h, []) if q0 <= d <= q1),
                            "made": {"show_period": sum(1 for d in made.get(h, []) if p0 <= d <= p1),
                                     "campaign_period": sum(1 for d in made.get(h, []) if q0 <= d <= q1)},
                            "paid_social_covered": {"show_period": bool(ads_from and p0 >= ads_from),
                                                    "campaign_period": bool(ads_from and q0 >= ads_from)},
                            "search_video": {"show_period": search_window(search.get(h, []), p0, p1, record,
                                                                          accounts is None or h in accounts),
                                             "campaign_period": search_window(search.get(h, []), q0, q1, record,
                                                                              accounts is None or h in accounts)}}
        c["campaign"] = {"entries": 0, "pictures_read": 0, "by_kind": Counter(), "by_line": Counter(),
                         "joined_by": Counter(), "ids": []}

    # campaigns and the other things a house publishes: by the season the title names, else by date
    read = src.get("campaigns_read", set())
    lags, unjoined = defaultdict(list), Counter()
    for r in src.get("campaigns", []):
        h, title = r.get("house_id"), r.get("title") or ""
        key = title_season(title)
        line = title_line(title, key is not None)
        target, how = None, None
        if key and (h, key) in col:
            target, how = col[(h, key)], "season"
        elif key and line != "collection" and (h, f"{key.split()[0]} {key.split()[1]} {main.get(h, 'rtw')}") in col:
            target, how = col[(h, f"{key.split()[0]} {key.split()[1]} {main.get(h, 'rtw')}")], "season"
        elif r.get("published"):
            pub = date.fromisoformat(r["published"][:10])
            prior = [c for c in by_house.get(h, []) if c["main"] and 0 <= (pub - date.fromisoformat(c["date"])).days <= OFFERING_DAYS]
            if prior:
                target, how = max(prior, key=lambda c: c["date"]), "date"
        if target is None:
            unjoined[line] += 1
            continue
        b = target["campaign"]
        b["entries"] += 1
        b["by_kind"][_entry_kind(r)] += 1
        b["by_line"][line] += 1
        b["joined_by"][how] += 1
        b["ids"].append(r.get("campaign_id"))
        b["pictures_read"] += int(r.get("campaign_id") in read)
        if how == "season" and line == "collection" and CAMPAIGN_TITLE.search(title) and r.get("published"):
            lag = (date.fromisoformat(r["published"][:10]) - date.fromisoformat(target["date"])).days
            lags[target["season"].split()[1]].append((lag, *target["campaign_window"]))

    def fill(c) -> dict:
        sh = c["show"]
        shop = c["shop_window"]
        camp = c["campaign"]
        adv = c["advertising"]
        return {"scene": "full" if c["scene"]["covered"] else "none",
                "show": ("full" if sh["heat"] is not None and sh["press"] is not None
                         else "part" if sh["heat"] is not None or sh["press"] is not None else "none"),
                "shop_window": ("full" if shop["months_read"] >= SHOP_FULL else "part" if shop["months_read"] else "none"),
                "campaign": ("full" if camp["pictures_read"] else "part" if camp["entries"] else "none"),
                "advertising": ("full" if advertised(adv, "show_period") and advertised(adv, "campaign_period")
                                else "part" if advertised(adv, "show_period") or advertised(adv, "campaign_period")
                                else "none")}

    records = []
    for c in sorted(col.values(), key=lambda c: (c["house"], c["date"])):
        c["campaign"] = {k: (dict(v) if isinstance(v, Counter) else v) for k, v in c["campaign"].items()}
        c["fill"] = fill(c)
        records.append(c)
    DIR.mkdir(parents=True, exist_ok=True)
    store.write_jsonl(DIR / "collections.jsonl", records)

    mains = [c for c in records if c["main"]]
    stages = ("scene", "show", "shop_window", "campaign", "advertising")
    by_stage = {s: dict(Counter(c["fill"][s] for c in mains)) for s in stages}
    by_year = defaultdict(lambda: {s: 0 for s in stages})
    for c in mains:
        y = c["season"].split()[0]
        for s in stages:
            by_year[y][s] += c["fill"][s] == "full"
    lag_summary = {}
    for s, v in sorted(lags.items()):
        days = [x for x, _, _ in v]
        lag_summary[s] = {"campaigns": len(v), "median_days": float(np.median(days)),
                          "quartiles": [float(x) for x in np.percentile(days, [25, 75])],
                          "in_campaign_window": sum(a <= x <= b for x, a, b in v),
                          "after_it": sum(x > b for x, _, b in v)}
    windows = {**{k: list(v) for k, v in STAGES.items()},
               "campaign_ads": f"{CAMPAIGN_FROM} days after the show to {CAMPAIGN_STOP} before the house's next main show "
                               f"(where that is not on file: {CAMPAIGN_USUAL_END['AW']} after autumn-winter, "
                               f"{CAMPAIGN_USUAL_END['SS']} after spring-summer)"}
    return {"collections": len(records), "main": len(mains), "windows": windows,
            "filled_main": by_stage, "full_by_year": dict(sorted(by_year.items())),
            "complete_chains": sum(all(c["fill"][s] == "full" for s in stages) for c in mains),
            "advertising": {"paid_social": "on file" if have_ads else "waiting on the paid social archive",
                            "search_video": {**record, "ads_dated": sum(len(v) for v in search.values()),
                                             "floor": SEARCH_FLOOR.isoformat(), "keep_days": SEARCH_KEEP_DAYS,
                                             "houses_without_account": sorted({c["house"] for c in mains} - accounts)
                                             if accounts is not None else [],
                                             "main_with_both_windows_complete": sum(
                                                 all((c["advertising"]["search_video"][p].get("record") == "complete")
                                                     for p in ("show_period", "campaign_period")) for c in mains)}},
            "campaigns_unjoined": dict(unjoined),
            "campaign_lag_after_show": lag_summary,
            "note": ("each stage is filled with its own measures; the shared description of the clothes that "
                     "would compare them across stages is not built yet")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m adtone.thread")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("calendar")
    sub.add_parser("build")
    ig = sub.add_parser("ingest")
    ig.add_argument("--house", required=True)
    ig.add_argument("--file", required=True)
    ig.add_argument("--read", required=True, help="the date the listing was read")
    ig.add_argument("--how", default="read through Claude's fetcher")
    a = ap.parse_args(argv)
    if a.cmd == "ingest":
        from pathlib import Path
        print(f"thread ingest: {ingest(a.house, Path(a.file).read_text(encoding='utf-8'), a.read, a.how)}")
        return 0
    if a.cmd == "calendar":
        out = calendar()
        c = out["check"]
        print(f"thread calendar: {out['shows']} shows for {out['houses']} houses; against {c['verified_shows']} verified "
              f"shows: {c['same_day']} same day, {c['one_day_apart']} a day apart, {c['further']} further, "
              f"{c['not_listed']} not listed")
        cut = [h for h, v in out["per_house"].items() if v.get("possibly_cut")]
        if cut:
            print(f"::warning::listings that may be cut short at {LISTING_CAP}: {', '.join(cut)}")
        missing = [h for h, v in out["per_house"].items() if not v["listed"]]
        if missing:
            print(f"::notice::no NOWFASHION listing for: {', '.join(missing)}")
        for note in out["notes"][:6]:
            print(f"::notice::{note}")
    else:
        out = build()
        print(f"thread build: {out.get('shows')} shows, {out.get('main_shows')} main, {out.get('houses')} houses; "
              f"with values {out.get('with_value')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
