"""The Thread: the show calendar from NOWFASHION's listings, checked against the verified shows, and heat and tone
for every show set against its season."""
import json
from datetime import date, timedelta

import numpy as np
import pytest

from adtone import thread as T

LISTING = """# Dior

## Runway shows (3)

- [Dior Ready To Wear Spring Summer 2018 Paris](https://nowfashion.com/dior-ready-to-wear-spring-summer-2018-paris.md) — 2017-09-26
- [Dior Couture Fall Winter 2016 Paris](/dior-couture-fall-winter-2016-paris.md) — 2016-07-04
- [Dior Ready To Wear Spring Summer 2018 Paris](https://nowfashion.com/dior-ready-to-wear-spring-summer-2018-paris.md) — 2017-09-26
Some other line
"""


def test_a_listing_gives_title_link_and_date():
    rows = T.parse_listing(LISTING)
    assert len(rows) == 3
    assert rows[0] == {"title": "Dior Ready To Wear Spring Summer 2018 Paris",
                       "url": "https://nowfashion.com/dior-ready-to-wear-spring-summer-2018-paris.md", "date": "2017-09-26"}
    assert rows[1]["url"] == "https://nowfashion.com/dior-couture-fall-winter-2016-paris.md"


@pytest.mark.parametrize("title,expected", [
    ("Dior Ready To Wear Spring Summer 2018 Paris", ("rtw", "SS", 2018, "Paris")),
    ("Chanel Couture Fall Winter 2016 Paris", ("couture", "AW", 2016, "Paris")),
    ("Zegna Menswear Fall Winter 2024 Milan", ("men", "AW", 2024, "Milan")),
    ("Gucci Resort 2019 Arles", ("resort", "RE", 2019, "Arles")),
    ("Max Mara Pre-Fall 2020 New York", ("prefall", "PF", 2020, "New York")),
    ("Dolce & Gabbana Ready To Wear Fall Winter 2017 Milan", ("rtw", "AW", 2017, "Milan")),
])
def test_titles_give_kind_season_year_and_city(title, expected):
    p = T.parse_title(title)
    assert (p["category"], p["season"], p["year"], p["city"]) == expected


class Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


class Sess:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url, timeout=None, headers=None):
        self.asked.append(url)
        assert "Focal research" in headers["User-Agent"]
        return self.pages.get(url, Resp(404))


def test_the_calendar_tries_each_name_and_checks_itself_against_the_verified_shows(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "DIR", tmp_path / "thread")
    monkeypatch.setattr(T, "PROV", tmp_path / "prov.jsonl")
    ref = tmp_path / "shows.csv"
    ref.write_text("house,date,season,city,kind,note,verified,source\n"
                   "dior,2017-09-26,SS18,Paris,show,,true,x\nsaint_laurent,2017-09-27,SS18,Paris,show,,true,x\n"
                   "chanel,2017-10-03,SS18,Paris,show,,true,x\nchanel,2017-03-07,AW17,Paris,show,,false,x\n")
    monkeypatch.setattr(T, "SHOWS_REF", ref)
    ysl = "- [Saint Laurent Ready To Wear Spring Summer 2018 Paris](/saint-laurent-ready-to-wear-spring-summer-2018-paris.md) — 2017-09-26\n"
    sess = Sess({f"{T.SITE}/brand/dior.md": Resp(200, LISTING),
                 f"{T.SITE}/brand/yves-saint-laurent.md": Resp(200, ysl)})
    out = T.calendar(sess, sleep=lambda s: None, houses=["dior", "saint_laurent", "chanel"])
    assert f"{T.SITE}/brand/saint-laurent.md" in sess.asked and f"{T.SITE}/brand/yves-saint-laurent.md" in sess.asked
    assert out["per_house"]["dior"] == {"slug": "dior", "listed": 3, "possibly_cut": False}
    assert out["per_house"]["chanel"]["listed"] == 0
    rows = [json.loads(x) for x in (tmp_path / "thread" / "shows.jsonl").read_text().splitlines()]
    assert len(rows) == 3                                     # the repeated Dior entry kept once
    assert rows[0]["url"].endswith("dior-couture-fall-winter-2016-paris") and rows[0]["source"] == "nowfashion"
    c = out["check"]
    assert c == {"verified_shows": 3, "same_day": 1, "one_day_apart": 1, "further": 0, "not_listed": 1}


def test_main_category_is_what_a_house_shows_most():
    rows = [{"house": "a", "date": "2018-01-01", "category": c} for c in ("rtw", "rtw", "couture", "men")] + \
           [{"house": "z", "date": "2018-01-01", "category": "men"}, {"house": "z", "date": "2014-01-01", "category": "rtw"}]
    assert T.main_category(rows) == {"a": "rtw", "z": "men"}


def test_heat_and_tone_are_measured_at_each_show_and_set_against_its_season(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "DIR", tmp_path / "thread")
    monkeypatch.setattr(T, "PROV", tmp_path / "prov.jsonl")
    monkeypatch.setattr(T, "RESULTS", tmp_path / "thread.json")
    rng = np.random.default_rng(1)
    start, n = date(2016, 1, 1), 900
    shows, attention, press = [], {}, {}
    seasons = [(date(2016, 9, 27), "SS", 2017), (date(2017, 3, 2), "AW", 2017), (date(2017, 9, 28), "SS", 2018),
               (date(2018, 3, 1), "AW", 2018)]
    for h in range(6):
        house = f"h{h}"
        v = {start + timedelta(days=k): int(1000 * np.exp(rng.normal(0, 0.05))) for k in range(n)}
        p = {start + timedelta(days=k): {"articles": 20, "tone": float(rng.normal(0, 0.2))} for k in range(n)}
        for j, (d, s, y) in enumerate(seasons):
            d = d + timedelta(days=h % 3)
            jump = (h + 1) * (1 + j)                         # house h5 draws the most, more each season
            v[d] = v[d] * jump
            for k in range(4):
                p[d + timedelta(days=k)] = {"articles": 20 * (h + 1), "tone": 1.0 * h}
            shows.append({"house": house, "date": d.isoformat(), "category": "rtw", "season": s, "year": y, "city": "Paris"})
        attention[house], press[house] = v, p
    shows.append({"house": "h0", "date": "2017-01-23", "category": "couture", "season": "SS", "year": 2017, "city": "Paris"})
    out = T.build(shows, attention, press)
    assert out["houses"] == 6 and out["main_shows"] == 24 and out["shows"] == 25
    rows = out["rows"]
    top = [r for r in rows if r["house"] == "h5" and r["main"]]
    assert all(r["heat_z"] > 1 and r["press_z"] > 1 and r["tone_z"] > 1 for r in top)
    low = [r for r in rows if r["house"] == "h0" and r["main"]]
    assert all(r["heat_z"] < 0 for r in low)
    assert [r["surprise"] is None for r in top] == [True, True, False, False]
    assert all("lasting" not in r for r in rows)            # the registered outcome is never computed here
    couture = [r for r in rows if not r["main"]]
    assert couture[0]["heat_z"] is None and couture[0]["season"] == "2017 SS couture"
    assert len(out["lines"]["h3"]) == 4 and (tmp_path / "thread" / "thread.csv").exists()
