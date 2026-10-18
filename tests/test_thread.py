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


def test_a_line_listed_on_its_own_page_joins_the_house_and_a_missing_page_keeps_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "DIR", tmp_path / "thread")
    monkeypatch.setattr(T, "PROV", tmp_path / "prov.jsonl")
    ref = tmp_path / "shows.csv"
    ref.write_text("house,date,season,city,kind,note,verified,source\n")
    monkeypatch.setattr(T, "SHOWS_REF", ref)
    rtw = "- [Giorgio Armani Ready To Wear Fall Winter 2026 Milan](/giorgio-armani-ready-to-wear-fall-winter-2026-milan.md) — 2026-03-01\n"
    prive = "- [Giorgio Armani Prive Couture Spring Summer 2026 Paris](/giorgio-armani-prive-couture-spring-summer-2026-paris.md) — 2026-01-27\n"
    sess = Sess({f"{T.SITE}/brand/giorgio-armani.md": Resp(200, rtw), f"{T.SITE}/brand/giorgio-armani-prive.md": Resp(200, prive)})
    out = T.calendar(sess, sleep=lambda s: None, houses=["armani"])
    assert out["per_house"]["armani"] == {"slug": "giorgio-armani", "listed": 2, "possibly_cut": False}
    rows = [json.loads(x) for x in (tmp_path / "thread" / "shows.jsonl").read_text().splitlines()]
    assert {r["category"] for r in rows} == {"rtw", "couture"}
    # the couture page unread: the house's rows on file stay as they were rather than lose the couture shows
    out = T.calendar(Sess({f"{T.SITE}/brand/giorgio-armani.md": Resp(200, rtw)}), sleep=lambda s: None, houses=["armani"])
    assert out["per_house"]["armani"]["listed"] == 0
    assert len((tmp_path / "thread" / "shows.jsonl").read_text().splitlines()) == 2


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
    # the attention that lasts: every show here has its 120 days on file, so each has a value, and so does momentum
    assert all(r["lasting"] is not None and r["momentum"] is not None for r in rows if r["main"])
    assert all(r["lasting_z"] is not None for r in rows if r["main"])
    couture = [r for r in rows if not r["main"]]
    assert couture[0]["heat_z"] is None and couture[0]["season"] == "2017 SS couture"
    assert len(out["lines"]["h3"]) == 4 and (tmp_path / "thread" / "thread.csv").exists()


def test_a_listing_read_elsewhere_is_merged_and_dates_outside_the_season_are_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "DIR", tmp_path / "thread")
    monkeypatch.setattr(T, "PROV", tmp_path / "prov.jsonl")
    ref = tmp_path / "shows.csv"
    ref.write_text("house,date,season,city,kind,note,verified,source\ndior,2017-09-26,SS18,Paris,show,,true,x\n")
    monkeypatch.setattr(T, "SHOWS_REF", ref)
    text = ("2019-11-28 | Dior Ready To Wear Spring Summer 2020 Paris | https://nowfashion.com/dior-ready-to-wear-spring-summer-2020-paris.md\n"
            "2017-09-26 | Dior Ready To Wear Spring Summer 2018 Paris | https://nowfashion.com/dior-ready-to-wear-spring-summer-2018-paris.md\n"
            "2017-06-24 | Dior Homme Menswear Spring Summer 2018 Paris | https://nowfashion.com/dior-homme-menswear-spring-summer-2018-paris.md\n"
            "2018-05-25 | Dior Resort 2019 Chantilly | https://nowfashion.com/dior-resort-2019-chantilly.md\n"
            "TOTAL: 4\n")
    assert T.ingest("dior", text, "2026-10-08", "read through Claude's fetcher") == {"house": "dior", "rows": 4, "doubtful": 1}
    T.ingest("chanel", "2017-10-03 | Chanel Ready To Wear Spring Summer 2018 Paris | https://nowfashion.com/chanel-rtw.md", "2026-10-08", "x")
    rows = [json.loads(x) for x in (tmp_path / "thread" / "shows.jsonl").read_text().splitlines()]
    assert {r["house"] for r in rows} == {"dior", "chanel"} and len(rows) == 5       # one house's ingest keeps the other's
    bad = [r for r in rows if r["date_doubtful"]]
    assert [r["date"] for r in bad] == ["2019-11-28"] and all(r["how"] for r in rows)
    check = json.loads((tmp_path / "thread" / "calendar_check.json").read_text())
    assert check["same_day"] == 1


@pytest.mark.parametrize("row,ok", [
    ({"category": "rtw", "season": "SS", "year": 2018, "date": "2017-09-26"}, True),
    ({"category": "rtw", "season": "SS", "year": 2020, "date": "2019-11-28"}, False),
    ({"category": "rtw", "season": "AW", "year": 2021, "date": "2021-03-08"}, True),
    ({"category": "couture", "season": "AW", "year": 2019, "date": "2019-07-01"}, True),
    ({"category": "men", "season": "SS", "year": 2018, "date": "2017-06-24"}, True),
    ({"category": "men", "season": "AW", "year": 2018, "date": "2018-06-24"}, False),
    ({"category": "resort", "season": "RE", "year": 2019, "date": "2018-05-25"}, True),
])
def test_dates_are_judged_against_the_weeks_their_kind_of_show_takes_place(row, ok):
    assert T.in_window(row) is ok


def test_a_compact_listing_names_everything_through_the_page_address(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "DIR", tmp_path / "thread")
    monkeypatch.setattr(T, "PROV", tmp_path / "prov.jsonl")
    ref = tmp_path / "shows.csv"
    ref.write_text("house,date,season,city,kind,note,verified,source\n")
    monkeypatch.setattr(T, "SHOWS_REF", ref)
    text = ("2026-09-25 | gucci-men-women-spring-summer-2027-milan\n2017-02-22 | gucci-ready-to-wear-fall-winter-2017-milan-2\n"
            "2020-01-22 | maison-margiela-artisanal-couture-spring-summer-2020-paris\nTOTAL: 3\n")
    assert T.ingest("gucci", text, "2026-10-08", "x")["rows"] == 3
    rows = {r["date"]: r for r in (json.loads(x) for x in (tmp_path / "thread" / "shows.jsonl").read_text().splitlines())}
    assert (rows["2026-09-25"]["category"], rows["2026-09-25"]["season"], rows["2026-09-25"]["year"]) == ("rtw", "SS", 2027)
    assert rows["2017-02-22"]["city"] == "Milan" and rows["2017-02-22"]["url"] == f"{T.SITE}/gucci-ready-to-wear-fall-winter-2017-milan-2"
    assert rows["2020-01-22"]["category"] == "couture" and not rows["2020-01-22"]["date_doubtful"]


def test_a_show_the_data_has_not_caught_up_with_gets_no_heat(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "DIR", tmp_path / "thread")
    monkeypatch.setattr(T, "PROV", tmp_path / "prov.jsonl")
    monkeypatch.setattr(T, "RESULTS", tmp_path / "thread.json")
    start = date(2017, 1, 1)
    att = {f"h{h}": {start + timedelta(days=k): 1000 for k in range(300)} for h in range(5)}
    shows = [{"house": f"h{h}", "date": (start + timedelta(days=d)).isoformat(), "category": "rtw", "season": "SS",
              "year": 2018, "city": "Paris"} for h in range(5) for d in (120, 298)]
    out = T.build(shows, att, {})
    by = {(r["house"], r["date"]): r for r in out["rows"]}
    assert by[("h0", (start + timedelta(days=120)).isoformat())]["heat"] == 0.0
    assert by[("h0", (start + timedelta(days=298)).isoformat())]["heat"] is None       # only two days after on file


def test_a_jump_read_off_a_handful_of_views_a_day_is_left_out(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "DIR", tmp_path / "thread")
    monkeypatch.setattr(T, "PROV", tmp_path / "prov.jsonl")
    monkeypatch.setattr(T, "RESULTS", tmp_path / "thread.json")
    start = date(2017, 1, 1)
    att = {f"h{h}": {start + timedelta(days=k): 1000 for k in range(300)} for h in range(5)}
    att["h0"] = {start + timedelta(days=k): 5 for k in range(300)}          # an article that is not yet the brand's
    att["h0"][start + timedelta(days=120)] = 15
    shows = [{"house": f"h{h}", "date": (start + timedelta(days=120)).isoformat(), "category": "rtw", "season": "SS",
              "year": 2018, "city": "Paris"} for h in range(5)]
    out = T.build(shows, att, {})
    by = {r["house"]: r for r in out["rows"]}
    assert by["h0"]["heat"] is None and by["h1"]["heat"] == 0.0
    assert out["left_out_thin_views"] == 1


def test_a_listing_a_few_days_off_a_verified_show_takes_its_date():
    ref = [{"house": "celine", "date": "2025-10-05"}, {"house": "celine", "date": "2025-07-06"}]
    row = {"house": "celine", "date": "2025-09-30", "year": 2026, "season": "SS", "category": "rtw"}
    out = T.redated(row, ref)
    assert out["date"] == "2025-10-05" and out["listed_date"] == "2025-09-30"
    near = {**row, "date": "2025-10-04"}
    assert T.redated(near, ref) == near                      # within a day: the listing stands
    far = {**row, "date": "2025-09-01"}
    assert T.redated(far, ref) == far                        # more than a week from any: left alone
    other = {**row, "house": "dior"}
    assert T.redated(other, ref) == other
    # a show of the same season held away from the usual week, verified, takes the listing's place and its city
    zref = [{"house": "zegna", "date": "2025-06-11", "season": "SS26 men", "city": "Dubai", "kind": "show"},
            {"house": "zegna", "date": "2025-01-20", "season": "AW25 men", "city": "Milan", "kind": "show"}]
    listed = {"house": "zegna", "date": "2025-06-23", "year": 2026, "season": "SS", "category": "men", "city": "Milan"}
    moved = T.redated(listed, zref)
    assert (moved["date"], moved["city"], moved["listed_date"], moved["listed_city"]) == ("2025-06-11", "Dubai",
                                                                                         "2025-06-23", "Milan")
    assert T.season_label(listed) == "SS26 men" and T.season_label({**listed, "category": "rtw"}) == "SS26"
    assert T.redated({**listed, "category": "rtw"}, zref) == {**listed, "category": "rtw"}   # another season: left
    assert T.redated({**listed, "date": "2025-08-30"}, zref)["date"] == "2025-08-30"           # 80 days off: left


def test_momentum_shares_no_days_with_the_weeks_lasting_attention_is_measured_from(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "DIR", tmp_path / "thread")
    monkeypatch.setattr(T, "PROV", tmp_path / "prov.jsonl")
    monkeypatch.setattr(T, "RESULTS", tmp_path / "thread.json")
    start = date(2017, 1, 1)
    att = {f"h{h}": {start + timedelta(days=k): 1000 for k in range(400)} for h in range(5)}
    for k in range(140, 191):                      # h0 runs high through the baseline weeks alone
        att["h0"][start + timedelta(days=k)] = 3000
    shows = [{"house": f"h{h}", "date": (start + timedelta(days=200)).isoformat(), "category": "rtw", "season": "SS",
              "year": 2018, "city": "Paris"} for h in range(5)]
    by = {r["house"]: r for r in T.build(shows, att, {})["rows"]}
    assert by["h0"]["lasting"] < -1 and by["h1"]["lasting"] == 0.0
    assert by["h0"]["momentum"] == 0.0                # the high baseline lowers lasting attention and nothing else


def test_each_show_without_heat_says_why(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "DIR", tmp_path / "thread")
    monkeypatch.setattr(T, "PROV", tmp_path / "prov.jsonl")
    monkeypatch.setattr(T, "RESULTS", tmp_path / "thread.json")
    start = date(2017, 1, 1)
    att = {f"h{h}": {start + timedelta(days=k): 1000 for k in range(300)} for h in range(5)}
    att["h1"] = {start + timedelta(days=k): 1000 for k in range(100, 300)}         # an article that starts late
    att["h2"] = {start + timedelta(days=k): 5 for k in range(300)}
    shows = [{"house": f"h{h}", "date": (start + timedelta(days=d)).isoformat(), "category": "rtw", "season": "SS",
              "year": 2018, "city": "Paris"} for h in range(5) for d in (120, 298)]
    out = T.build(shows, att, {})
    by = {(r["house"], r["date"][5:]): r["heat_missing"] for r in out["rows"]}
    early, late = (start + timedelta(days=120)).isoformat()[5:], (start + timedelta(days=298)).isoformat()[5:]
    assert by[("h0", early)] is None and by[("h0", late)] == "not yet on file"
    assert by[("h1", early)] == "before the views" and by[("h2", early)] == "thin views"


def test_a_show_held_off_the_calendar_is_kept_once_its_date_is_verified(tmp_path, monkeypatch):
    ref = [{"house": "gucci", "date": "2021-11-02", "verified": "true"}]
    love_parade = {"house": "gucci", "date": "2021-11-03", "category": "rtw", "season": "SS", "year": 2022}
    posted_late = {"house": "dior", "date": "2019-11-28", "category": "rtw", "season": "SS", "year": 2020}
    assert T.doubtful(love_parade, ref) is False and T.doubtful(posted_late, ref) is True
    assert T.doubtful({**love_parade, "house": "prada"}, ref) is True        # another house's show does not vouch
    assert T.vouched(love_parade, ref) is ref[0] and T.vouched(posted_late, ref) is None


@pytest.mark.parametrize("title,key", [
    ("Dior F/W 2023 Campaign (Dior)", "2023 AW rtw"),
    ("Chanel S/S 17 Show (Chanel)", "2017 SS rtw"),
    ("Chanel Cruise 2014 Press Kit (Chanel)", "2014 RE resort"),
    ("Dior Couture Spring 2007 Show (Dior)", "2007 SS couture"),
    ("Chanel Haute Couture Winter 2013 (Chanel)", "2013 AW couture"),
    ("Jil Sander Pre-Fall 2026 Campaign (Jil Sander)", "2026 PF prefall"),
    ("F/W Pre-Collection 26 Lookbook (Loewe)", "2026 PF prefall"),
    ("LOEWE SS26 Charms Collection (Loewe)", "2026 SS rtw"),
    ("Chanel S/S 1995 Show (Chanel)", "1995 SS rtw"),
    ("Chanel Pre-Spring 2023 Campaign (Chanel)", "2023 PS prespring"),
    ("Chanel J12 Watches 2026 Campaign (Chanel)", None),
    ("Jennifer Lawrence for Dior (Dior)", None),
])
def test_a_campaign_title_names_its_collection(title, key):
    assert T.title_season(title) == key


def test_what_a_campaign_sells_is_read_from_its_title():
    assert T.title_line("Chanel Handbags S/S 2020 Campaign", True) == "accessories"
    assert T.title_line("Chanel Bleu de Chanel Fragrance", False) == "beauty"
    assert T.title_line("Chanel J12 Watches 2026 Campaign", False) == "jewellery_watches"
    assert T.title_line("Dior F/W 2023 Campaign", True) == "collection"
    assert T.title_line("Loewe 2024 Met Gala", False) == "other"
    assert T.title_line("Dior Forever Make Up Spring 2021", True) == "beauty"
    assert T.title_line("The New 'DiorAlps' Ski Wear Capsule F/W 21", True) == "capsule"


@pytest.mark.parametrize("texts,line", [
    (["Discover the Fall-Winter 2026 collection"], "collection"),
    (["Découvrez la collection Printemps-Été 2027"], "collection"),
    (["La nuova campagna Autunno-Inverno 2026"], "collection"),
    (["Watch the show live", "Spring-Summer 2027"], "collection"),     # a show is advertised, not a watch
    (["The new Galleria bag"], "accessories"),
    (["Le nouveau sac", "Découvrir"], "accessories"),
    (["Die neue Tasche"], "accessories"),
    (["Eau de Parfum, the new fragrance"], "beauty"),
    (["A beautiful collection"], "collection"),                       # beautiful is not beauty
    (["Haute Joaillerie"], "jewellery_watches"),
    (["The new Tank watch"], "jewellery_watches"),
    (["Holiday gifts for everyone"], "holiday"),
    (["Idées cadeaux pour Noël"], "holiday"),
    (["Express 24h delivery"], "other"),                              # no season in "ss 24"
    (["Complete the look"], "other"),                                 # été only as a word
    ([], "other"),
    (None, "other"),
])
def test_what_an_ad_sells_is_read_from_its_own_text_in_five_languages(texts, line):
    assert T.ad_line(texts) == line


@pytest.mark.parametrize("texts,season", [
    (["Discover the Fall-Winter 2026 collection"], "2026 AW rtw"),
    (["SS26 is here"], "2026 SS rtw"),
    (["FW25 campaign", "Shop now"], "2025 AW rtw"),
    (["Printemps-Été 2026 : la nouvelle collection"], "2026 SS rtw"),
    (["Herbst/Winter 2026 Kollektion"], "2026 AW rtw"),
    (["La collezione Autunno Inverno 2026"], "2026 AW rtw"),
    (["Colección Primavera-Verano 2026"], "2026 SS rtw"),
    (["The Cruise 2027 show"], "2027 RE resort"),
    (["Pre-Fall 2026"], "2026 PF prefall"),
    (["Men's Spring Summer 2027 campaign"], "2027 SS men"),
    (["Haute Couture Autumn-Winter 2026"], "2026 AW couture"),
    (["Women's Winter 2026"], "2026 AW rtw"),                        # women is not men
    (["Express 24h delivery"], None),                                 # no season in "ss 24"
    (["The new collection. Shop now."], None),                        # a collection, no season named
    (["Complete the look"], None),
    ([], None),
    (None, None),
])
def test_the_season_an_ad_names_is_kept_as_a_key_the_words_are_not(texts, season):
    assert T.ad_season(texts) == season


def test_each_collection_is_followed_through_its_stages(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "DIR", tmp_path / "thread")
    monkeypatch.setattr(T, "PROV", tmp_path / "prov.jsonl")
    monkeypatch.setattr(T, "RESULTS", tmp_path / "thread.json")
    start = date(2022, 1, 1)
    att = {f"h{h}": {start + timedelta(days=k): 1000 + (500 if k == 400 else 0) for k in range(700)} for h in range(5)}
    shows = [{"house": f"h{h}", "date": (start + timedelta(days=400)).isoformat(), "category": "rtw", "season": "AW",
              "year": 2023, "city": "Paris"} for h in range(5)]
    show_day = start + timedelta(days=400)                                   # 2023-02-05
    sources = {
        "ambassadors": [{"house_id": "h0", "announced_date": (show_day - timedelta(days=30)).isoformat(), "verified": "true"},
                        {"house_id": "h0", "announced_date": (show_day - timedelta(days=300)).isoformat(), "verified": "true"}],
        "homepages": [{"house_id": "h0", "month": m} for m in ("2023-02", "2023-03", "2023-04", "2023-05", "2023-05")],
        "campaigns": [{"house_id": "h0", "campaign_id": "a", "title": "H0 F/W 2023 Campaign", "kind": "campaign",
                       "published": "2023-07-20"},
                      {"house_id": "h0", "campaign_id": "b", "title": "H0 Handbags Campaign", "kind": "campaign",
                       "published": "2023-03-01"},
                      {"house_id": "h1", "campaign_id": "c", "title": "H1 S/S 2031 Campaign", "kind": "campaign"}],
        "campaigns_read": {"a"},
        "ads": [{"house_id": "h0", "start": (show_day + timedelta(days=3)).isoformat()},
                {"house_id": "h0", "start": (show_day + timedelta(days=90)).isoformat()},
                {"house_id": "h0", "start": (show_day + timedelta(days=91)).isoformat(), "dynamic": True, "source": "metaweb"}],
    }
    out = T.build(shows, att, {}, sources=sources)
    recs = {json.loads(x)["house"]: json.loads(x) for x in (tmp_path / "thread" / "collections.jsonl").read_text().splitlines()}
    h0 = recs["h0"]
    assert h0["id"] == "h0:2023:AW:rtw" and h0["scene"] == {"appointments": 1, "verified": 1, "covered": True}
    assert h0["shop_window"]["months_read"] == 4 and h0["shop_window"]["pictures_read"] == 5
    assert h0["campaign"]["entries"] == 2 and h0["campaign"]["joined_by"] == {"season": 1, "date": 1}
    assert h0["campaign"]["by_line"] == {"collection": 1, "accessories": 1} and h0["campaign"]["pictures_read"] == 1
    assert h0["advertising"] == {"show_period": 1, "campaign_period": 2, "made": {"show_period": 1, "campaign_period": 1},
                                 "paid_social_covered": {"show_period": False, "campaign_period": True},   # the first ad on file is after the show window opens
                                 "search_video": {"show_period": {"record": "not read"}, "campaign_period": {"record": "not read"}}}
    assert h0["fill"] == {"scene": "full", "show": "part", "shop_window": "full", "campaign": "full", "advertising": "full"}
    assert recs["h1"]["fill"]["campaign"] == "none" and "lasting" in recs["h1"]["show"]
    s = out["collections"]
    assert s["complete_chains"] == 0                                  # no press on file, so no show is full
    assert s["campaigns_unjoined"] == {"collection": 1}
    assert s["campaign_lag_after_show"]["AW"]["median_days"] == (date(2023, 7, 20) - show_day).days


def test_search_and_video_ads_count_where_their_record_covers_the_window(tmp_path, monkeypatch):
    record = {"first_read": "2026-10-12", "last_read": "2026-10-16"}        # complete from 2025-10-12
    ads = [(date(2026, 3, 4), "VIDEO", "Apparel"), (date(2026, 3, 6), "TEXT", "Beauty & Personal Care"),
           (date(2026, 5, 1), "IMAGE", "Apparel"), (date(2024, 3, 1), "IMAGE", "Apparel")]
    show = T.search_window(ads, date(2026, 2, 24), date(2026, 3, 24), record)
    assert show == {"ads": 2, "video": 1, "image": 0, "text": 1, "beauty": 1, "record": "complete"}
    assert T.search_window(ads, date(2024, 2, 20), date(2024, 3, 20), record)["record"] == "survivors"
    assert T.search_window(ads, date(2025, 10, 1), date(2025, 10, 30), record)["record"] == "partly survivors"
    assert T.search_window(ads, date(2022, 9, 1), date(2022, 10, 1), record) == {"record": "before the dataset"}
    assert T.search_window(ads, date(2026, 10, 1), date(2026, 10, 30), record)["open"] is True
    assert T.search_window(ads, date(2026, 2, 24), date(2026, 3, 24), {}) == {"record": "not read"}
    # a house with no account of its own has no record to count from, which is said rather than counted as none
    assert T.search_window([], date(2026, 2, 24), date(2026, 3, 24), record, account=False) == {"record": "no account found"}
    listed = tmp_path / "accounts.csv"
    listed.write_text("house,advertiser_id,status\nh0,AR1,house\nh1,AR2,doubt\nh2,AR3,not\nh3,,house\n")
    assert T.search_accounts(listed) == {"h0", "h1"}
    prov = tmp_path / "prov.jsonl"
    prov.write_text("\n".join(json.dumps(r) for r in [{"updated_at": "2026-10-12T17:51:51Z"},
                                                      {"updated_at": "2026-10-12T18:32:58Z", "stage": "discover+collect"},
                                                      {"updated_at": "2026-10-16T11:39:29Z", "stage": "collect"}]))
    assert T.search_record(prov) == record
    # in a build: an ad dated to the dataset's first day has no day of its own and is left out; the stage is filled
    # by search and video ads only on windows their record covers whole
    start = date(2025, 1, 1)
    att = {f"h{h}": {start + timedelta(days=k): 1000 for k in range(700)} for h in range(5)}
    show_day = date(2026, 3, 2)
    shows = [{"house": f"h{h}", "date": show_day.isoformat(), "category": "rtw", "season": "AW", "year": 2026, "city": "Paris"}
             for h in range(5)] + [{"house": "h1", "date": "2024-02-26", "category": "rtw", "season": "AW", "year": 2024,
                                    "city": "Milan"}]
    sources = {"ambassadors": [], "homepages": [], "campaigns": [], "campaigns_read": set(), "ads": [],
               "search_record": record, "search_accounts": {"h0", "h1", "h2", "h3"},
               "search_ads": [{"house_id": "h0", "first": "2026-03-04", "fmt": "VIDEO", "topic": "Apparel"},
                              {"house_id": "h0", "first": "2026-05-20", "fmt": "IMAGE", "topic": "Apparel"},
                              {"house_id": "h0", "first": "2023-03-01", "fmt": "TEXT", "topic": "Apparel"},
                              {"house_id": "h1", "first": "2026-03-03", "fmt": "TEXT", "topic": "Apparel"},
                              {"house_id": "h1", "first": "2024-02-27", "fmt": "VIDEO", "topic": "Apparel"}]}
    monkeypatch.setattr(T, "RESULTS", tmp_path / "thread.json")
    monkeypatch.setattr(T, "DIR", tmp_path / "thread")
    monkeypatch.setattr(T, "PROV", tmp_path / "prov.jsonl")
    out = T.build(shows, att, {}, sources=sources)
    recs = {(c["house"], c["date"]): c for c in map(json.loads, (tmp_path / "thread" / "collections.jsonl").read_text().splitlines())}
    h0 = recs[("h0", "2026-03-02")]
    assert h0["advertising"]["search_video"]["show_period"]["ads"] == 1                # the floored ad is not counted
    assert h0["advertising"]["search_video"]["campaign_period"]["ads"] == 1
    assert h0["fill"]["advertising"] == "full" and recs[("h1", "2026-03-02")]["fill"]["advertising"] == "part"
    old = recs[("h1", "2024-02-26")]
    assert old["advertising"]["search_video"]["show_period"] == {"ads": 1, "video": 1, "image": 0, "text": 0,
                                                                  "beauty": 0, "record": "survivors"}
    assert old["fill"]["advertising"] == "none"                                       # survivors do not fill the stage
    assert recs[("h4", "2026-03-02")]["advertising"]["search_video"]["show_period"] == {"record": "no account found"}
    assert recs[("h3", "2026-03-02")]["advertising"]["search_video"]["show_period"]["record"] == "complete"
    summary = out["collections"]["advertising"]
    assert summary["paid_social"] == "waiting on the paid social archive" and summary["search_video"]["ads_dated"] == 4
    assert summary["search_video"]["houses_without_account"] == ["h4"]


def test_the_campaign_window_runs_to_a_week_before_the_next_main_show():
    show = date(2025, 3, 4)
    assert T.campaign_window(show, date(2025, 9, 30), "AW") == (45, 203)       # 210 days on, less the week
    assert T.campaign_window(show, None, "AW") == (45, 203)                    # not on file: the usual gap
    assert T.campaign_window(date(2025, 9, 30), None, "SS") == (45, 147)
    assert T.campaign_window(show, None, None) == (45, 150)
    assert T.campaign_window(show, show, "AW") == (45, 203)                    # a same-day show is not the next one


def test_a_checked_show_the_listings_do_not_hold_joins_the_calendar():
    ref = [{"house": "the_row", "date": "2025-10-02", "season": "SS26", "city": "Paris", "kind": "show", "source": "https://a | https://b"},
           {"house": "the_row", "date": "2025-03-05", "season": "AW25", "city": "Paris", "kind": "show"},
           {"house": "jacquemus", "date": "2024-06-10", "season": "AW24", "city": "Capri", "kind": "show"},
           {"house": "zegna", "date": "2025-06-11", "season": "SS26 men", "city": "Dubai", "kind": "show"},
           {"house": "moncler", "date": "2023-02-20", "season": "AW23", "city": "London", "kind": "presentation"},
           {"house": "dior", "date": "2025-10-01", "season": "SS26", "city": "Paris", "kind": "show"}]
    rows = [{"house": "the_row", "date": "2025-03-06", "year": 2025, "season": "AW", "category": "rtw"},   # within a week
            {"house": "jacquemus", "date": "2024-09-01", "year": 2024, "season": "AW", "category": "rtw"},  # same season, far off
            {"house": "zegna", "date": "2025-06-11", "year": 2026, "season": "SS", "category": "men"},
            {"house": "dior", "date": "2025-10-01", "year": 2026, "season": "SS", "category": "rtw"}]
    added = T.unlisted(rows, ref)
    assert [(a["house"], a["date"], a["year"], a["season"], a["category"]) for a in added] == [
        ("the_row", "2025-10-02", 2026, "SS", "rtw")]
    assert added[0]["source"] == "verified" and added[0]["url"] == "https://a" and added[0]["city"] == "Paris"
    assert T.unlisted(rows + added, ref) == []          # added once
