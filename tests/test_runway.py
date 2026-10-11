from datetime import date, timedelta

import numpy as np
import pytest

from adtone import registry, runway
from adtone.concepts import Concept


def world(k: float, seed: int = 4, houses: int = 8, years: int = 5, start: date = date(2018, 1, 1)):
    """Daily log page views. Shows spike by a random amount; with k > 0 a show also lifts the following
    months by k times its surprise. News spikes of the same size happen on other days and lift nothing."""
    rng = np.random.default_rng(seed)
    n = 365 * years
    series, shows = {}, {}
    for h in range(houses):
        y = 8 + rng.normal(0, 0.03, n).cumsum() * 0.2 + rng.normal(0, 0.05, n)
        dates = []
        for yr in range(years):
            for day in (60, 160, 270):
                t = yr * 365 + day + int(rng.integers(-5, 6))
                if t + 160 < n:
                    dates.append(t)
        sizes = rng.uniform(0.3, 1.5, len(dates))
        for t, s in zip(dates, sizes):
            y[t:t + 6] += s * np.exp(-np.arange(min(6, n - t)) / 2)
            y[t + 10:t + 150] += k * (s - 0.9)
        for t in rng.choice(np.arange(70, n - 160), size=years * 5, replace=False):
            if all(abs(t - d) > 40 for d in dates):
                s = rng.uniform(0.3, 1.5)
                y[t:t + 6] += s * np.exp(-np.arange(6) / 2)
        hid = f"h{h}"
        series[hid] = {start + timedelta(days=i): float(v) for i, v in enumerate(y)}
        shows[hid] = [start + timedelta(days=t) for t in dates]
    return runway.Panel(series), shows


def test_sticky_show_attention_is_told_apart_from_ordinary_spikes():
    panel, shows = world(k=0.5)
    out = runway.sticky_test(panel, shows, reps=200)
    assert out["status"] == "ok" and out["slope"] > out["placebo_q95"] and out["p"] < 0.05


def test_show_attention_that_decays_like_any_spike_is_not_called_sticky():
    panel, shows = world(k=0.0, seed=5)
    out = runway.sticky_test(panel, shows, reps=200)
    assert out["status"] == "ok" and out["p"] > 0.05


def test_spike_and_lasting_are_read_from_the_right_windows():
    start = date(2020, 1, 1)
    y = np.full(400, 5.0)
    y[200] += 2.0                      # the show
    y[230:330] += 0.5                  # a lasting lift a month to four months later
    flat = {start + timedelta(days=i): 5.0 for i in range(400)}
    p = runway.Panel({"a": {start + timedelta(days=i): float(v) for i, v in enumerate(y)}, "b": flat, "c": flat})
    sp, la = p.at("a", start + timedelta(days=200))
    assert sp == pytest.approx(2.0) and la == pytest.approx(0.5 * 91 / 91, rel=0.02)


def test_shipped_show_dates_are_verified_and_belong_to_registry_houses():
    shows = runway.load_shows()
    ids = {h.id for h in registry.load().houses}
    assert len(shows) >= 35 and all(r["house"] in ids for r in shows)
    assert len({(r["house"], r["date"]) for r in shows}) == len(shows)
    assert all(r["source"] for r in shows)


def _concept(h, d, vec, reach, i):
    return Concept(house_id=h, concept_id=f"{h}{i}", first_seen=d, ad_ids=[str(i)], shas=[str(i)],
                   vec=np.asarray(vec, float), reach=reach)


def test_the_bridge_reads_the_campaigns_lift_from_its_landing_and_leaves_saint_laurent_out():
    """Each house shows once. Its campaign for the collection lands 60 to 62 days later (the third concept on day
    62), and its page views rise from the landing by an amount set by the reach pushed from it and by how much
    the campaign resembles the show-period ads. A leather-goods concept with the show's look is not the
    campaign: it carries the reach but must not count towards the alignment."""
    rng = np.random.default_rng(3)
    start, n = date(2025, 10, 1), 1100
    views = {}
    recs, cb = [], {}
    for i in range(60):
        h = "saint_laurent" if i < 5 else f"h{i}"          # one show per house: windows never overlap
        d = date(2026, 1, 1) + timedelta(days=200 * i if i < 5 else 7 * i)
        reach = float(10 ** rng.uniform(3, 6))          # log-uniform, so its logarithm actually varies
        show_dir = rng.normal(size=8)
        mix = rng.uniform(0, 1)
        camp_dir = mix * show_dir + (1 - mix) * rng.normal(size=8)
        cs = [_concept(h, d + timedelta(days=k), show_dir + rng.normal(0, .1, 8), 0, f"{i}s{k}") for k in range(3)]
        cs += [_concept(h, d + timedelta(days=60 + k), camp_dir + rng.normal(0, .1, 8), 0, f"{i}c{k}") for k in range(3)]
        cs += [_concept(h, d + timedelta(days=67), show_dir, int(reach), f"{i}bag")]
        cb.setdefault(h, []).extend(cs)
        y = 5.0 + rng.normal(0, 0.01, n)
        t = (d + timedelta(days=62) - start).days
        y[t:] += 0.8 * np.log1p(reach) / 14 + 0.8 * mix + rng.normal(0, 0.02)
        views[h] = {start + timedelta(days=j): float(v) for j, v in enumerate(y)}
        recs.append({"house": h, "date": d, "surprise": float(rng.normal())})
    panel = runway.Panel(views)
    out = runway.bridge(recs, cb, panel, is_collection=lambda c: not c.concept_id.endswith("bag"), boot=300)
    assert out["n_shows"] == 55 and out["excluded"] == ["saint_laurent"] and out["target"] == "campaign_lift"
    c = out["coefficients"]
    assert c["alignment"]["interval_90"][0] > 0 and c["log_reach_campaign"]["interval_90"][0] > 0
    f = runway.bridge_features(cb["h9"], date(2026, 1, 1) + timedelta(days=63), (45, 150),
                               lambda c: not c.concept_id.endswith("bag"))
    assert f["landing"] == date(2026, 1, 1) + timedelta(days=63 + 62) and f["n_campaign_ads"] == 3


def test_a_campaign_lands_when_its_third_concept_for_the_collection_is_first_seen():
    d = date(2026, 3, 1)
    cs = [_concept("a", d + timedelta(days=k), np.ones(4), 0, f"x{k}") for k in (50, 70, 90, 95)]
    cs.append(_concept("a", d + timedelta(days=60), np.ones(4), 0, "bag"))
    is_coll = lambda c: not c.concept_id.endswith("bag")          # noqa: E731
    assert runway.landing(cs, d, (45, 150), is_coll) == d + timedelta(days=90)
    assert runway.landing(cs, d, (45, 150)) == d + timedelta(days=70)     # counting the bag, it lands earlier
    assert runway.landing(cs, d, (45, 80), is_coll) is None              # two in the window: not landed


def test_the_campaigns_lift_runs_from_its_landing_and_stops_before_the_next_show():
    start = date(2026, 1, 1)
    y = np.full(400, 5.0)
    y[200:] += 0.3                                      # the campaign lands on day 200
    flat = {start + timedelta(days=i): 5.0 for i in range(400)}
    risen = {start + timedelta(days=i): float(v) for i, v in enumerate(y)}
    market = {start + timedelta(days=i): 5.0 + (0.1 if i >= 200 else 0.0) for i in range(400)}
    p = runway.Panel({"a": risen, "b": market, "c": market, "d": flat})
    landed = start + timedelta(days=200)
    assert runway.campaign_lift(p, "a", landed) == pytest.approx(0.3 - 0.1)   # net of the median house
    assert runway.campaign_lift(p, "a", landed, landed + timedelta(days=50)) == pytest.approx(0.2)
    assert runway.campaign_lift(p, "a", landed, landed + timedelta(days=35)) is None   # under 30 days before the cut
    assert runway.campaign_lift(p, "a", start + timedelta(days=30)) is None             # no baseline before it


def test_an_ad_counts_for_the_collection_by_its_text_or_else_by_a_full_outfit():
    ads = {"1": {"line": "collection"}, "2": {"line": "other"}, "3": {"line": "accessories"}, "4": {}}
    sort = runway.collection_sorter(ads, worn_full={"full"})
    c = lambda ids, shas: Concept("a", "k", date(2026, 1, 1), ids, shas, np.ones(2))   # noqa: E731
    assert sort(c(["1", "2"], ["x"]))                   # one version names the collection
    assert not sort(c(["1", "3"], ["full"]))            # a version names the bag: leather goods, never the campaign
    assert sort(c(["2", "4"], ["full"]))                # the text names nothing: a full outfit worn counts
    assert not sort(c(["2", "4"], ["part"]))


def test_holding_the_scene_constant_takes_out_what_appointments_explain():
    rng = np.random.default_rng(8)
    recs = []
    for h in range(12):
        for k in range(8):
            c = float(rng.integers(0, 3))              # new ambassadors before the show
            recs.append({"house": f"h{h}", "surprise": c + rng.normal(0, 0.3), "lasting": 0.5 * c + rng.normal(0, 0.05),
                         "scene": c})
    assert runway.within_slope(recs) > 0.3                                  # the guests pass for stickiness
    assert abs(runway.within_slope(recs, control="scene")) < 0.05           # held constant, nothing is left


def test_the_scene_counts_appointments_in_the_180_days_before_and_is_blank_before_the_file_starts():
    sc = runway.Scene({"a": [date(2024, 1, 10), date(2024, 5, 1), date(2023, 1, 1)]})
    assert sc.at("a", date(2024, 6, 1)) == 2.0
    assert sc.at("b", date(2024, 6, 1)) == 0.0
    assert sc.at("a", date(2019, 3, 1)) is None           # the file starts in 2019: the 180 days are not all covered


def test_the_scene_run_draws_the_registered_placebos_and_leaves_the_registered_run_alone():
    panel, shows = world(k=0.5, start=date(2020, 1, 1))
    registered = runway.sticky_test(panel, shows, reps=200)
    same = runway.sticky_test(panel, shows, reps=200, scene=runway.Scene({}))      # no appointments anywhere
    assert same["slope"] == registered["slope"] and same["p"] == registered["p"]
    assert same["held_constant"] and "held_constant" not in registered
