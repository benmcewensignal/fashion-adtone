"""Revenue by house, as the houses and their groups report it: the plainest measure of success.

    python -m adtone.revenue check     # validates reference/revenue.csv and prints coverage

reference/revenue.csv holds one row per house, period and measure, copied by hand from results
releases, each with its source. Groups report differently, so `measure` says what a figure is:

  house_revenue     Kering's revenue by house (Gucci, Saint Laurent, Bottega Veneta, the other houses)
  group_revenue     a single-house company's total (Hermès, Chanel, Burberry; Valentino and
                    Dolce&Gabbana from press reports, marked unverified where approximate)
  net_revenues      Brunello Cucinelli; Prada Group's brand revenues including royalties
  net_sales         Prada Group's retail plus wholesale by brand (to 2022)
  retail_net_sales  Prada Group's retail by brand (from 2023)
  segment_revenue   Capri's Versace segment (to the Prada sale)
  brand_revenue     Zegna's ZEGNA brand line; the Tom Ford Fashion line
  branded_products  Zegna's earlier ZEGNA-branded products line

Changes are as reported: `reported_change_pct` at current exchange rates and
`comparable_change_pct` at constant rates and perimeter (organic), the figure that compares a
house with itself. LVMH, OTB, Richemont, Puig and Max Mara do not report by house, so Dior, Louis
Vuitton, Celine, Loewe, Fendi, Givenchy, Loro Piana, Margiela, Jil Sander, Chloé, Alaïa, Dries Van
Noten and Max Mara have no rows: the gap is the groups', not this file's.
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from . import config, registry

REVENUE_FILE = config.ROOT / "reference" / "revenue.csv"
COLUMNS = ["house_id", "brand", "group", "measure", "period", "period_type", "period_start", "period_end", "revenue_m",
           "currency", "reported_change_pct", "comparable_change_pct", "verified", "source_url", "note"]
MEASURES = ("house_revenue", "group_revenue", "net_revenues", "net_sales", "retail_net_sales", "segment_revenue",
            "brand_revenue", "branded_products")
PERIOD_TYPES = ("quarter", "half", "nine_months", "year")
AGGREGATES = ("kering_other_houses",)   # reported lines that are not one house
TOLERANCE_PP = 1.0                      # stated growth against growth implied by the two figures


@dataclass(frozen=True)
class Figure:
    house_id: str
    measure: str
    period: str
    period_type: str
    start: date
    end: date
    revenue_m: float | None
    currency: str
    reported_change_pct: float | None
    comparable_change_pct: float | None
    verified: bool
    source_url: str


def _f(x: str) -> float | None:
    x = (x or "").strip()
    return float(x) if x else None


def read(path: Path | None = None) -> list[dict]:
    with (path or REVENUE_FILE).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def figures(rows: list[dict] | None = None) -> list[Figure]:
    rows = read() if rows is None else rows
    return [Figure(r["house_id"], r["measure"], r["period"], r["period_type"], date.fromisoformat(r["period_start"]),
                   date.fromisoformat(r["period_end"]), _f(r["revenue_m"]), r["currency"], _f(r["reported_change_pct"]),
                   _f(r["comparable_change_pct"]), r["verified"].strip().lower() == "true", r["source_url"]) for r in rows]


def _year_before(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:   # 29 February
        return d.replace(year=d.year - 1, day=28)


def problems(rows: list[dict], house_ids: set[str]) -> list[str]:
    """Every row well formed, sourced and unique; and wherever the same period a year earlier is on file,
    the stated reported change agrees with the change the two figures imply."""
    out, seen = [], {}
    for i, r in enumerate(rows, start=2):
        at = f"line {i}"
        if list(r.keys()) != COLUMNS:
            return [f"columns must be {', '.join(COLUMNS)}"]
        if r["house_id"] not in house_ids and r["house_id"] not in AGGREGATES:
            out.append(f"{at}: unknown house {r['house_id']!r}")
        if r["measure"] not in MEASURES:
            out.append(f"{at}: measure {r['measure']!r} is not one of {', '.join(MEASURES)}")
        if r["period_type"] not in PERIOD_TYPES:
            out.append(f"{at}: period_type {r['period_type']!r}")
        try:
            a, b = date.fromisoformat(r["period_start"]), date.fromisoformat(r["period_end"])
            if a > b:
                out.append(f"{at}: period starts after it ends")
        except ValueError:
            out.append(f"{at}: period dates must be YYYY-MM-DD")
            continue
        for c in ("revenue_m", "reported_change_pct", "comparable_change_pct"):
            try:
                _f(r[c])
            except ValueError:
                out.append(f"{at}: {c} {r[c]!r} is not a number")
        if not r["revenue_m"].strip():
            out.append(f"{at}: no revenue figure")
        if not r["currency"] or len(r["currency"]) != 3:
            out.append(f"{at}: currency must be a three-letter code")
        if r["verified"] not in ("true", "false"):
            out.append(f"{at}: verified must be true or false")
        urls = [u.strip() for u in r["source_url"].split("|") if u.strip()]
        if not urls or not all(u.startswith("https://") for u in urls):
            out.append(f"{at}: every row needs its source, as an https address")
        k = (r["house_id"], r["measure"], r["period_start"], r["period_end"])
        if k in seen:
            out.append(f"{at}: repeats line {seen[k]}")
        seen[k] = i
    if out:
        return out
    by = {(f.house_id, f.measure, f.start, f.end): f for f in figures(rows)}
    for f in by.values():
        p = by.get((f.house_id, f.measure, _year_before(f.start), _year_before(f.end)))
        if not p or f.reported_change_pct is None or not f.revenue_m or not p.revenue_m or p.currency != f.currency:
            continue
        implied = (f.revenue_m / p.revenue_m - 1) * 100
        if abs(implied - f.reported_change_pct) > TOLERANCE_PP:
            out.append(f"{f.house_id} {f.measure} {f.period}: stated {f.reported_change_pct:+.1f}% against "
                       f"{implied:+.1f}% implied by the figures on file (restated comparatives, or an error)")
    return out


def organic(house_id: str, period_type: str = "quarter", rows: list[Figure] | None = None) -> list[tuple[date, date, float]]:
    """(start, end, comparable change %) for one house, oldest first, from its main measure."""
    figs = [f for f in (rows if rows is not None else figures()) if f.house_id == house_id
            and f.period_type == period_type and f.comparable_change_pct is not None]
    if not figs:
        return []
    counts = defaultdict(int)
    for f in figs:
        counts[f.measure] += 1
    main = max(counts, key=lambda m: (counts[m], m))
    return sorted((f.start, f.end, f.comparable_change_pct) for f in figs if f.measure == main)


def coverage(rows: list[Figure] | None = None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in rows if rows is not None else figures():
        c = out.setdefault(f.house_id, {"rows": 0, "first": f.start, "last": f.end, "types": set(), "unverified": 0})
        c["rows"] += 1
        c["first"], c["last"] = min(c["first"], f.start), max(c["last"], f.end)
        c["types"].add(f.period_type)
        c["unverified"] += int(not f.verified)
    return out


def main(argv: list[str] | None = None) -> int:
    reg = registry.load()
    rows = read()
    errs = problems(rows, {h.id for h in reg.houses})
    for e in errs:
        print(f"::error::{e}")
    cov = coverage(figures(rows))
    print(f"{len(rows)} figures for {len(cov)} houses and lines")
    for h, c in sorted(cov.items()):
        print(f"  {h:20s} {c['rows']:4d}  {c['first']} to {c['last']}  {', '.join(sorted(c['types']))}"
              + (f"  ({c['unverified']} unverified)" if c["unverified"] else ""))
    missing = sorted(h.id for h in reg.houses if h.id not in cov)
    print(f"no figures (their groups do not report by house): {', '.join(missing)}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
