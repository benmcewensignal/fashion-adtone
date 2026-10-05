"""Testability and power under the real calendar.

The repository keeps an ad for a year after its last impression, so a collection that starts
on C holds every ad still running on or after C minus 365 days. A house's before side for a
debut on D is the ads launched before D and still running a year before C. How much that
leaves depends on two things unknown until the first backfill: how long luxury ads run, and
how often brand imagery launches (which sets how many campaign blocks exist). This sweeps both,
and compares the frozen 21-day gap rule for blocks with calendar-month blocks.

    python scripts/power.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from adtone import analysis, registry  # noqa: E402
from adtone.concepts import assign_blocks  # noqa: E402
from adtone.registry import Registry  # noqa: E402
from adtone.synth import make_world  # noqa: E402

analysis.MAX_EXACT = 200      # sample the null instead of enumerating it: power estimates, not final tests
N_PERM = 49
REG = registry.load()
PANEL = Registry(REG.version, "SIM", [h for h in REG.houses if h.group != "watch"])
COLLECTION = date(2026, 10, 6)


def concepts(cadence, median_days, effect, mover, seed, collection=COLLECTION):
    start = date(2024, 10, 1)
    days = (collection - start).days
    spec = {h.id: {"debut": h.debut.date if h.debut else None, "shift": effect if h.group == "treated" else 0.0}
            for h in PANEL.houses}
    w = make_world(spec, dim=32, noise=0.12, campaign_sd=0.08, start=start, days=days,
                   campaigns=max(2, round(cadence * days / 365)), mover=mover, seed=seed)
    rng = np.random.default_rng(seed + 1000)
    retain_from = collection - timedelta(days=365)
    return [c for c in w.concepts
            if c.first_seen <= collection and c.first_seen + timedelta(days=float(rng.lognormal(np.log(median_days), 0.6))) >= retain_from]


def set_blocks(cs, rule):
    assign_blocks(cs, rule)


def run(cadence, median_days, rule, effect, reps, mover=("balenciaga", "gucci", 0.6), collection=COLLECTION):
    rows = []
    for r in range(reps):
        cs = concepts(cadence, median_days, effect, mover if effect else None, seed=100 * r + cadence + median_days,
                      collection=collection)
        set_blocks(cs, rule)
        out = analysis.analyse(cs, PANEL, N_PERM)
        h1, mv, h3 = out["event_study"]["h1"], out["mover"] or {}, out["h3"]
        rows.append({"n_suff": h1["n_treated_sufficient"], "h1": h1.get("supported"), "h2": mv.get("supported"),
                     "h3": h3.get("supported"),
                     "suff": [k for k, v in out["event_study"]["treated"].items() if "z" in v]})
    share = lambda key, val: sum(1 for x in rows if x[key] is val) / len(rows)
    return {"n_suff": np.mean([x["n_suff"] for x in rows]),
            "h1_testable": 1 - share("h1", None), "h1_yes": share("h1", True),
            "h2_testable": 1 - share("h2", None), "h2_yes": share("h2", True),
            "h3_testable": 1 - share("h3", None), "h3_yes": share("h3", True),
            "houses": {h.id: sum(h.id in x["suff"] for x in rows) / len(rows) for h in PANEL.group("treated")}}


if __name__ == "__main__":
    print("cadence | run length | blocks | treated testable | H1 testable/supported | H2 | H3")
    results = {}
    rules = sys.argv[1:] or ["gap21", "hybrid"]
    for rule in rules:
        for cadence in (6, 12, 24):
            for med in (21, 60):
                res = run(cadence, med, rule, 0.75, reps=6)
                results[(rule, cadence, med)] = res
                print(f"{cadence:>3}/yr | {med:>3} d | {rule:6} | {res['n_suff']:.1f} of 10 | "
                      f"{res['h1_testable']:.0%} / {res['h1_yes']:.0%} | {res['h2_testable']:.0%} / {res['h2_yes']:.0%} | "
                      f"{res['h3_testable']:.0%} / {res['h3_yes']:.0%}", flush=True)
    print(f"\nper-house share of runs with a testable before and after side ({rules[-1]} blocks, 12/yr, 60 d):")
    for h, v in results[(rules[-1], 12, 60)]["houses"].items():
        print(f"  {h}: {v:.0%}")
    print("\nnull worlds (no effect), false support:")
    for rule in rules:
        res = run(12, 60, rule, 0.0, reps=10)
        print(f"  {rule}: H1 supported {res['h1_yes']:.0%} of runs (testable {res['h1_testable']:.0%}), H3 {res['h3_yes']:.0%}")
    print(f"\ncollection two months later (1 Dec 2026), {rules[-1]} blocks, 12/yr, 60 d:")
    late = run(12, 60, rules[-1], 0.75, reps=6, collection=date(2026, 12, 1))
    print(f"  treated testable {late['n_suff']:.1f} of 10, H1 testable {late['h1_testable']:.0%}, supported {late['h1_yes']:.0%}")
