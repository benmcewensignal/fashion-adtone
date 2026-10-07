import json
from datetime import date, timedelta

import numpy as np
import pytest

from adtone import guard, press, runway
from adtone.registry import House, Registry


class R:
    def __init__(self, status=200, body=None, text=None):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")

    def json(self):
        return self._body if self._body is not None else json.loads(self.text)


def _timeline(start: date, days: int, value):
    return {"timeline": [{"series": "x", "data": [
        {"date": (start + timedelta(days=i)).strftime("%Y%m%dT000000Z"), "value": value(i), "norm": 1000} for i in range(days)]}]}


class FakeGdelt:
    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        if self.fail_on and self.fail_on in params["query"]:
            return R(text="Your search contained a phrase that is too common.")
        s = date(int(params["startdatetime"][:4]), int(params["startdatetime"][4:6]), int(params["startdatetime"][6:8]))
        e = date(int(params["enddatetime"][:4]), int(params["enddatetime"][4:6]), int(params["enddatetime"][6:8]))
        days = (e - s).days + 1
        if params["mode"] == "timelinevolraw":
            return R(body=_timeline(s, days, lambda i: i % 3))          # every third day has no articles
        return R(body=_timeline(s, days, lambda i: -1.5))


def test_a_window_joins_volume_and_tone_and_leaves_tone_blank_without_articles():
    rows = press.window(FakeGdelt(), '"Prada"', date(2026, 1, 1), date(2026, 1, 6), sleep=lambda s: None)
    assert len(rows) == 6 and rows[0]["articles"] == 0 and rows[0]["tone"] is None
    assert rows[1]["articles"] == 1 and rows[1]["tone"] == -1.5 and rows[1]["total"] == 1000


def test_gdelt_refusals_are_reported_not_parsed_as_empty():
    with pytest.raises(RuntimeError, match="too common"):
        press.fetch(FakeGdelt(fail_on="Prada"), '"Prada"', date(2026, 1, 1), date(2026, 1, 2), "timelinetone",
                    sleep=lambda s: None)


def test_ambiguous_names_search_with_fashion_context():
    assert "fashion" in press.query_for(House("celine", "Celine", "treated", "G", [], [], []))
    assert press.query_for(House("prada", "Prada", "control", "G", [], [], [])) == '"Prada"'


def test_the_backfill_resumes_where_the_budget_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(press, "paths", lambda: {"dir": tmp_path / "press", "state": tmp_path / "press.json",
                                                 "prov": tmp_path / "press.jsonl"})
    monkeypatch.setattr(press, "START", date(2026, 1, 1))
    monkeypatch.setattr(press, "WINDOW_DAYS", 90)
    reg = Registry(1, "T", [House("prada", "Prada", "control", "G", [], [], []),
                            House("gucci", "Gucci", "treated", "G", [], [], [])])
    ticks = iter(range(0, 10_000, 30))
    st = press.collect(FakeGdelt(), reg, "r1", end=date(2026, 12, 31), budget_s=60, sleep=lambda s: None,
                       clock=lambda: next(ticks))
    assert st["complete"] == [] and "prada" in st["covered"]
    st = press.collect(FakeGdelt(), reg, "r2", end=date(2026, 12, 31), budget_s=10_000, sleep=lambda s: None)
    assert st["complete"] == ["gucci", "prada"]
    rows = [json.loads(l) for l in (tmp_path / "press" / "prada.jsonl").read_text().splitlines()]
    assert len(rows) == 365 and rows[0]["date"] == "2026-01-01" and rows[0]["query"] == '"Prada"'


def test_reception_is_the_tone_after_a_show_against_the_usual_tone():
    start = date(2025, 1, 1)
    days = 400
    views = {start + timedelta(days=i): 8.0 for i in range(days)}
    panel = runway.Panel({"a": views, "b": views})
    show = start + timedelta(days=200)
    tone = {start + timedelta(days=i): {"articles": 5, "tone": -1.0} for i in range(days)}
    for k in range(4):
        tone[show + timedelta(days=k)] = {"articles": 40, "tone": 1.5}
    pp = runway.PressPanel(panel, {"a": tone})
    got = pp.at("a", show)
    assert got["reception"] == pytest.approx(2.5) and got["press_spike"] == pytest.approx(np.log1p(40) - np.log1p(5))
    assert pp.at("b", show)["reception"] is None


def _rows(effect: float, seed: int, houses: int = 12, shows: int = 12):
    rng = np.random.default_rng(seed)
    rows = []
    for h in range(houses):
        level = rng.normal(0, 1)
        for k in range(shows):
            spike, mom, rec = rng.normal(0, 1), rng.normal(0, 1), rng.normal(0, 1) + 0.5 * level
            lasting = level + 0.4 * spike + 0.3 * mom + effect * rec + rng.normal(0, 0.6)
            rows.append({"house": f"h{h}", "date": date(2020, 1, 1) + timedelta(days=60 * k), "spike": spike,
                         "momentum": mom, "reception": rec, "lasting": lasting})
    return rows


def test_clothes_that_matter_are_found_and_clothes_that_do_not_are_not():
    yes = runway.what_the_clothes_add(_rows(0.35, 1), perms=300, boot=200)
    assert yes["status"] == "ok" and yes["supported"] and yes["increment"] > 0.03
    no = runway.what_the_clothes_add(_rows(0.0, 2), perms=300, boot=200)
    assert no["status"] == "ok" and not no["supported"]


def test_a_house_whose_press_is_always_warm_does_not_pass_for_good_clothes():
    rng = np.random.default_rng(3)
    rows = []
    for h in range(12):
        level = rng.normal(0, 1)          # some houses get warmer press and more lasting attention, show after show
        for k in range(12):
            spike, mom = rng.normal(0, 1), rng.normal(0, 1)
            rows.append({"house": f"h{h}", "date": date(2020, 1, 1) + timedelta(days=60 * k), "spike": spike,
                         "momentum": mom, "reception": 3 * level + rng.normal(0, 1),
                         "lasting": 2 * level + 0.4 * spike + rng.normal(0, 0.6)})
    pooled = np.corrcoef([r["reception"] for r in rows], [r["lasting"] for r in rows])[0, 1]
    assert pooled > 0.6                                       # the naive reading: warm press, lasting attention
    assert not runway.what_the_clothes_add(rows, perms=300, boot=200)["supported"]   # within houses: nothing
    assert runway.what_the_clothes_add(rows[:20], perms=50, boot=50)["status"].startswith("insufficient")


def test_the_thread_is_one_row_per_show_with_every_source(tmp_path):
    start = date(2025, 1, 1)
    views = {start + timedelta(days=i): 8.0 + (0.5 if 200 <= i <= 203 else 0) for i in range(420)}
    panel = runway.Panel({"a": views, "b": {d: 8.0 for d in views}})
    shows = {"a": [start + timedelta(days=60), start + timedelta(days=130), start + timedelta(days=200)]}
    pp = runway.PressPanel(panel, {"a": {d: {"articles": 3, "tone": 0.2} for d in views}})
    rows = runway.event_table(panel, shows, pp)
    assert len(rows) == 3 and {"spike", "lasting", "reception", "momentum"} <= set(rows[-1])
    runway.write_table(rows, tmp_path / "t.csv")
    head = (tmp_path / "t.csv").read_text().splitlines()[0]
    assert head == ",".join(runway.TABLE_FIELDS)


def test_the_guard_knows_the_press_files_and_the_thread():
    assert guard.violations("press", ["data/press/prada.jsonl", "data/state/press.json"]) == []
    assert guard.violations("attention", ["data/press/prada.jsonl"])   # press has its own daily workflow now
    assert guard.violations("analyse", ["data/results/runway_events.csv"]) == []


def _press_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(press, "paths", lambda: {"dir": tmp_path / "press", "state": tmp_path / "press.json",
                                                 "prov": tmp_path / "press.jsonl"})


def test_a_year_comes_back_in_one_window_when_it_is_daily(tmp_path, monkeypatch):
    _press_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(press, "START", date(2024, 1, 1))
    fake = FakeGdelt()
    reg = Registry(1, "T", [House("prada", "Prada", "control", "G", [], [], [])])
    st = press.collect(fake, reg, "r1", end=date(2025, 12, 31), sleep=lambda s: None)
    assert st["complete"] == ["prada"] and len(fake.calls) == 4          # two windows, two modes each


class WeeklyGdelt(FakeGdelt):
    """Answers spans longer than 90 days with one point a week, as a coarse timeline would."""
    def get(self, url, params=None, timeout=None):
        r = super().get(url, params, timeout)
        body = r._body
        if body and len(body["timeline"][0]["data"]) > 90:
            body["timeline"][0]["data"] = body["timeline"][0]["data"][::7]
        return R(body=body)


def test_a_coarse_timeline_drops_the_house_to_short_windows(tmp_path, monkeypatch):
    _press_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(press, "START", date(2025, 1, 1))
    reg = Registry(1, "T", [House("prada", "Prada", "control", "G", [], [], [])])
    st = press.collect(WeeklyGdelt(), reg, "r1", end=date(2025, 12, 31), sleep=lambda s: None)
    assert st["span"] == {"prada": press.SHORT_DAYS} and st["complete"] == ["prada"]
    rows = [json.loads(l) for l in (tmp_path / "press" / "prada.jsonl").read_text().splitlines()]
    assert len(rows) == 365


class Refusing(FakeGdelt):
    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        return R(status=429, text="")


def test_a_rate_limit_is_waited_out_twice_then_stops_the_run(tmp_path, monkeypatch):
    _press_paths(monkeypatch, tmp_path)
    reg = Registry(1, "T", [House("prada", "Prada", "control", "G", [], [], []),
                            House("gucci", "Gucci", "treated", "G", [], [], [])])
    fake, waits = Refusing(), []
    st = press.collect(fake, reg, "r1", end=date(2026, 12, 31), sleep=waits.append)
    assert st["rate_limited"] and st["failed"] == {} and len(fake.calls) == 12    # one window, three rounds, then stop
    assert {30.0, 60.0, 120.0} <= set(waits) and waits.count(press.RATE_PAUSE_S) == 2
    assert "HTTP 429" in st["last_error"]


class Pleading(FakeGdelt):
    """GDELT's other way of saying no: HTTP 200 and a sentence instead of JSON."""
    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        if len(self.calls) <= 2:
            return R(text="Please limit requests to one every 5 seconds or contact kalev.leetaru5@gmail.com for larger queries.")
        return super().get(url, params, timeout)


def test_a_plea_to_slow_down_is_a_refusal_not_a_failed_house(tmp_path, monkeypatch):
    _press_paths(monkeypatch, tmp_path)
    reg = Registry(1, "T", [House("prada", "Prada", "control", "G", [], [], [])])
    fake, waits = Pleading(), []
    st = press.collect(fake, reg, "r1", end=date(2017, 3, 31), sleep=waits.append)
    assert st["failed"] == {} and not st["rate_limited"] and st["complete"] == ["prada"]
    assert 30.0 in waits and 60.0 in waits


def test_page_views_wait_and_retry_after_too_many_requests():
    from adtone import attention

    class Wiki:
        def __init__(self):
            self.n = 0

        def get(self, url, timeout=None):
            self.n += 1
            if self.n < 3:
                r = R(status=429, text="")
                r.headers = {"Retry-After": "7"}
                return r
            r = R(body={"items": [{"timestamp": "2026010100", "views": 12}]})
            r.headers = {}
            return r
    waits = []
    got = attention.daily_views(Wiki(), "Prada", date(2026, 1, 1), date(2026, 1, 1), sleep=waits.append)
    assert got == {"2026-01-01": 12} and waits == [7.0, 7.0]
