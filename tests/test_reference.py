"""The hand-copied reference tables: revenue, credits, shows, statements and media value."""
import csv
from datetime import date
from pathlib import Path

from adtone import credits, registry, revenue, runway

REF = Path(__file__).resolve().parent.parent / "reference"


def _ids():
    return {h.id for h in registry.load().houses}


def test_the_shipped_revenue_file_is_clean_and_its_growth_adds_up():
    rows = revenue.read()
    assert len(rows) > 400 and revenue.problems(rows, _ids()) == []
    cov = revenue.coverage()
    assert {"gucci", "hermes", "prada", "miu_miu", "burberry", "zegna"} <= set(cov)
    assert "dior" not in cov    # LVMH does not report by house


def _row(**kw):
    base = {"house_id": "gucci", "brand": "Gucci", "group": "Kering", "measure": "house_revenue", "period": "2025Q1",
            "period_type": "quarter", "period_start": "2025-01-01", "period_end": "2025-03-31", "revenue_m": "1571",
            "currency": "EUR", "reported_change_pct": "-25.0", "comparable_change_pct": "-24.0", "verified": "true",
            "source_url": "https://example.org/kering-q1-2025.pdf", "note": ""}
    return {**base, **kw}


def test_stated_growth_that_the_figures_contradict_is_caught():
    prev = _row(period="2024Q1", period_start="2024-01-01", period_end="2024-03-31", revenue_m="2100",
                reported_change_pct="-18.0")
    ok = _row()   # 1571 / 2100 - 1 = -25.2%
    assert revenue.problems([prev, ok], {"gucci"}) == []
    bad = _row(revenue_m="1871")   # -10.9% implied, -25% stated
    assert any("implied" in p for p in revenue.problems([prev, bad], {"gucci"}))
    assert any("repeats" in p for p in revenue.problems([ok, ok], {"gucci"}))
    assert any("https" in p for p in revenue.problems([_row(source_url="www.example.org")], {"gucci"}))


def test_organic_growth_comes_from_the_house_main_measure():
    figs = revenue.figures()
    g = revenue.organic("gucci", rows=figs)
    assert g and g[0][0] == date(2019, 1, 1) and all(isinstance(x[2], float) for x in g)
    assert [s for s, _, _ in g] == sorted(s for s, _, _ in g)


def test_credits_shows_statements_and_media_value_are_well_formed():
    ids = _ids()
    with (REF / "credits.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) > 500 and credits.problems(rows, ids) == []
    shows = runway.load_shows(verified_only=False)
    assert len(shows) >= 90 and all(s["house"] in ids for s in shows)
    assert len({(s["house"], s["date"], s["kind"]) for s in shows}) == len(shows)
    with (REF / "statements.csv").open(encoding="utf-8") as f:
        st = list(csv.DictReader(f))
    assert st and all(r["house"] in ids and r["source_url"].startswith("https://") for r in st)
    assert all(len(r["excerpt"].split()) <= 80 for r in st)    # short quotations only
    with (REF / "media_value.csv").open(encoding="utf-8") as f:
        mv = list(csv.DictReader(f))
    assert mv and all(r["brand"] and r["rank"].isdigit() for r in mv)
    assert all(not r["house_id"] or r["house_id"] in ids for r in mv)
