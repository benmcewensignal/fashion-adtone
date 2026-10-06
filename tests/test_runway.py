from datetime import date, timedelta

import numpy as np
import pytest

from adtone import registry, runway
from adtone.concepts import Concept


def world(k: float, seed: int = 4, houses: int = 8, years: int = 5):
    """Daily log page views. Shows spike by a random amount; with k > 0 a show also lifts the following
    months by k times its surprise. News spikes of the same size happen on other days and lift nothing."""
    rng = np.random.default_rng(seed)
    start = date(2018, 1, 1)
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


def test_the_bridge_reads_reach_and_alignment_and_leaves_saint_laurent_out():
    rng = np.random.default_rng(3)
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
        cs += [_concept(h, d + timedelta(days=30), show_dir, int(reach), f"{i}r")]
        cb.setdefault(h, []).extend(cs)
        lasting = 0.8 * np.log1p(reach) / 14 + 0.8 * mix + rng.normal(0, 0.05)
        recs.append({"house": h, "date": d, "surprise": float(rng.normal()), "lasting": float(lasting)})
    out = runway.bridge(recs, cb, boot=300)
    assert out["n_shows"] == 55 and out["excluded"] == ["saint_laurent"]
    c = out["coefficients"]
    assert c["alignment"]["interval_90"][0] > 0 and c["log_reach_after"]["interval_90"][0] > 0
