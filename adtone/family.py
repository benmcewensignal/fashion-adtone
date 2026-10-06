"""Amendment 2 analyses: the transfer family, the pooled shift, the ad mix, declared sensitivities.

    python -m adtone.family     # after the analysis; writes data/results/family.json

Exploratory until PREREGISTRATION-AMENDMENT-2.md is frozen by hash, registered once it is. H1 to H3
stay exactly as frozen on the core panel; nothing here replaces them.

Transfer is a family, not one pair. Each move is scored twice: does the destination move towards the
look the designer left behind (toward), and does the origin move away from it (away, the origin's own
shift)? The moves differ in who travelled with the designer, which is what lets the pattern of results
say whether a look belongs to the designer, to the image-makers or to neither.

The late-September 2025 debuts are one shock, not seven events: the pooled shift averages them and
compares the average with averages of control shifts at fashion-week placebo dates.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, timedelta

import numpy as np

from . import config, registry, store
from .analysis import (Concept, amendment_frozen, changepoint, eligible, gate, holm, load_concepts, permuted_shift,
                       residuals, split_sides, sufficient, toward)

# label, origin, destination, origin cut (None: the origin's registry debut), who travelled with the designer
TRANSFERS = (
    ("demna", "balenciaga", "gucci", None, "designer"),             # H2 itself, frozen; here for the pattern
    ("blazy", "bottega_veneta", "chanel", None, "image-makers"),     # Soth, the Thornfeldts, Fortune
    ("anderson", "loewe", "dior", None, "team"),                     # Sims, Bruno, Delhomme, Bartlett
    ("chiuri", "dior", "fendi", None, "designer alone"),             # nobody on file: the contrast
    ("mulier", "alaia", "versace", date(2026, 3, 10), "not yet known"),  # extension; his Alaia era ends March 2026
)
FAMILY_SIZE = 4                     # Holm over blazy, anderson, chiuri and mulier; H2 keeps its own rule
CLUSTER = (date(2025, 9, 23), date(2025, 10, 6))
PLACEBO_DATES = (date(2026, 3, 2), date(2026, 6, 23), date(2026, 9, 28))   # Paris fashion weeks in the window
ART_DIRECTION_CHANGED = ("miu_miu", "valentino")                          # 2026 changes in the credits
DRAWS = 20000
DETECT_HORIZON_DAYS = 120          # the frozen detection window
# The kill rule, fixed before any data. Breach any one and the 2025 calibration fails: the Saint Laurent
# prediction is not scored, and nothing in this amendment is reported as a finding.
KILL = {"control_false_positive_rate": 0.20, "in_time_placebo_rate": 0.20,
        "season_control_rate": 0.20, "season_ratio": 0.5}


def modal(c: Concept, field: str):
    vals = [o.get(field) for o in c.outputs if o.get(field) is not None]
    vals = [v if not isinstance(v, list) else tuple(v) for v in vals]
    return Counter(vals).most_common(1)[0][0] if vals else None


def mean_confidence(c: Concept) -> float:
    vals = [float(o["confidence"]) for o in c.outputs if o.get("confidence") is not None]
    return float(np.mean(vals)) if vals else 0.0


# ---------- the transfer family ----------

def transfer_family(by_house, res, reg: registry.Registry, n_perm: int, away: dict[str, dict] | None = None) -> dict:
    ids = {h.id for h in reg.houses}
    tests = []
    for label, o, d, cut, moved in TRANSFERS:
        entry = {"label": label, "origin": o, "destination": d, "moved_with": moved}
        if not {o, d} <= ids:
            tests.append({**entry, "status": "not in panel"})
            continue
        o_cut = cut or reg.by_id(o).debut.date
        t = toward(by_house, res, reg, o, d, o_cut, reg.by_id(d).debut.date, n_perm)
        entry.update(t)
        if away and o in away:
            entry["away"] = {k: away[o].get(k) for k in ("z", "p", "status") if k in away[o]}
        tests.append(entry)
    rest = [t for t in tests if t["label"] != "demna"]
    for t, adj in zip(rest, holm([t.get("p") for t in rest], m=FAMILY_SIZE)):
        t["p_holm"] = adj
        if t.get("p") is not None:
            t["supported"] = bool(t["transfer"] > 0 and adj <= 0.05 and t["specific"])
    return {"tests": tests, "reading": attribution(tests)}


def attribution(tests: list[dict]) -> dict:
    """The reading fixed before the data: which pattern of transfers points where."""
    ok = {t["label"]: t.get("supported") for t in tests}
    known = {k: v for k, v in ok.items() if v is not None}
    if not known:
        return {"reading": "untested", "why": "no transfer test had enough data"}
    if ok.get("chiuri"):
        return {"reading": "designer", "why": "the look moved with Chiuri, whom nobody on file followed"}
    image = [k for k in ("blazy", "anderson") if ok.get(k)]
    if image == ["blazy"]:
        return {"reading": "photographers", "why": "only the move whose photographers travelled transferred"}
    if image:
        tail = " and not with Chiuri, who moved alone" if ok.get("chiuri") is False else ""
        return {"reading": "image-makers", "why": "the look moved with the teams that travelled" + tail}
    if ok.get("demna"):
        return {"reading": "designer and photographer, inseparable",
                "why": "only Demna's move transferred, and he photographs his own campaigns"}
    return {"reading": "no transfer", "why": "no move in the family transferred"}


# ---------- one shock, not seven ----------

def _debut_in(h: registry.House, window: tuple[date, date]) -> date | None:
    for e in h.events:
        if e.kind == "designer_debut" and window[0] <= e.date <= window[1]:
            return e.date
    return None


def pooled_shift(by_house, res, reg: registry.Registry, n_perm: int, window=CLUSTER, placebos=PLACEBO_DATES,
                 draws: int = DRAWS, seed: int = 11, res_for: dict[str, dict] | None = None) -> dict:
    rng = np.random.default_rng(seed)
    res_for = res_for or {}
    tz = {}
    for h in reg.group("treated"):
        d = _debut_in(h, window)
        if d is None:
            continue
        pre, post, _, _ = split_sides(by_house.get(h.id, []), res_for.get(h.id, res), d, config.POST_LAG_DAYS)
        if sufficient(pre, post):
            tz[h.id] = permuted_shift(pre, post, n_perm, rng)["z"]
    pz = []
    for h in reg.group("control"):
        for d in placebos:
            pre, post, _, _ = split_sides(by_house.get(h.id, []), res_for.get(h.id, res), d, config.POST_LAG_DAYS)
            if sufficient(pre, post):
                pz.append({"house": h.id, "split": d.isoformat(), "z": permuted_shift(pre, post, n_perm, rng)["z"]})
    out = {"cluster": [window[0].isoformat(), window[1].isoformat()], "treated_z": tz, "n_placebo": len(pz),
           "placebo_dates": [d.isoformat() for d in placebos]}
    if len(tz) < 3 or len(pz) < 6:
        return {**out, "status": "insufficient", "reason": "needs three cluster houses and six placebo shifts"}
    obs = float(np.mean(list(tz.values())))
    pool = np.array([p["z"] for p in pz])
    null = rng.choice(pool, size=(draws, len(tz)), replace=True).mean(axis=1)
    p = float((1 + np.sum(null >= obs - 1e-12)) / (1 + draws))
    flagged = sum(1 for p_ in pz if p_["z"] > float(np.quantile(pool, 0.95)))
    return {**out, "status": "ok", "mean_z": round(obs, 3), "p": round(p, 4),
            "control_false_positive_rate": round(float(np.mean(pool > 1.645)), 3), "placebo_z_flagged": flagged}


# ---------- two falsifications the frozen tests do not cover ----------

def in_time_placebo(by_house, res, reg: registry.Registry, n_perm: int, seed: int = 13) -> dict:
    """Treated houses should not shift at a fake debut inside their own pre-debut period.

    The fake date is the median date of the house's pre-debut concepts, with no gap after it: the
    archive keeps a year, so pre-debut history is short, and a fixed offset with the frozen 90-day gap
    left most houses untestable. Only pre-debut concepts are used, so the real change cannot leak in.
    Houses with more than one event in the window (Versace) are left out: their earlier event is real."""
    rng = np.random.default_rng(seed)
    houses = {}
    for h in reg.group("treated"):
        if len([e for e in h.events if e.kind in ("designer_debut", "designer_exit")]) > 1:
            continue
        real = h.debut.date
        cs = sorted((c for c in by_house.get(h.id, []) if c.first_seen < real), key=lambda c: c.first_seen)
        if len(cs) < 2 * config.MIN_CONCEPTS_SIDE:
            houses[h.id] = {"status": "insufficient"}
            continue
        fake = cs[len(cs) // 2].first_seen
        pre, post, _, _ = split_sides(cs, res, fake, 0)
        houses[h.id] = ({**permuted_shift(pre, post, n_perm, rng), "fake_date": fake.isoformat()}
                        if sufficient(pre, post) else {"status": "insufficient"})
    tested = [v for v in houses.values() if "p" in v]
    rate = round(float(np.mean([v["p"] < 0.05 for v in tested])), 3) if tested else None
    return {"rule": "median pre-debut date, no gap", "houses": houses, "n_tested": len(tested), "rate": rate}


def season_check(by_house, res, reg: registry.Registry, n_perm: int, window=CLUSTER,
                 horizon: int = DETECT_HORIZON_DAYS, seed: int = 17) -> dict:
    """Is the detector finding debuts, or finding September? Controls scanned blind should not break
    inside the same fashion weeks as often as the treated houses break after their debuts."""
    rng = np.random.default_rng(seed)
    end = window[1] + timedelta(days=horizon)

    def scan(hid):
        return changepoint(by_house.get(hid, []), res, n_perm, rng)

    treated, controls = {}, {}
    for h in reg.group("treated"):
        d = _debut_in(h, window)
        if d is None:
            continue
        cp = scan(h.id)
        if "p" in cp:
            split = date.fromisoformat(cp["split"])
            treated[h.id] = {**cp, "hit": bool(cp["p"] < 0.05 and d <= split <= d + timedelta(days=horizon))}
    for h in reg.group("control"):
        cp = scan(h.id)
        if "p" in cp:
            split = date.fromisoformat(cp["split"])
            controls[h.id] = {**cp, "any_break": bool(cp["p"] < 0.05),
                              "september_break": bool(cp["p"] < 0.05 and window[0] <= split <= end)}
    t_rate = float(np.mean([v["hit"] for v in treated.values()])) if treated else None
    c_sep = float(np.mean([v["september_break"] for v in controls.values()])) if controls else None
    c_any = float(np.mean([v["any_break"] for v in controls.values()])) if controls else None
    passes = None
    if t_rate is not None and c_sep is not None:
        passes = bool(c_sep <= KILL["season_control_rate"] and (t_rate == 0 or c_sep <= KILL["season_ratio"] * t_rate))
    return {"window": [window[0].isoformat(), end.isoformat()], "treated": treated, "controls": controls,
            "treated_hit_rate": None if t_rate is None else round(t_rate, 3),
            "control_september_rate": None if c_sep is None else round(c_sep, 3),
            "control_false_positive_rate": None if c_any is None else round(c_any, 3), "passes": passes}


# ---------- the kill rule ----------

def kill_rule(primary: dict, mix: dict | None = None) -> dict:
    """Written before the data: when the 2025 calibration fails, nothing is narrated."""
    reasons = []
    season = primary.get("season_check") or {}
    fpr = season.get("control_false_positive_rate")
    if fpr is not None and fpr > KILL["control_false_positive_rate"]:
        reasons.append(f"controls raise a false alarm in {fpr:.0%} of scans, above {KILL['control_false_positive_rate']:.0%}")
    if season.get("passes") is False:
        reasons.append("controls break inside the same fashion weeks too often: the detector is finding September")
    placebo = primary.get("in_time_placebo") or {}
    if placebo.get("rate") is not None and placebo["rate"] > KILL["in_time_placebo_rate"]:
        reasons.append(f"treated houses shift at fake pre-debut dates in {placebo['rate']:.0%} of tests")
    pooled = primary.get("pooled_shift") or {}
    if mix and pooled.get("status") == "ok" and pooled.get("p", 1) > 0.05:
        houses = [v for v in mix.get("houses", {}).values() if v.get("control_percentile") is not None]
        if houses and np.mean([v["control_percentile"] >= 0.95 for v in houses]) >= 0.5:
            reasons.append("the ad mix changed but the brand-image look did not: the change is mix, not look")
    tested = bool(season.get("controls")) or placebo.get("n_tested")
    if not tested:
        return {"verdict": "not yet testable", "reasons": [], "saint_laurent": "pending"}
    return {"verdict": "fail" if reasons else "pass", "reasons": reasons,
            "saint_laurent": "not scored" if reasons else "scored as frozen"}


# ---------- the ad mix, reported as its own outcome ----------

def _shares(cs: list[Concept], field: str) -> Counter:
    return Counter(modal(c, field) for c in cs)


def _tvd(a: Counter, b: Counter) -> float:
    na, nb = sum(a.values()) or 1, sum(b.values()) or 1
    return 0.5 * sum(abs(a[k] / na - b[k] / nb) for k in set(a) | set(b))


def mix_change(by_house_all, reg: registry.Registry, placebos=PLACEBO_DATES, field: str = "creative_type") -> dict:
    """How far the mix of ad types moved at each debut, against the controls at fashion-week placebos."""
    def split(cs, d):
        return [c for c in cs if c.first_seen < d], [c for c in cs if c.first_seen >= d]
    null = []
    for h in reg.group("control"):
        for d in placebos:
            a, b = split(by_house_all.get(h.id, []), d)
            if len(a) >= config.MIN_CONCEPTS_SIDE and len(b) >= config.MIN_CONCEPTS_SIDE:
                null.append(_tvd(_shares(a, field), _shares(b, field)))
    houses = {}
    for h in reg.group("treated"):
        a, b = split(by_house_all.get(h.id, []), h.debut.date)
        if len(a) < config.MIN_CONCEPTS_SIDE or len(b) < config.MIN_CONCEPTS_SIDE:
            houses[h.id] = {"status": "insufficient"}
            continue
        v = _tvd(_shares(a, field), _shares(b, field))
        houses[h.id] = {"tvd": round(v, 3), "control_percentile": round(float(np.mean(np.array(null) < v)), 3) if null else None}
    return {"field": field, "n_placebo": len(null), "houses": houses}


# ---------- declared sensitivities ----------

def high_confidence(cs: list[Concept], floor: float = 0.6) -> list[Concept]:
    return [c for c in cs if mean_confidence(c) >= floor]


def no_text(cs: list[Concept]) -> list[Concept]:
    """Drop concepts whose images carry a logo or words: the channel through which a reader can recognise the house."""
    return [c for c in cs if modal(c, "text_in_image") == "none"]


def without(reg: registry.Registry, drop: tuple[str, ...]) -> registry.Registry:
    return registry.Registry(version=reg.version, status=reg.status, houses=[h for h in reg.houses if h.id not in drop])


def run(concepts: list[Concept], reg: registry.Registry, n_perm: int = config.N_PERM) -> dict:
    from .analysis import event_study
    primary = eligible(concepts)
    field = {h.id for h in reg.group("control")}

    def family_on(cs, r, fld):
        res = residuals(cs, fld)
        by = {}
        for c in cs:
            by.setdefault(c.house_id, []).append(c)
        away = event_study(by, res, r, n_perm)["treated"]
        return {"transfer": transfer_family(by, res, r, n_perm, away), "pooled_shift": pooled_shift(by, res, r, n_perm),
                "in_time_placebo": in_time_placebo(by, res, r, n_perm), "season_check": season_check(by, res, r, n_perm)}

    def owner_fields(cs, r, fld):
        """Residuals for each house against controls of other owners only: a group-wide media policy
        cannot then move a treated house and its control together."""
        controls = {h.id: h.owner for h in r.group("control")}
        out = {}
        for owner in {h.owner for h in r.houses}:
            res_o = residuals(cs, {c for c in fld if controls.get(c) != owner})
            for h in r.houses:
                if h.owner == owner:
                    out[h.id] = res_o
        return out

    by_all = {}
    for c in concepts:
        by_all.setdefault(c.house_id, []).append(c)
    core_field = {h.id for h in reg.core().group("control")}
    main = family_on(primary, reg, field)
    by_primary = {}
    for c in primary:
        by_primary.setdefault(c.house_id, []).append(c)
    mix = {"creative_type": mix_change(by_all, reg, field="creative_type"),
           "category": mix_change(by_all, reg, field="category")}
    return {
        "primary": main,
        "kill_rule": kill_rule(main, mix["creative_type"]),
        "owner_clustered": {"pooled_shift": pooled_shift(by_primary, residuals(primary, field), reg, n_perm,
                                                         res_for=owner_fields(primary, reg, field))},
        "mix": mix,
        "sensitivity": {
            "high_confidence": family_on(high_confidence(primary), reg, field),
            "no_logo_or_text": family_on(no_text(primary), reg, field),
            "core_controls_only": family_on(primary, reg, core_field),
            "without_art_direction_changes": family_on(primary, without(reg, ART_DIRECTION_CHANGED),
                                                       field - set(ART_DIRECTION_CHANGED)),
        },
    }


def main(argv: list[str] | None = None) -> int:
    reg = registry.load()
    reasons = gate(reg)
    if reasons:
        print("Amendment 2 analyses wait until the design is frozen: " + "; ".join(reasons))
        return 0
    instrument = config.instrument()
    concepts, info = load_concepts(instrument, config.EMBED_TAG, reg)
    watch = {h.id for h in reg.group("watch")} - {"alaia"}
    concepts = [c for c in concepts if c.house_id not in watch]
    if not concepts:
        print("no collected concepts yet")
        return 0
    out = {"status": "Amendment 2" if amendment_frozen() else "EXPLORATORY: Amendment 2 is not frozen",
           "generated_at": store.utc_now(), "instrument": instrument, "inputs": info, **run(concepts, reg)}
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.RESULTS_DIR / "family.json").write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(json.dumps({"kill_rule": out["kill_rule"], "reading": out["primary"]["transfer"]["reading"],
                      "pooled": {k: out["primary"]["pooled_shift"].get(k) for k in ("status", "mean_z", "p")}}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
