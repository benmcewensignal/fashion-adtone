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
from datetime import date

import numpy as np

from . import config, registry, store
from .analysis import (Concept, amendment_frozen, eligible, gate, holm, load_concepts, permuted_shift, residuals,
                       split_sides, sufficient, toward)

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
                 draws: int = DRAWS, seed: int = 11) -> dict:
    rng = np.random.default_rng(seed)
    tz = {}
    for h in reg.group("treated"):
        d = _debut_in(h, window)
        if d is None:
            continue
        pre, post, _, _ = split_sides(by_house.get(h.id, []), res, d, config.POST_LAG_DAYS)
        if sufficient(pre, post):
            tz[h.id] = permuted_shift(pre, post, n_perm, rng)["z"]
    pz = []
    for h in reg.group("control"):
        for d in placebos:
            pre, post, _, _ = split_sides(by_house.get(h.id, []), res, d, config.POST_LAG_DAYS)
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
        return {"transfer": transfer_family(by, res, r, n_perm, away), "pooled_shift": pooled_shift(by, res, r, n_perm)}

    by_all = {}
    for c in concepts:
        by_all.setdefault(c.house_id, []).append(c)
    core_field = {h.id for h in reg.core().group("control")}
    return {
        "primary": family_on(primary, reg, field),
        "mix": {"creative_type": mix_change(by_all, reg, field="creative_type"),
                "category": mix_change(by_all, reg, field="category")},
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
    instrument = f"{config.RUBRIC_VERSION}@{config.CLAUDE_MODEL}"
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
    print(json.dumps({"reading": out["primary"]["transfer"]["reading"],
                      "pooled": {k: out["primary"]["pooled_shift"].get(k) for k in ("status", "mean_z", "p")}}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
