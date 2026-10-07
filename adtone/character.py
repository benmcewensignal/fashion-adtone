"""Character readings from what each house chose to show, starting with its homepage month by month.

    python -m adtone.character [--period half|year] [--all-types]   # writes data/results/character.json

Exploratory and descriptive: nothing here is in the pre-registration, and nothing here is a call.
It runs on the homepage history (adtone.homepages) because that history goes back to 2014 and needs
no one's permission; the same reading applies to ads, films and TikTok once they are collected.

Character. For each house and period (half-years by default), every image the reader has answered
gives one answer to each of the eighteen questions. The house's character in the period is the
share of its images giving each answer (the share showing one person, the share shot in a studio,
the share read as austere), the mean of the street-to-couture scale, and the centroid of the
images' fingerprints. An image shown in several months of one period counts once in it. By default
only image-led pictures count (brand images and product on a model): a homepage also carries
packshots and type, which say more about the shop than about the house's look.

Shift. Between a house's consecutive periods, how far its character moved: for each question, the
total variation distance between the two distributions of answers (0 the same, 1 nothing in
common), averaged over the questions, with the mood list and the scale scored the same way; and,
separately, one minus the cosine between the two fingerprint centroids. Each distance comes with a
permutation p: the same distance with the images' periods shuffled 999 times. Small samples give
large distances by chance; the p says whether this one is larger than chance.

Distinctiveness. How far a house's character sits from the average of the other houses in the same
period, by the same distance.

Contribution to success. Where a house's revenue is reported on its own (adtone.revenue), each
shift is set beside the house's organic growth over the following half-year: one Spearman
correlation over every house and half-year, with growth demeaned within house (each house against
its own usual growth), and a permutation p that shuffles growth within each house. Houses change
their image when they are struggling as well as when they are thriving, so a correlation either
way is a pattern to examine, not an effect.
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from collections import defaultdict
from datetime import date

import numpy as np

from . import config, registry, store

QUESTIONS = ("light", "colour_temperature", "saturation", "setting", "people", "gaze", "expression", "pose",
             "framing", "primary_subject", "styling_register", "production", "text_in_image")
IMAGE_LED = ("brand_image", "product_on_model")
MIN_IMAGES = 6           # per period, before a shift or a distance is reported
N_PERM = 999
SCALE = ("street_couture_axis", 1, 5)


def period_of(month: str, grain: str = "half") -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y}" if grain == "year" else f"{y}H{1 if m <= 6 else 2}"


def period_span(p: str) -> tuple[date, date]:
    if "H" in p:
        y, h = int(p[:4]), int(p[5])
        return (date(y, 1, 1), date(y, 6, 30)) if h == 1 else (date(y, 7, 1), date(y, 12, 31))
    y = int(p)
    return date(y, 1, 1), date(y, 12, 31)


def next_period(p: str) -> str:
    if "H" in p:
        y, h = int(p[:4]), int(p[5])
        return f"{y}H2" if h == 1 else f"{y + 1}H1"
    return str(int(p) + 1)


class Encoder:
    """Answers as one row of numbers per image: a one-hot block per question, one column per mood,
    and the scale mapped to 0..1. Distances are computed block by block on period means."""

    def __init__(self, spec: dict):
        self.enums = {q: list(spec["enums"][q]) for q in QUESTIONS}
        self.moods = list(spec["lists"]["mood"]["options"])
        self.blocks: list[tuple[str, int, int]] = []
        i = 0
        for q in QUESTIONS:
            self.blocks.append((q, i, i + len(self.enums[q])))
            i += len(self.enums[q])
        self.mood_at = i
        i += len(self.moods)
        self.scale_at = i
        self.width = i + 1

    def row(self, out: dict) -> np.ndarray:
        v = np.zeros(self.width)
        for q, a, _ in self.blocks:
            ans = out.get(q)
            if ans in self.enums[q]:
                v[a + self.enums[q].index(ans)] = 1.0
        for m in out.get("mood") or []:
            if m in self.moods:
                v[self.mood_at + self.moods.index(m)] = 1.0
        s = out.get(SCALE[0])
        v[self.scale_at] = ((float(s) - SCALE[1]) / (SCALE[2] - SCALE[1])) if isinstance(s, (int, float)) else np.nan
        return v

    def distance(self, a: np.ndarray, b: np.ndarray) -> float:
        """Mean over questions of total variation distance; the moods' mean absolute difference in shares
        and the scale's absolute difference count as two more questions."""
        parts = [0.5 * float(np.abs(a[s:e] - b[s:e]).sum()) for _, s, e in self.blocks]
        parts.append(float(np.abs(a[self.mood_at:self.scale_at] - b[self.mood_at:self.scale_at]).mean()))
        if not (np.isnan(a[self.scale_at]) or np.isnan(b[self.scale_at])):
            parts.append(abs(float(a[self.scale_at] - b[self.scale_at])))
        return float(np.mean(parts))

    def profile(self, mean: np.ndarray, n: int) -> dict:
        out = {"n": n}
        for q, s, e in self.blocks:
            out[q] = {ans: round(float(mean[s + k]), 3) for k, ans in enumerate(self.enums[q]) if mean[s + k] > 0}
        out["mood"] = {m: round(float(mean[self.mood_at + k]), 3) for k, m in enumerate(self.moods) if mean[self.mood_at + k] > 0}
        sc = mean[self.scale_at]
        out["street_couture"] = None if np.isnan(sc) else round(float(SCALE[1] + sc * (SCALE[2] - SCALE[1])), 2)
        return out

    def moved(self, a: np.ndarray, b: np.ndarray, top: int = 4) -> list[dict]:
        """The answers whose shares changed most, for the report."""
        rows = []
        for q, s, e in self.blocks:
            for k, ans in enumerate(self.enums[q]):
                rows.append((float(b[s + k] - a[s + k]), q, ans))
        for k, m in enumerate(self.moods):
            rows.append((float(b[self.mood_at + k] - a[self.mood_at + k]), "mood", m))
        rows.sort(key=lambda r: -abs(r[0]))
        return [{"question": q, "answer": ans, "change": round(d, 3)} for d, q, ans in rows[:top] if abs(d) >= 0.1]


def _nanmean(x: np.ndarray) -> np.ndarray:
    """Column means; the scale column is blank where the reader gave no scale."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(x, axis=0)


def _cos_dist(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(1 - (a @ b) / (na * nb)) if na and nb else float("nan")


def permuted(enc: Encoder, X0: np.ndarray, X1: np.ndarray, V0: np.ndarray | None, V1: np.ndarray | None,
             n_perm: int, rng: np.random.Generator) -> dict:
    """Observed distances between two periods and their permutation p-values."""
    obs_r = enc.distance(_nanmean(X0), _nanmean(X1))
    X = np.vstack([X0, X1])
    n0 = len(X0)
    null_r = np.empty(n_perm)
    has_v = V0 is not None and V1 is not None and len(V0) == n0 and len(V1) == len(X1)
    if has_v:
        V = np.vstack([V0, V1])
        obs_v = _cos_dist(V0.mean(axis=0), V1.mean(axis=0))
        null_v = np.empty(n_perm)
    for i in range(n_perm):
        idx = rng.permutation(len(X))
        a, b = idx[:n0], idx[n0:]
        null_r[i] = enc.distance(_nanmean(X[a]), _nanmean(X[b]))
        if has_v:
            null_v[i] = _cos_dist(V[a].mean(axis=0), V[b].mean(axis=0))
    out = {"rubric": round(obs_r, 4), "rubric_p": round((1 + int((null_r >= obs_r - 1e-12).sum())) / (1 + n_perm), 4),
           "rubric_chance": round(float(np.median(null_r)), 4)}
    if has_v:
        out.update({"fingerprint": round(obs_v, 4),
                    "fingerprint_p": round((1 + int((null_v >= obs_v - 1e-12).sum())) / (1 + n_perm), 4),
                    "fingerprint_chance": round(float(np.median(null_v)), 4)})
    return out


def load_homepage_images(types=IMAGE_LED) -> list[dict]:
    """(house, month, sha, reader answers, fingerprint) for every image the reader answered."""
    from .embed import VectorStore
    from . import homepages
    P = homepages.paths()
    obs = {}
    for p in sorted(P["obs"].glob("*.jsonl")):
        for r in store.read_jsonl(p):
            if r.get("status") == "ok" and r.get("output"):
                obs[r["sha"]] = r["output"]
    vecs: dict[str, np.ndarray] = {}
    for d in sorted(P["vectors"].glob("*")) if P["vectors"].exists() else []:
        vecs.update(VectorStore(d.name, root=P["vectors"]).vecs)
    out = []
    for p in sorted(P["captures"].glob("*.jsonl")):
        for row in store.read_jsonl(p):
            if row.get("status") != "resolved":
                continue
            for im in row.get("images") or []:
                o = obs.get(im["sha"])
                if o is None or (types and o.get("creative_type") not in types):
                    continue
                out.append({"house": row["house_id"], "month": row["month"], "sha": im["sha"], "out": o,
                            "vec": vecs.get(im["sha"])})
    return out


def read(images: list[dict], enc: Encoder, grain: str = "half", n_perm: int = N_PERM, seed: int = 20261007) -> dict:
    rng = np.random.default_rng(seed)
    groups: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    for im in images:
        groups[(im["house"], period_of(im["month"], grain))].setdefault(im["sha"], im)   # once per period
    houses: dict[str, dict] = defaultdict(lambda: {"periods": {}, "shifts": [], "distinct": {}})
    mats: dict[tuple[str, str], tuple[np.ndarray, np.ndarray | None]] = {}
    for (h, p), ims in sorted(groups.items()):
        X = np.vstack([enc.row(im["out"]) for im in ims.values()])
        vs = [im["vec"] for im in ims.values()]
        V = np.vstack(vs) if all(v is not None for v in vs) else None
        mats[(h, p)] = (X, V)
        houses[h]["periods"][p] = enc.profile(_nanmean(X), len(X))
    for h in sorted(houses):
        ps = sorted(houses[h]["periods"])
        for p0, p1 in zip(ps, ps[1:]):
            if next_period(p0) != p1:
                continue   # a gap: the shift would span more than one step
            (X0, V0), (X1, V1) = mats[(h, p0)], mats[(h, p1)]
            if len(X0) < MIN_IMAGES or len(X1) < MIN_IMAGES:
                continue
            s = {"from": p0, "to": p1, "n0": len(X0), "n1": len(X1)}
            s.update(permuted(enc, X0, X1, V0, V1, n_perm, rng))
            s["moved"] = enc.moved(_nanmean(X0), _nanmean(X1))
            houses[h]["shifts"].append(s)
    by_period: dict[str, dict[str, np.ndarray]] = defaultdict(dict)
    for (h, p), (X, _) in mats.items():
        if len(X) >= MIN_IMAGES:
            by_period[p][h] = _nanmean(X)
    for p, means in by_period.items():
        if len(means) < 3:
            continue
        for h, m in means.items():
            others = [v for k, v in means.items() if k != h]
            houses[h]["distinct"][p] = round(enc.distance(m, _nanmean(np.vstack(others))), 4)
    return {h: houses[h] for h in sorted(houses)}


def half_growth(house: str, figs=None) -> dict[str, float]:
    """Organic growth by calendar half-year: a reported half where there is one, otherwise the
    revenue-weighted mean of its two quarters. A fiscal period counts in the calendar half that holds
    most of its days."""
    from . import revenue
    figs = revenue.figures() if figs is None else figs
    mine = [f for f in figs if f.house_id == house and f.comparable_change_pct is not None]
    if not mine:
        return {}
    counts: dict[str, int] = defaultdict(int)
    for f in mine:
        counts[f.measure] += 1
    main = max(counts, key=lambda m: (counts[m], m))
    mine = [f for f in mine if f.measure == main]

    def half(f) -> str:
        mid = f.start.toordinal() + (f.end.toordinal() - f.start.toordinal()) // 2
        d = date.fromordinal(mid)
        return f"{d.year}H{1 if d.month <= 6 else 2}"
    out: dict[str, float] = {}
    for f in mine:
        if f.period_type == "half":
            out[half(f)] = f.comparable_change_pct
    quarters: dict[str, list] = defaultdict(list)
    for f in mine:
        if f.period_type == "quarter" and 80 <= (f.end - f.start).days <= 100:
            quarters[half(f)].append(f)
    for h, qs in quarters.items():
        if h not in out and len(qs) == 2 and all(q.revenue_m for q in qs):
            w = sum(q.revenue_m for q in qs)
            out[h] = sum(q.comparable_change_pct * q.revenue_m for q in qs) / w
    return out


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    rx, ry = np.argsort(np.argsort(x)), np.argsort(np.argsort(y))
    return float(np.corrcoef(rx, ry)[0, 1]) if len(x) > 2 and np.std(rx) and np.std(ry) else float("nan")


def success(houses: dict, growth: dict[str, dict[str, float]], n_perm: int = N_PERM, seed: int = 7) -> dict:
    """Shift in a half-year against organic growth over the next, each house against its own usual growth."""
    pairs = []
    for h, rec in houses.items():
        g = growth.get(h) or {}
        if len(g) < 3:
            continue
        mean_g = float(np.mean(list(g.values())))
        for s in rec["shifts"]:
            nxt = next_period(s["to"])
            if nxt in g:
                pairs.append((h, s["to"], s["rubric"], g[nxt] - mean_g))
    out = {"pairs": len(pairs), "houses": sorted({p[0] for p in pairs})}
    if len(pairs) < 8:
        out["note"] = "too few house-half-years with both a shift and the next half's growth"
        return out
    x = np.array([p[2] for p in pairs])
    y = np.array([p[3] for p in pairs])
    r = _spearman(x, y)
    rng = np.random.default_rng(seed)
    idx_by_house: dict[str, list[int]] = defaultdict(list)
    for i, p in enumerate(pairs):
        idx_by_house[p[0]].append(i)
    null = np.empty(n_perm)
    for k in range(n_perm):
        yp = y.copy()
        for idx in idx_by_house.values():
            yp[idx] = y[rng.permutation(idx)]
        null[k] = _spearman(x, yp)
    out.update({"spearman": round(r, 3), "p_two_sided": round((1 + int((np.abs(null) >= abs(r) - 1e-12).sum())) / (1 + n_perm), 4),
                "pairs_detail": [{"house": h, "half": t, "shift": round(s, 4), "next_growth_vs_usual": round(g, 2)}
                                 for h, t, s, g in pairs]})
    return out


def main(argv: list[str] | None = None) -> int:
    from .score import load_rubric
    ap = argparse.ArgumentParser(prog="adtone.character")
    ap.add_argument("--period", choices=["half", "year"], default="half")
    ap.add_argument("--all-types", action="store_true", help="count packshots and type too")
    a = ap.parse_args(argv)
    enc = Encoder(load_rubric().spec)
    images = load_homepage_images(None if a.all_types else IMAGE_LED)
    houses = read(images, enc, a.period)
    reg = registry.load()
    growth = {h.id: half_growth(h.id) for h in reg.houses}
    out = {"generated_at": store.utc_now(), "source": "homepages", "period": a.period,
           "types": "all" if a.all_types else list(IMAGE_LED), "images": len(images),
           "houses": houses, "success": success(houses, growth) if a.period == "half" else None,
           "status": "exploratory: descriptive readings, not in the pre-registration"}
    path = config.RESULTS_DIR / "character.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    n_shift = sum(len(r["shifts"]) for r in houses.values())
    moved = sum(1 for r in houses.values() for s in r["shifts"] if s["rubric_p"] <= 0.05)
    print(f"character: {len(images)} images, {len(houses)} houses, {n_shift} shifts measured, {moved} beyond chance (p<=0.05); "
          f"success pairs: {out['success']['pairs'] if out['success'] else 'n/a'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
