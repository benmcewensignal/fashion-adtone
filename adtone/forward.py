"""Forward tests: predictions frozen before the events they predict.

    python -m adtone.forward

Each design lives in forward/<id>.md, frozen by hash. Facts that arrive later (a successor's
name, a first show, a control's leadership change) go in forward/events.yml, which a design
reads but cannot change. A test computes nothing until its adjudication date, so there is no
peeking, and once a result is recorded it is never recomputed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import numpy as np
import yaml

from . import analysis, config, registry
from .concepts import Concept
from .score import check_frozen

FORWARD_DIR = config.ROOT / "forward"
FINAL = ("adjudicated", "void", "insufficient")
_SPEC = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)


def load_spec(path: Path, verify: bool = True) -> dict:
    if verify:
        check_frozen(path, path.with_suffix(".sha256"))
    m = _SPEC.search(path.read_text(encoding="utf-8"))
    if not m:
        raise ValueError(f"{path.name}: no machine-readable specification")
    return json.loads(m.group(1))


def load_events(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _date(v) -> date | None:
    if v in (None, ""):
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def _transfer(origin_ref, d_pre, d_post, refs: dict, own: str, n_perm: int, rng) -> dict:
    cos = analysis._cos
    t_obs = cos(d_post.vecs.mean(axis=0), origin_ref) - cos(d_pre.vecs.mean(axis=0), origin_ref)
    vecs, splits, exact, total = analysis.block_splits(d_pre, d_post, n_perm, rng)
    null = np.array([cos(vecs[b].mean(axis=0), origin_ref) - cos(vecs[a].mean(axis=0), origin_ref) for a, b in splits])
    p = analysis._p(null, t_obs, exact)
    names = list(refs)
    mat = np.array([refs[n] / (np.linalg.norm(refs[n]) or 1.0) for n in names])
    coef = np.linalg.solve(mat @ mat.T + config.MOVER_RIDGE * np.eye(len(names)),
                           mat @ (d_post.vecs.mean(axis=0) - d_pre.vecs.mean(axis=0)))
    beta = {n: round(float(b), 4) for n, b in zip(names, coef)}
    others = {n: b for n, b in beta.items() if n != own}
    specific = beta["origin"] > 0 and beta["origin"] == max(others.values())
    return {"transfer": round(t_obs, 4), "p": round(p, 4), "min_p": round(1 / total if exact else 1 / (1 + n_perm), 4),
            "coefficients": beta, "hit": bool(t_obs > 0 and p <= 0.05 and specific)}


def evaluate(spec: dict, events: dict, get_concepts: Callable[[], list[Concept]], reg: registry.Registry,
             today: date, n_perm: int = config.N_PERM, seed: int = 20261005) -> dict:
    house = spec["house"]
    d = _date(events.get(spec["event_key"]))
    if d is None:
        if today > date.fromisoformat(spec["void_after"]):
            return {"status": "void", "reason": f"no show by a new creative director by {spec['void_after']}"}
        return {"status": "awaiting_event"}
    adjudicate_on = d + timedelta(days=spec["adjudicate_after_days"])
    deadline = d + timedelta(days=spec["deadline_days"])
    base = {"event_date": d.isoformat(), "adjudicate_on": adjudicate_on.isoformat()}
    if today < adjudicate_on:
        return {"status": "awaiting_adjudication", **base}

    def not_yet(reason: str) -> dict:
        return {"status": "insufficient" if today >= deadline else "awaiting_data", "reason": reason, **base}

    changes = events.get("control_leadership_changes") or {}
    lookback = d - timedelta(days=spec["control_change_lookback_days"])
    dropped = sorted(h for h, when in changes.items() if _date(when) and lookback <= _date(when) <= today)
    field = {h.id for h in reg.group("control")} - {house} - set(dropped)
    cs = analysis.eligible(get_concepts())
    res = analysis.residuals(cs, field)
    by_house: dict[str, list[Concept]] = {}
    for c in cs:
        by_house.setdefault(c.house_id, []).append(c)
    lag = spec["post_lag_days"]
    rng = np.random.default_rng(seed)

    pre, post, _, _ = analysis.split_sides(by_house.get(house, []), res, d, lag)
    if not analysis.sufficient(pre, post):
        return not_yet(f"{house}: {pre.n} concepts / {pre.n_blocks} blocks before, {post.n} / {post.n_blocks} after")
    placebo = []
    for c in sorted(field):
        for k in spec["placebo_offsets_days"]:
            split = d + timedelta(days=k)
            a, b, _, _ = analysis.split_sides(by_house.get(c, []), res, split, lag)
            if analysis.sufficient(a, b):
                placebo.append({"house": c, "split": split.isoformat(), "z": analysis.permuted_shift(a, b, n_perm, rng)["z"]})
    n_controls = len({p["house"] for p in placebo})
    if n_controls < spec["min_controls"]:
        return not_yet(f"only {n_controls} controls have placebo splits")
    target = analysis.permuted_shift(pre, post, n_perm, rng)
    q = float(np.quantile([p["z"] for p in placebo], spec["placebo_quantile"]))
    p1 = {**target, "placebo_q": round(q, 3), "n_placebo": len(placebo), "hit": bool(target["z"] > q)}

    window = [c for c in by_house[house]
              if d - timedelta(days=spec["changepoint_days_before"]) <= c.first_seen <= today]
    cp = analysis.changepoint(window, res, n_perm, rng)
    if "p" in cp:
        cp["days_after_event"] = (date.fromisoformat(cp["split"]) - d).days
        cp["hit"] = bool(cp["p"] < spec["alpha"] and 0 <= cp["days_after_event"] <= spec["changepoint_max_days_after"])
    else:
        cp["hit"] = False

    origin = events.get(spec["origin_key"])
    announced = _date(events.get(spec["announce_key"]))
    if not origin:
        p3 = {"status": "not_applicable"}
    else:
        o_cs = [c for c in by_house.get(origin, [])
                if announced and announced - timedelta(days=spec["origin_window_days"]) <= c.first_seen < announced]
        if len(o_cs) < config.MIN_CONCEPTS_SIDE:
            p3 = {"status": "insufficient", "origin": origin, "n_origin": len(o_cs)}
        else:
            own = f"{house}:pre"
            refs = {"origin": np.mean([res[c.concept_id] for c in o_cs], axis=0), own: pre.vecs.mean(axis=0)}
            for h in sorted(field):
                hc = by_house.get(h, [])
                if h != origin and len(hc) >= config.MIN_CONCEPTS_SIDE:
                    refs[h] = np.mean([res[c.concept_id] for c in hc], axis=0)
            p3 = {"origin": origin, **_transfer(refs["origin"], pre, post, refs, own, n_perm, rng)}

    return {"status": "adjudicated", **base, "field": sorted(field), "dropped_controls": dropped,
            "prediction_1_magnitude": p1, "prediction_2_timing": cp, "prediction_3_transfer": p3}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.forward")
    ap.add_argument("--today", default=None)
    ap.add_argument("--instrument", default=f"{config.RUBRIC_VERSION}@{config.CLAUDE_MODEL}")
    ap.add_argument("--embedder", default=config.EMBED_TAG)
    ap.add_argument("--n-perm", type=int, default=config.N_PERM)
    a = ap.parse_args(argv)
    today = date.fromisoformat(a.today) if a.today else date.today()
    reg = registry.load()
    events = load_events(FORWARD_DIR / "events.yml")
    cache: dict[str, list[Concept]] = {}

    def get_concepts() -> list[Concept]:
        if "c" not in cache:
            cache["c"] = analysis.load_concepts(a.instrument, a.embedder, reg)[0]
        return cache["c"]

    for path in sorted(p for p in FORWARD_DIR.glob("*-v*.md") if re.fullmatch(r"[a-z0-9-]+-v\d+\.md", p.name)):
        # designs only: <id>-v<n>.md; an addendum such as <id>-v1-addendum-1.md is frozen with an amendment
        spec = load_spec(path)
        out = config.RESULTS_DIR / f"forward-{spec['id']}.json"
        if out.exists() and json.loads(out.read_text()).get("status") in FINAL:
            print(f"{spec['id']}: final result already recorded, not recomputed")
            continue
        due = _date(events.get(spec["event_key"]))
        if due and today >= due + timedelta(days=spec["adjudicate_after_days"]) and analysis.gate(reg):
            result = {"status": "awaiting_freeze", "reason": "; ".join(analysis.gate(reg))}
        else:
            result = evaluate(spec, events, get_concepts, reg, today, a.n_perm)
        result.update({"id": spec["id"], "evaluated_on": today.isoformat(), "instrument": a.instrument})
        config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, default=str) + "\n")
        print(f"{spec['id']}: {result['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
