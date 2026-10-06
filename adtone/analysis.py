"""Tonal shift analysis, as pre-registered in PREREGISTRATION.md.

    python -m adtone.analysis

Everything is measured on residuals: a concept's embedding minus the field's tone in
the same month. The field is the control houses only, each house's own contribution
left out. The 2025 wave moved too many houses at once for an all-house field to be
neutral: with half the panel changing, an all-house field would drift with them and
make the controls look as if they had moved.

Every null permutes whole campaign blocks. Results on the 2025 debuts are validation
of the instrument, run on history; under the standing rule they never count as calls.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from . import config, registry, store
from .concepts import Concept, build

RUBRIC_FIELDS = ("light", "colour_temperature", "saturation", "setting", "people", "framing",
                 "styling_register", "production", "primary_subject")


def month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def eligible(concepts: list[Concept], types=config.PRIMARY_TYPES,
             excluded=config.EXCLUDED_CATEGORIES) -> list[Concept]:
    return [c for c in concepts if c.creative_type in types and c.category not in excluded]


def residuals(concepts: list[Concept], field_houses: set[str]) -> dict[str, np.ndarray]:
    """concept_id -> residual against the leave-one-out control field for its month."""
    hm: dict[tuple[str, str], list[np.ndarray]] = defaultdict(list)
    for c in concepts:
        hm[(c.house_id, month_key(c.first_seen))].append(c.vec)
    hm_mean = {k: np.mean(v, axis=0) for k, v in hm.items()}
    overall: dict[str, np.ndarray] = {}
    for f in field_houses:
        vs = [c.vec for c in concepts if c.house_id == f]
        if vs:
            overall[f] = np.mean(vs, axis=0)
    out: dict[str, np.ndarray] = {}
    cache: dict[tuple[str, str], np.ndarray] = {}
    for c in concepts:
        m = month_key(c.first_seen)
        key = (c.house_id, m)
        if key not in cache:
            same_month = [hm_mean[(f, m)] for f in field_houses if f != c.house_id and (f, m) in hm_mean]
            if same_month:
                cache[key] = np.mean(same_month, axis=0)
            else:
                others = [v for f, v in overall.items() if f != c.house_id]
                cache[key] = np.mean(others, axis=0) if others else np.zeros_like(c.vec)
        out[c.concept_id] = c.vec - cache[key]
    return out


@dataclass
class Side:
    vecs: np.ndarray        # concepts x dims
    blocks: np.ndarray      # block label per concept

    @property
    def n(self) -> int:
        return len(self.vecs)

    @property
    def n_blocks(self) -> int:
        return len(set(self.blocks.tolist()))


def split_sides(cs: list[Concept], res: dict[str, np.ndarray], when: date, lag_days: int):
    pre = [c for c in cs if c.first_seen < when]
    post = [c for c in cs if c.first_seen >= when + timedelta(days=lag_days)]
    mk = lambda xs, tag: Side(np.array([res[c.concept_id] for c in xs]) if xs else np.zeros((0, 1)),
                              np.array([f"{tag}{c.block}" for c in xs]))
    return mk(pre, "a"), mk(post, "b"), pre, post


def _shift(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a.mean(axis=0) - b.mean(axis=0)))


MAX_EXACT = 20000


def block_splits(pre: Side, post: Side, n_perm: int, rng: np.random.Generator):
    """Ways of reassigning whole blocks between the sides, keeping each side's block count.

    Exhaustive when there are few enough (an exact test), otherwise a random sample.
    With few blocks the null has few distinct values, so the smallest attainable p is
    1 / C(blocks, blocks before): about 0.05 for three blocks a side. That floor is
    reported with every result rather than hidden.
    """
    vecs = np.vstack([pre.vecs, post.vecs])
    labels = np.concatenate([pre.blocks, post.blocks])
    uniq = sorted(set(labels.tolist()))
    idx = [np.nonzero(labels == u)[0] for u in uniq]
    n, k = len(uniq), pre.n_blocks
    total = math.comb(n, k)
    exact = total <= MAX_EXACT
    if exact:
        choices = itertools.combinations(range(n), k)
    else:
        choices = (tuple(rng.permutation(n)[:k]) for _ in range(n_perm))
    splits = []
    for ch in choices:
        chosen = set(ch)
        a = np.concatenate([idx[j] for j in range(n) if j in chosen])
        b = np.concatenate([idx[j] for j in range(n) if j not in chosen])
        splits.append((a, b))
    return vecs, splits, exact, total


def _p(null: np.ndarray, obs: float, exact: bool) -> float:
    ge = int((null >= obs - 1e-12).sum())
    return ge / len(null) if exact else (1 + ge) / (1 + len(null))


def permuted_shift(pre: Side, post: Side, n_perm: int, rng: np.random.Generator) -> dict:
    """Observed pre/post shift against a null that reassigns whole blocks between the sides."""
    obs = _shift(pre.vecs, post.vecs)
    vecs, splits, exact, total = block_splits(pre, post, n_perm, rng)
    null = np.array([_shift(vecs[a], vecs[b]) for a, b in splits])
    sd = float(null.std()) or 1e-12
    return {"shift": round(obs, 5), "null_mean": round(float(null.mean()), 5), "null_sd": round(sd, 5),
            "z": round((obs - float(null.mean())) / sd, 3), "p": round(_p(null, obs, exact), 4),
            "min_p": round(1 / total if exact else 1 / (1 + n_perm), 4), "exact": exact}


def sufficient(pre: Side, post: Side) -> bool:
    return (pre.n >= config.MIN_CONCEPTS_SIDE and post.n >= config.MIN_CONCEPTS_SIDE
            and pre.n_blocks >= config.MIN_BLOCKS_SIDE and post.n_blocks >= config.MIN_BLOCKS_SIDE)


def event_study(by_house: dict[str, list[Concept]], res: dict[str, np.ndarray], reg: registry.Registry,
                n_perm: int, seed: int = 20261005) -> dict:
    rng = np.random.default_rng(seed)
    treated: dict[str, dict] = {}
    dates = []
    for h in reg.group("treated"):
        d = h.debut.date
        dates.append(d)
        pre, post, _, _ = split_sides(by_house.get(h.id, []), res, d, config.POST_LAG_DAYS)
        entry = {"debut": d.isoformat(), "designer": h.debut.designer, "verified": h.debut.verified,
                 "n_pre": pre.n, "n_post": post.n, "blocks_pre": pre.n_blocks, "blocks_post": post.n_blocks}
        if sufficient(pre, post):
            entry.update(permuted_shift(pre, post, n_perm, rng))
        else:
            entry["status"] = "insufficient"
        treated[h.id] = entry
    placebo = []
    for h in reg.group("control"):
        for d in sorted(set(dates)):
            pre, post, _, _ = split_sides(by_house.get(h.id, []), res, d, config.POST_LAG_DAYS)
            if sufficient(pre, post):
                placebo.append({"house": h.id, "split": d.isoformat(), **permuted_shift(pre, post, n_perm, rng)})
    tz = {k: v["z"] for k, v in treated.items() if "z" in v}
    pz = [p["z"] for p in placebo]
    h1: dict = {"n_treated_sufficient": len(tz), "n_placebo": len(pz),
                "n_controls_with_placebo": len({p["house"] for p in placebo})}
    if len(tz) >= 4 and h1["n_controls_with_placebo"] >= 3:
        q90 = float(np.quantile(pz, 0.9))
        above = sorted(k for k, z in tz.items() if z > q90)
        frac = len(above) / len(tz)
        h1.update({"control_q90": round(q90, 3), "treated_above_q90": above, "fraction_above": round(frac, 3),
                   "supported": frac >= 0.6})
    else:
        h1["supported"] = None
        h1["status"] = "insufficient data for the pre-registered test"
    return {"treated": treated, "control_placebo": placebo, "h1": h1}


def changepoint(cs: list[Concept], res: dict[str, np.ndarray], n_perm: int, rng: np.random.Generator) -> dict:
    cs = sorted(cs, key=lambda c: (c.first_seen, c.concept_id))
    blocks: dict[int, list[Concept]] = defaultdict(list)
    for c in cs:
        blocks[c.block].append(c)
    order = [blocks[b] for b in sorted(blocks)]
    if len(order) < 2 * config.MIN_BLOCKS_SIDE:
        return {"status": "insufficient", "n_blocks": len(order)}

    def scan(seq):
        mats = [np.array([res[c.concept_id] for c in blk]) for blk in seq]
        sizes = np.array([len(m) for m in mats])
        allv = np.vstack(mats)
        cum = np.cumsum(sizes)
        best, best_k = -1.0, None
        for k in range(config.MIN_BLOCKS_SIDE, len(seq) - config.MIN_BLOCKS_SIDE + 1):
            nl = int(cum[k - 1])
            nr = len(allv) - nl
            if nl < config.MIN_CONCEPTS_SIDE or nr < config.MIN_CONCEPTS_SIDE:
                continue
            t = _shift(allv[:nl], allv[nl:]) * np.sqrt(nl * nr / (nl + nr))
            if t > best:
                best, best_k = t, k
        return best, best_k

    t_obs, k_obs = scan(order)
    if k_obs is None:
        return {"status": "insufficient", "n_blocks": len(order)}
    null = np.empty(n_perm)
    for i in range(n_perm):
        t, _ = scan([order[j] for j in rng.permutation(len(order))])
        null[i] = t
    return {"split": order[k_obs][0].first_seen.isoformat(), "stat": round(float(t_obs), 4),
            "p": round((1 + int((null >= t_obs).sum())) / (1 + n_perm), 4), "n_blocks": len(order),
            "n_concepts": sum(len(b) for b in order)}


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na and nb else 0.0


def mover(by_house: dict[str, list[Concept]], res: dict[str, np.ndarray], reg: registry.Registry,
          origin: str, destination: str, n_perm: int, seed: int = 7) -> dict:
    """Did the destination's tone move towards the origin's pre-departure tone, and towards it specifically?"""
    o, d = reg.by_id(origin), reg.by_id(destination)
    return toward(by_house, res, reg, origin, destination, o.debut.date, d.debut.date, n_perm, seed)


def toward(by_house: dict[str, list[Concept]], res: dict[str, np.ndarray], reg: registry.Registry,
           origin: str, destination: str, origin_cut: date, dest_cut: date, n_perm: int, seed: int = 7) -> dict:
    """The transfer test with explicit dates: the origin's look before origin_cut, the destination
    either side of dest_cut. mover() calls this with the registry's debut dates."""
    rng = np.random.default_rng(seed)
    o_pre, _, _, _ = split_sides(by_house.get(origin, []), res, origin_cut, config.POST_LAG_DAYS)
    d_pre, d_post, _, _ = split_sides(by_house.get(destination, []), res, dest_cut, config.POST_LAG_DAYS)
    out = {"origin": origin, "destination": destination, "n_origin_pre": o_pre.n, "n_dest_pre": d_pre.n,
           "n_dest_post": d_post.n}
    if o_pre.n < config.MIN_CONCEPTS_SIDE or not sufficient(d_pre, d_post):
        out["status"] = "insufficient"
        out["supported"] = None
        return out
    ref = o_pre.vecs.mean(axis=0)
    t_obs = _cos(d_post.vecs.mean(axis=0), ref) - _cos(d_pre.vecs.mean(axis=0), ref)
    vecs, splits, exact, total = block_splits(d_pre, d_post, n_perm, rng)
    null = np.array([_cos(vecs[b].mean(axis=0), ref) - _cos(vecs[a].mean(axis=0), ref) for a, b in splits])
    p = _p(null, t_obs, exact)
    out["min_p"] = round(1 / total if exact else 1 / (1 + n_perm), 4)
    # Specificity. A house that merely resembles the origin also gains when the destination
    # moves towards the origin, so ranking raw transfers rewards resemblance. Instead the
    # destination's movement is regressed on every reference tone at once, and the origin
    # has to carry the largest coefficient: credit for the movement beyond what lookalikes explain.
    refs = {origin: ref}
    rivals = {}
    g_pre, g_post = d_pre.vecs.mean(axis=0), d_post.vecs.mean(axis=0)
    for h in reg.houses:
        if h.id in (origin, destination) or h.group == "watch":
            continue   # watch houses are collected for forward tests, outside the v1 panel
        cs = by_house.get(h.id, [])
        if h.group == "treated":
            cs = [c for c in cs if c.first_seen < h.debut.date]
        if len(cs) < config.MIN_CONCEPTS_SIDE:
            continue
        r = np.mean([res[c.concept_id] for c in cs], axis=0)
        refs[h.id] = r
        rivals[h.id] = round(_cos(g_post, r) - _cos(g_pre, r), 4)
    # The destination's own pre tone joins the regression, so moving away from its old look is
    # not credited to whichever house happens to sit opposite it. Ridge shrinkage is needed
    # because the controls' residuals are built against each other and nearly sum to zero:
    # without it their coefficients inflate together along a direction the data cannot pin down.
    own = f"{destination}:pre"
    refs[own] = g_pre
    names = list(refs)
    mat = np.array([refs[n] / (np.linalg.norm(refs[n]) or 1.0) for n in names])
    lam = config.MOVER_RIDGE
    coef = np.linalg.solve(mat @ mat.T + lam * np.eye(len(names)), mat @ (g_post - g_pre))
    beta = {n: round(float(b), 4) for n, b in zip(names, coef)}
    others = {n: b for n, b in beta.items() if n != own}
    specific = beta[origin] > 0 and beta[origin] == max(others.values())
    rank = 1 + sum(1 for v in rivals.values() if v >= t_obs)
    out.update({"transfer": round(t_obs, 4), "p": round(p, 4), "rank_raw_transfer": rank,
                "n_references": len(names), "rivals": rivals, "coefficients": beta, "specific": specific,
                "supported": bool(t_obs > 0 and p <= 0.05 and specific)})
    return out


def holm(ps: list, m: int) -> list:
    """Holm-adjusted p-values over m planned tests; a test that could not run still counts in m."""
    out: list = [None] * len(ps)
    running = 0.0
    for rank, (p, i) in enumerate(sorted((p, i) for i, p in enumerate(ps) if p is not None)):
        running = max(running, min(1.0, (m - rank) * p))
        out[i] = round(running, 4)
    return out


def amendment_frozen(path=None) -> bool:
    """True when the amendment says FROZEN and still matches the hash it was frozen with."""
    path = path or config.AMENDMENT2_FILE
    sha = path.with_suffix(".sha256")
    if not path.exists() or not sha.exists():
        return False
    frozen = re.search(r"^STATUS: FROZEN", path.read_text(encoding="utf-8"), re.M)
    return bool(frozen) and hashlib.sha256(path.read_bytes()).hexdigest() == sha.read_text().strip()


def secondary_movers(by_house: dict[str, list[Concept]], res: dict[str, np.ndarray], reg: registry.Registry,
                     n_perm: int) -> dict:
    """The other designer moves inside the panel, each tested exactly like H2, Holm-adjusted as a pair."""
    ids = {h.id for h in reg.houses}
    tests = [mover(by_house, res, reg, o, d, n_perm) for o, d in config.SECONDARY_MOVERS if {o, d} <= ids]
    for t, adj in zip(tests, holm([t.get("p") for t in tests], m=len(config.SECONDARY_MOVERS))):
        t["p_holm"] = adj
        if "p" in t:
            t["supported"] = bool(t["transfer"] > 0 and adj <= 0.05 and t["specific"])
    registered = amendment_frozen()
    return {"registered": registered,
            "basis": "Amendment 2" if registered else "exploratory: Amendment 2 is not frozen",
            "tests": tests}


def rubric_deltas(pre: list[Concept], post: list[Concept], top: int = 8) -> list[dict]:
    def shares(cs, fld):
        vals = [v for v in (_concept_value(c, fld) for c in cs) if v is not None]
        n = len(vals) or 1
        return {k: v / n for k, v in Counter(vals).items()}
    rows = []
    for fld in RUBRIC_FIELDS:
        a, b = shares(pre, fld), shares(post, fld)
        for val in set(a) | set(b):
            rows.append({"field": fld, "value": val, "pre": round(a.get(val, 0), 3), "post": round(b.get(val, 0), 3),
                         "delta": round(b.get(val, 0) - a.get(val, 0), 3)})
    axis = lambda cs: [np.mean([o["street_couture_axis"] for o in c.outputs]) for c in cs if c.outputs]
    rows.sort(key=lambda r: -abs(r["delta"]))
    out = rows[:top]
    pa, pb = axis(pre), axis(post)
    if pa and pb:
        out.append({"field": "street_couture_axis", "value": "mean", "pre": round(float(np.mean(pa)), 2),
                    "post": round(float(np.mean(pb)), 2), "delta": round(float(np.mean(pb) - np.mean(pa)), 2)})
    return out


def _concept_value(c: Concept, fld: str):
    vals = [o[fld] for o in c.outputs if fld in o]
    if not vals:
        return None
    cnt = Counter(vals)
    best = max(cnt.values())
    return sorted(v for v, n in cnt.items() if n == best)[0]


def analyse(concepts: list[Concept], reg: registry.Registry, n_perm: int = config.N_PERM,
            types=config.PRIMARY_TYPES) -> dict:
    cs = eligible(concepts, types=types)
    field_houses = {h.id for h in reg.group("control")}
    res = residuals(cs, field_houses)
    by_house: dict[str, list[Concept]] = defaultdict(list)
    for c in cs:
        by_house[c.house_id].append(c)
    es = event_study(by_house, res, reg, n_perm)
    rng = np.random.default_rng(11)
    cps = {h.id: changepoint(by_house.get(h.id, []), res, n_perm, rng) for h in reg.houses}
    # H3: does the detector find the known breaks, and stay quiet on controls?
    hits, tested = [], 0
    for h in reg.group("treated"):
        cp = cps[h.id]
        if "p" not in cp:
            continue
        tested += 1
        lagd = (date.fromisoformat(cp["split"]) - h.debut.date).days
        cp["days_after_debut"] = lagd
        if cp["p"] < 0.05 and 0 <= lagd <= 120:
            hits.append(h.id)
    ctrl = [cps[h.id] for h in reg.group("control") if "p" in cps[h.id]]
    fp = [c for c in ctrl if c["p"] < 0.05]
    h3 = {"treated_tested": tested, "treated_hits": hits, "controls_tested": len(ctrl),
          "control_false_positives": len(fp)}
    if tested >= 4 and len(ctrl) >= 3:
        h3["supported"] = len(hits) / tested >= 0.5 and len(fp) / len(ctrl) <= 0.2
    else:
        h3["supported"] = None
    deltas = {}
    for h in reg.group("treated"):
        _, _, pre, post = split_sides(by_house.get(h.id, []), res, h.debut.date, config.POST_LAG_DAYS)
        if pre and post:
            deltas[h.id] = rubric_deltas(pre, post)
    mv = mover(by_house, res, reg, "balenciaga", "gucci", n_perm) if {"balenciaga", "gucci"} <= {h.id for h in reg.houses} else None
    mv2 = secondary_movers(by_house, res, reg, n_perm)
    coverage = {h.id: {"concepts": len(by_house.get(h.id, [])),
                       "blocks": len({c.block for c in by_house.get(h.id, [])}),
                       "first": min((c.first_seen for c in by_house.get(h.id, [])), default=None),
                       "last": max((c.first_seen for c in by_house.get(h.id, [])), default=None)} for h in reg.houses}
    for v in coverage.values():
        v["first"] = v["first"].isoformat() if v["first"] else None
        v["last"] = v["last"].isoformat() if v["last"] else None
    return {"types": list(types), "n_concepts": len(cs), "coverage": coverage, "event_study": es,
            "changepoints": cps, "h3": h3, "mover": mv, "movers_secondary": mv2, "rubric_deltas": deltas}


def gate(reg: registry.Registry, prereg_path=None) -> list[str]:
    """Reasons analysis may not run yet. Empty means it may."""
    reasons = []
    path = prereg_path or config.PREREG_FILE
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if not re.search(r"^STATUS: FROZEN", text, re.M):
        reasons.append("PREREGISTRATION.md is not frozen")
    if reg.status != "FROZEN":
        reasons.append("registry/houses.yml is not frozen")
    missing = [h.id for h in reg.houses if not h.page_ids and h.group != "watch"]
    if reg.status == "FROZEN" and missing:
        reasons.append(f"the frozen registry has no confirmed pages for {', '.join(missing)}")
    return reasons


def confirmed_only(ads: dict[str, dict], reg: registry.Registry) -> dict[str, dict]:
    """Drop ads from pages that were collected provisionally but never confirmed."""
    allowed = {h.id: set(h.page_ids) for h in reg.houses if h.page_ids}
    return {k: a for k, a in ads.items() if a.get("page_id") in allowed.get(a.get("house_id"), ())}


def load_concepts(instrument: str, embed_tag: str, reg: registry.Registry | None = None) -> tuple[list[Concept], dict]:
    from .embed import VectorStore
    ads = store.ShardedTable(config.ADS_DIR, "ad_id").rows
    if reg is not None:
        ads = confirmed_only(ads, reg)
    media = store.ShardedTable(config.MEDIA_DIR, "ad_id").rows
    rubric_version = instrument.split("@", 1)[0]
    obs = {r["sha"]: r["output"] for r in store.read_jsonl(config.OBS_DIR / f"{rubric_version}.jsonl")
           if r["instrument"] == instrument and r.get("status") == "ok"}
    vecs = VectorStore(embed_tag).vecs
    info = {"ads": len(ads), "media_rows": len(media), "scored_images": len(obs), "vectors": len(vecs)}
    return build(ads, media, obs, vecs), info


def report_md(summary: dict) -> str:
    p = summary["primary"]
    h1, h3, mv = p["event_study"]["h1"], p["h3"], p["mover"] or {}
    word = lambda x: "not testable yet" if x is None else ("supported" if x else "not supported")
    lines = [
        "# fashion-adtone results", "",
        f"Generated {summary['generated_at']}. {summary['status']}", "",
        f"Instrument `{summary['instrument']}`, embedder `{summary['embedder']}`, "
        f"{p['n_concepts']} brand-image concepts.", "",
        f"- H1, the 2025 debuts moved house tone more than controls moved: {word(h1.get('supported'))} "
        f"({h1.get('n_treated_sufficient', 0)} treated houses with enough data).",
        f"- H2, Gucci moved towards Balenciaga's pre-Piccioli tone: {word(mv.get('supported'))}.",
        f"- H3, the detector finds known breaks and stays quiet on controls: {word(h3.get('supported'))}.",
        *[f"- {t['destination']} towards {t['origin']}'s tone before its new director "
          f"({p['movers_secondary']['basis']}): {word(t.get('supported'))}."
          for t in p.get("movers_secondary", {}).get("tests", [])],
        "", "Per-house detail is in summary.json.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.analysis")
    ap.add_argument("--instrument", default=f"{config.RUBRIC_VERSION}@{config.CLAUDE_MODEL}")
    ap.add_argument("--embedder", default=config.EMBED_TAG)
    ap.add_argument("--n-perm", type=int, default=config.N_PERM)
    args = ap.parse_args(argv)
    reg = registry.load()
    reasons = gate(reg)
    if reasons:
        # Not a failure: the weekly schedule simply waits until both files are frozen.
        print("analysis waits until the design is frozen: " + "; ".join(reasons))
        return 0
    concepts, info = load_concepts(args.instrument, args.embedder, reg)
    core = reg.core()   # H1 to H3 read the frozen v1 panel only; extension houses are Amendment 2's
    summary = {
        "status": "VALIDATION: retrospective analysis of the 2025 debuts. Derived analysis, never calls.",
        "generated_at": store.utc_now(), "instrument": args.instrument, "embedder": args.embedder,
        "registry_status": reg.status, "inputs": info,
        "params": {k: getattr(config, k) for k in ("PHASH_MAX_DIST", "BLOCK_GAP_DAYS", "POST_LAG_DAYS",
                                                   "MIN_BLOCKS_SIDE", "MIN_CONCEPTS_SIDE")} | {"n_perm": args.n_perm},
        "block_rule": config.BLOCK_RULE,
        "primary": analyse(concepts, core, args.n_perm, config.PRIMARY_TYPES),
        "sensitivity": analyse(concepts, core, args.n_perm, config.SENSITIVITY_TYPES)["event_study"]["h1"],
    }
    # Amendment 1: the pre-registered 21-day gap rule, reported for H1 beside the primary result.
    from .concepts import assign_blocks
    assign_blocks(concepts, "gap21")
    summary["sensitivity_gap21"] = analyse(concepts, core, args.n_perm, config.PRIMARY_TYPES)["event_study"]["h1"]
    assign_blocks(concepts, config.BLOCK_RULE)
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.RESULTS_DIR / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    (config.RESULTS_DIR / "report.md").write_text(report_md(summary))
    store.append_jsonl(config.PROV_DIR / "analyse.jsonl",
                       [{"generated_at": summary["generated_at"], "instrument": args.instrument, **info}])
    print(report_md(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
