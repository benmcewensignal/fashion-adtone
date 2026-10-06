"""A hypothetical season: every analysis run on simulated ads, on the real calendar.

    python scripts/demo.py               # both worlds
    python scripts/demo.py --world null

NOTHING HERE IS A FINDING. The registry, debut dates and owners are real; the ads are simulated, with
effects planted so the output can be checked against a known answer. Two worlds:

  team   what the cast tab calls "it travels with the team": every debut shifts the house's look, and
         Demna, Blazy and Anderson carry their old house's look to the new one; Chiuri carries nothing.
  null   nothing is planted at all. A sound instrument says no to everything here.

Simulated looks have 16 dimensions rather than the fingerprint's 512, and two full years of ads where
the real archive will hold less (docs/POWER.md), so real results will be noisier than these.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from adtone import analysis, family, registry, runway  # noqa: E402
from adtone.synth import make_world  # noqa: E402

SHIFTS = {"gucci": 1.4, "chanel": 1.1, "dior": 0.9, "balenciaga": 0.8, "loewe": 0.7, "bottega_veneta": 0.6,
          "celine": 0.6, "fendi": 0.5, "margiela": 0.5, "jil_sander": 0.3}
MOVERS = [("balenciaga", "gucci", 0.5), ("bottega_veneta", "chanel", 0.4), ("loewe", "dior", 0.4)]


def concepts_for(kind: str, reg, seed: int):
    spec = {}
    for h in reg.houses:
        planted = kind == "team" and h.group == "treated"
        spec[h.id] = {"debut": h.debut.date if planted else None, "shift": SHIFTS.get(h.id, 0.0) if planted else 0.0}
    return make_world(spec, movers=MOVERS if kind == "team" else [], start=date(2024, 10, 1), days=730,
                      campaigns=24, seed=seed).concepts


def attention_world(sticky: float, seed: int = 4, houses: int = 12, years: int = 6):
    """Daily log page views with three shows a year; with sticky > 0 a show lifts the months after it
    in proportion to its surprise. News spikes of the same size happen on other days and lift nothing."""
    rng = np.random.default_rng(seed)
    start, n = date(2019, 1, 1), 365 * years
    series, shows = {}, {}
    for h in range(houses):
        y = 8 + rng.normal(0, 0.03, n).cumsum() * 0.2 + rng.normal(0, 0.05, n)
        dates = [yr * 365 + d + int(rng.integers(-5, 6)) for yr in range(years) for d in (60, 160, 270)]
        dates = [t for t in dates if t + 160 < n]
        for t, s in zip(dates, rng.uniform(0.3, 1.5, len(dates))):
            y[t:t + 6] += s * np.exp(-np.arange(6) / 2)
            y[t + 10:t + 150] += sticky * (s - 0.9)
        for t in rng.choice(np.arange(70, n - 160), size=years * 5, replace=False):
            if all(abs(t - d) > 40 for d in dates):
                y[t:t + 6] += rng.uniform(0.3, 1.5) * np.exp(-np.arange(6) / 2)
        series[f"house{h}"] = {start + timedelta(days=i): float(v) for i, v in enumerate(y)}
        shows[f"house{h}"] = [start + timedelta(days=t) for t in dates]
    return runway.Panel(series), shows


def report(kind: str, n_perm: int, seed: int) -> None:
    reg = registry.load().core()
    t0 = time.time()
    cs = concepts_for(kind, reg, seed)
    field = {h.id for h in reg.group("control")}
    res = analysis.residuals(cs, field)
    by: dict = {}
    for c in cs:
        by.setdefault(c.house_id, []).append(c)
    print(f"\n=== world: {kind}  ({len(cs)} simulated concepts across {len(by)} houses; HYPOTHETICAL) ===")

    es = analysis.event_study(by, res, reg, n_perm)
    h1 = es["h1"]
    zs = {k: round(v["z"], 1) for k, v in es["treated"].items() if "z" in v}
    print(f"H1 shift   supported={h1.get('supported')}  {len(h1.get('treated_above_q90', []))} of "
          f"{h1['n_treated_sufficient']} debuts beat the control 90th percentile ({h1.get('control_q90')})")
    print("           z by house:", dict(sorted(zs.items(), key=lambda kv: -kv[1])))

    h2 = analysis.mover(by, res, reg, "balenciaga", "gucci", n_perm)
    keys = [k for k in ("transfer", "p", "specific", "largest_coefficient", "status") if k in h2]
    print("H2 Demna  ", {k: h2[k] for k in keys})

    out = family.run(cs, reg, n_perm)
    prim = out["primary"]
    print("Transfer family reading:", prim["transfer"]["reading"])
    for t in prim["transfer"]["tests"]:
        if t.get("status") == "not in panel":
            continue
        away = t.get("away") or {}
        print(f"   {t['label']:9s} {t['origin']} -> {t['destination']}: transfer={t.get('transfer')} p={t.get('p')} "
              f"specific={t.get('specific')} | old house moved away p={away.get('p')}")
    ps = prim["pooled_shift"]
    print(f"Pooled September shift: status={ps.get('status')} mean z={ps.get('mean_z')} p={ps.get('p')}")
    oc = out["owner_clustered"]["pooled_shift"]
    print(f"   against other owners' controls only: mean z={oc.get('mean_z')} p={oc.get('p')}")
    ip, sc = prim["in_time_placebo"], prim["season_check"]
    print(f"In-time placebo: {ip.get('n_tested')} houses tested, {ip.get('rate')} shifted at a fake date (limit 0.20)")
    print(f"September check: treated hit {sc.get('treated_hit_rate')}, controls broke in the same weeks "
          f"{sc.get('control_september_rate')}, any false alarm {sc.get('control_false_positive_rate')}; passes={sc.get('passes')}")
    k = out["kill_rule"]
    print(f"Kill rule: {k['verdict']}; Saint Laurent prediction {k['saint_laurent']}", k["reasons"] or "")
    print(f"({time.time() - t0:.0f} s)")


def readings(kind: str, seeds: range, n_perm: int = 99) -> None:
    """How often the transfer family's reading, fixed in advance, names what was planted."""
    from collections import Counter
    reg = registry.load().core()
    field = {h.id for h in reg.group("control")}
    tally, hits = Counter(), Counter()
    for seed in seeds:
        cs = concepts_for(kind, reg, seed)
        res = analysis.residuals(cs, field)
        by: dict = {}
        for c in cs:
            by.setdefault(c.house_id, []).append(c)
        tf = family.transfer_family(by, res, reg, n_perm, analysis.event_study(by, res, reg, n_perm)["treated"])
        tally[tf["reading"]["reading"]] += 1
        hits.update(t["label"] for t in tf["tests"] if t.get("supported"))
    print(f"{kind}: readings over {len(seeds)} seasons {dict(tally)}; moves supported {dict(hits)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", choices=["team", "null", "both"], default="both")
    ap.add_argument("--n-perm", type=int, default=199)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--readings", type=int, default=0, help="also tally the reading over this many seasons per world")
    a = ap.parse_args(argv)
    for kind in (["team", "null"] if a.world == "both" else [a.world]):
        report(kind, a.n_perm, a.seed)
    print("\n=== runway to attention (HYPOTHETICAL page views, 12 houses, six years of shows) ===")
    for sticky in (0.5, 0.0):
        panel, shows = attention_world(sticky)
        r = runway.sticky_test(panel, shows, reps=300)
        print(f"shows {'leave a lasting lift' if sticky else 'decay like any spike'}: slope={r['slope']} "
              f"placebo 95th={r.get('placebo_q95')} p={r.get('p')} over {r['n_with_surprise']} shows")
    if a.readings:
        print(f"\n=== the transfer family's reading over {a.readings} seasons per world (HYPOTHETICAL) ===")
        for kind in ("team", "null"):
            readings(kind, range(100, 100 + a.readings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
