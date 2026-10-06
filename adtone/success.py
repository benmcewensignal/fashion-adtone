"""Change and success, broadly, as two descriptive questions.

    python -m adtone.success    # after the analysis; writes data/results/success.json

Association. For each house that changed director, its measured shift (the event-study z from the
analysis) is set against the change in its Wikipedia attention: the log ratio of mean daily views in
the 182 days after the debut over the 182 days before, less the median change of the control houses
over the same days. Spearman's rank correlation, a permutation p-value, and the size a correlation
must reach to stand out from noise with this many houses.

Leader drift. Whether the other houses' advertising moves towards the house the Lyst Index ranks
first. For each spell of one leader, each other house's similarity to the leader's look in that spell
is compared with its similarity to the same look in the six months before. The average change is
ranked against the same change measured towards every other house put in the leader's place.
With no convergence the leader ranks first about as often as chance; with partial convergence it
ranks first most of the time. When nearly every house converges heavily, the stand-in leaders inherit
the leader's look and the ranking understates it, so a modest rank alongside a large mean change
means heavy convergence, not none.

Neither is causal. Houses change designers when they are struggling, a new designer changes product,
prices and crew at once, attention follows any debut, and the Lyst Index changed its method in the
first quarter of 2026, the quarter Chanel took the lead.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import defaultdict
from datetime import date, timedelta

import numpy as np

from . import config, registry, store

WINDOW_DAYS = 182
MIN_COVERAGE = 0.8      # share of days needed on each side of the anchor
MIN_CONTROLS = 3
BEFORE_MONTHS = 6
N_PERM = 20000
LEADERS_FILE = config.ROOT / "reference" / "lyst_leaders.csv"
RANKS_FILE = config.ROOT / "reference" / "lyst_ranks.csv"


def load_lyst_ranks(path=RANKS_FILE, method: str | None = "lyst-v2") -> dict[str, dict[str, int]]:
    """quarter -> house_id -> rank, panel houses only, one method. Ranks are compared only within a
    method: Lyst changed it in Q1 2026 and began counting Chanel and Dior."""
    import csv
    out: dict[str, dict[str, int]] = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["house_id"] and (method is None or r["method"] == method):
                out.setdefault(r["quarter"], {})[r["house_id"]] = int(r["rank"])
    return out
OUT_NAME = "success.json"


# ---------- association: shift against attention ----------

def log_change(series: dict[date, int], anchor: date, window: int = WINDOW_DAYS) -> float | None:
    lo, hi = anchor - timedelta(days=window), anchor + timedelta(days=window)
    before = [v for d, v in series.items() if lo <= d < anchor]
    after = [v for d, v in series.items() if anchor <= d < hi]
    if min(len(before), len(after)) < MIN_COVERAGE * window:
        return None
    mb, ma = float(np.mean(before)), float(np.mean(after))
    return float(np.log(ma / mb)) if mb > 0 and ma > 0 else None


def attention_changes(series: dict[str, dict[date, int]], reg: registry.Registry,
                      window: int = WINDOW_DAYS) -> dict[str, dict]:
    """Each treated house's attention change around its debut, less the controls' median change there."""
    controls = [h.id for h in reg.group("control")]
    out = {}
    for h in reg.group("treated"):
        a = h.debut.date
        own = log_change(series.get(h.id, {}), a, window)
        ctrl = [c for c in (log_change(series.get(k, {}), a, window) for k in controls) if c is not None]
        entry = {"anchor": a.isoformat(), "change": None if own is None else round(own, 4), "controls_used": len(ctrl)}
        if own is not None and len(ctrl) >= MIN_CONTROLS:
            med = float(np.median(ctrl))
            entry.update({"control_median": round(med, 4), "relative": round(own - med, 4)})
        out[h.id] = entry
    return out


def ranks(a) -> np.ndarray:
    """Average ranks, ties shared (as scipy's rankdata 'average')."""
    a = np.asarray(a, dtype=float)
    order = np.argsort(a, kind="mergesort")
    inv = np.empty(len(a), dtype=int)
    inv[order] = np.arange(len(a))
    s = a[order]
    first = np.r_[True, s[1:] != s[:-1]]
    dense = first.cumsum()[inv]
    bounds = np.r_[np.nonzero(first)[0], len(a)]
    return 0.5 * (bounds[dense] + bounds[dense - 1] + 1)


def spearman(x, y) -> float:
    rx, ry = ranks(x), ranks(y)
    rx, ry = rx - rx.mean(), ry - ry.mean()
    den = float(np.sqrt((rx ** 2).sum() * (ry ** 2).sum()))
    return float((rx * ry).sum() / den) if den else 0.0


def association(shift: dict[str, float], attention: dict[str, float], n_perm: int = N_PERM, seed: int = 3) -> dict:
    houses = sorted(set(shift) & set(attention))
    if len(houses) < 5:
        return {"status": "insufficient", "n": len(houses), "houses": houses,
                "reason": "needs at least five houses with both a measured shift and an attention change"}
    x = np.array([shift[h] for h in houses], dtype=float)
    y = np.array([attention[h] for h in houses], dtype=float)
    rho = spearman(x, y)
    rx, ry = ranks(x), ranks(y)
    rx, ry = rx - rx.mean(), ry - ry.mean()
    den = float(np.sqrt((rx ** 2).sum() * (ry ** 2).sum())) or 1.0
    rng = np.random.default_rng(seed)
    perms = np.array([rng.permutation(ry) for _ in range(n_perm)])
    null = perms @ rx / den
    p = float((1 + np.sum(np.abs(null) >= abs(rho) - 1e-12)) / (1 + n_perm))
    return {"status": "ok", "n": len(houses), "rho": round(rho, 4), "p": round(p, 4),
            "bar_95": round(float(np.quantile(np.abs(null), 0.95)), 4),
            "points": [{"house": h, "shift_z": round(float(shift[h]), 4), "attention": round(float(attention[h]), 4)}
                       for h in houses]}


# ---------- leader drift: does advertising converge on the hottest house? ----------

def leaders(path=LEADERS_FILE) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]
    for i, r in enumerate(rows, start=2):
        if not re.match(r"^\d{4}Q[1-4]$", r["quarter"]) or not r["house_id"] or not r["source_url"].startswith("https://"):
            raise ValueError(f"{path.name} line {i}: needs a quarter like 2026Q1, a house id and an https source")
    return rows


def _month(m: str, k: int) -> str:
    y, mm = map(int, m.split("-"))
    t = y * 12 + (mm - 1) + k
    return f"{t // 12}-{t % 12 + 1:02d}"


def leader_by_month(rows: list[dict]) -> dict[str, str]:
    out = {}
    for r in rows:
        y, q = int(r["quarter"][:4]), int(r["quarter"][-1])
        for m in range(3 * q - 2, 3 * q + 1):
            out[f"{y}-{m:02d}"] = r["house_id"]
    return out


def spells(by_month: dict[str, str]) -> list[tuple[str, list[str]]]:
    out: list[tuple[str, list[str]]] = []
    for m in sorted(by_month):
        h = by_month[m]
        if out and out[-1][0] == h and _month(out[-1][1][-1], 1) == m:
            out[-1][1].append(m)
        else:
            out.append((h, [m]))
    return out


def monthly_tone(concepts, vectors: dict[str, np.ndarray]) -> dict[str, dict[str, np.ndarray]]:
    acc: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for c in concepts:
        if c.concept_id in vectors:
            acc[c.house_id][c.first_seen.strftime("%Y-%m")].append(vectors[c.concept_id])
    return {h: {m: np.mean(v, axis=0) for m, v in ms.items()} for h, ms in acc.items()}


def _cos(a, b) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na and nb else 0.0


def drift_towards(tone: dict, target: str, months: list[str], before: int = BEFORE_MONTHS, exclude=()) -> dict | None:
    """Average change in the other houses' similarity to the target's look in these months."""
    own = [tone.get(target, {}).get(m) for m in months]
    own = [v for v in own if v is not None]
    if not own:
        return None
    look = np.mean(own, axis=0)
    prior_months = [_month(months[0], -k) for k in range(before, 0, -1)]
    changes = {}
    for h, by_m in tone.items():
        if h == target or h in exclude:
            continue
        during = [_cos(by_m[m], look) for m in months if m in by_m]
        prior = [_cos(by_m[m], look) for m in prior_months if m in by_m]
        if during and prior:
            changes[h] = float(np.mean(during) - np.mean(prior))
    if len(changes) < 3:
        return None
    return {"mean_change": float(np.mean(list(changes.values()))), "houses": len(changes)}


def leader_drift(tone: dict, by_month: dict[str, str], before: int = BEFORE_MONTHS) -> list[dict]:
    out = []
    for leader, months in spells(by_month):
        entry = {"leader": leader, "from": months[0], "to": months[-1]}
        obs = drift_towards(tone, leader, months, before)
        if obs is None:
            out.append({**entry, "status": "insufficient"})
            continue
        pseudo = [r["mean_change"] for k in tone if k != leader
                  for r in [drift_towards(tone, k, months, before, exclude=(leader,))] if r is not None]
        out.append({**entry, "status": "ok", "mean_change": round(obs["mean_change"], 4), "houses": obs["houses"],
                    "rank": 1 + sum(1 for v in pseudo if v >= obs["mean_change"]), "of": 1 + len(pseudo),
                    "best_possible_p": round(1 / (1 + len(pseudo)), 4)})
    return out


# ---------- run ----------

def shifts_from_summary(summary: dict) -> dict[str, float]:
    treated = (summary.get("primary") or {}).get("event_study", {}).get("treated", {})
    return {h: float(v["z"]) for h, v in treated.items() if "z" in v}


def main(argv: list[str] | None = None) -> int:
    from . import analysis, attention
    reg = registry.load()
    reasons = analysis.gate(reg)
    if reasons:
        print("success analysis waits until the design is frozen: " + "; ".join(reasons))
        return 0
    path = config.RESULTS_DIR / "summary.json"
    if not path.exists():
        print("no analysis summary yet")
        return 0
    summary = json.loads(path.read_text(encoding="utf-8"))
    changes = attention_changes({h.id: attention.load_series(h.id) for h in reg.houses}, reg)
    assoc = association(shifts_from_summary(summary), {h: c["relative"] for h, c in changes.items() if "relative" in c})
    concepts, _ = analysis.load_concepts(summary["instrument"], summary["embedder"], reg)
    cs = analysis.eligible(concepts)
    tone = monthly_tone(cs, analysis.residuals(cs, {h.id for h in reg.group("control")}))
    lead = leaders()
    out = {"status": "DESCRIPTIVE: association and drift, not effects.", "generated_at": store.utc_now(),
           "basis": "Amendment 2" if analysis.amendment_frozen() else "exploratory: Amendment 2 is not frozen",
           "attention": {"source": f"Wikipedia page views, {attention.PROJECT}, human users",
                         "window_days": WINDOW_DAYS, "by_house": changes},
           "association": assoc,
           "leaders": {"source": "Lyst Index #1 by quarter (reference/lyst_leaders.csv)",
                       "methods": sorted({r["method"] for r in lead}), "drift": leader_drift(tone, leader_by_month(lead))}}
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.RESULTS_DIR / OUT_NAME).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({"association": assoc, "drift": out["leaders"]["drift"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
