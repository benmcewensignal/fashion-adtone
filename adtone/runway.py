"""Runway to attention: does a show's attention last, and does the campaign that follows carry it?

    python -m adtone.runway        # after the attention run; writes data/results/runway.json

Two halves. The attention half needs no advertising: Wikipedia page views reach back to July 2015 and
show dates are public, so whether a show that drew more attention than the house usually gets lifts the
months after it can be read across many shows. The advertising half needs Meta's archive, which holds a
year: after each show, how much reach the house pushed, how closely its campaign resembled its own
show-period advertising, and whether either made the attention last.

The comparison that matters is with ordinary attention. A house's page views jump for many reasons, and
any jump is followed by some lasting change. So the same relation is measured at placebo dates, away from
any show, and a show's attention counts as sticky only if it lasts better than a jump of the same size
on an ordinary day.

Section 13 of Amendment 2. Nothing is computed on real data until that amendment is frozen. Saint
Laurent is left out of the advertising half until its forward prediction has been judged.
"""
from __future__ import annotations

import csv
import json
import sys
import warnings
from datetime import date, timedelta

import numpy as np

from . import config, registry, store

SHOWS_FILE = config.ROOT / "reference" / "shows.csv"
BASELINE = (-60, -10)      # days around the show, inclusive
PEAK = (-1, 3)
LASTING = (30, 120)
SHOW_ADS = (-7, 21)        # the house's own show-period advertising: the stand-in for the runway
CAMPAIGN_ADS = (45, 150)   # the campaign that follows
REACH_AFTER = (0, 90)
COVERAGE = 0.8             # share of days a window needs
MIN_PRIOR_SHOWS = 2        # before a show's surprise can be measured
PLACEBO_GAP = 30           # placebo dates stay this far from any show of the house
PLACEBO_REPS = 1000
MIN_BRIDGE_SHOWS = 30      # shows with advertising data before the bridge is reported as more than description
EXCLUDE_ADS = ("saint_laurent",)


def load_shows(path=SHOWS_FILE, verified_only: bool = True) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if verified_only and r.get("verified", "").strip().lower() != "true":
                continue
            rows.append({**r, "date": date.fromisoformat(r["date"])})
    return sorted(rows, key=lambda r: (r["house"], r["date"]))


def logged(views: dict[date, int]) -> dict[date, float]:
    return {d: float(np.log1p(v)) for d, v in views.items()}


class Panel:
    """Every house's log page views on one daily index, with each window computed once, so an event
    or a placebo date is a lookup rather than a recomputation."""

    def __init__(self, all_series: dict[str, dict[date, float]]):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)   # empty windows are expected at the edges
            self._build(all_series)

    def _build(self, all_series: dict[str, dict[date, float]]) -> None:
        self.houses = sorted(h for h, s in all_series.items() if s)
        days = [d for h in self.houses for d in all_series[h]]
        self.start = min(days)
        n = (max(days) - self.start).days + 1
        self.days = [self.start + timedelta(days=k) for k in range(n)]
        Y = np.full((len(self.houses), n), np.nan)
        for i, h in enumerate(self.houses):
            for d, v in all_series[h].items():
                Y[i, (d - self.start).days] = v
        self.Y = Y
        base = self._mean(BASELINE)
        later = self._mean(LASTING)
        self.spike = self._max(PEAK) - base
        raw = later - base
        self.lasting = np.full_like(raw, np.nan)
        for i in range(len(self.houses)):
            others = np.delete(raw, i, axis=0)
            with np.errstate(all="ignore"):
                med = np.nanmedian(others, axis=0) if len(others) else np.zeros(n)
            self.lasting[i] = raw[i] - np.nan_to_num(med)
        self.row = {h: i for i, h in enumerate(self.houses)}

    def _shifted(self, k: int) -> np.ndarray:
        out = np.full_like(self.Y, np.nan)
        if k >= 0:
            out[:, :self.Y.shape[1] - k] = self.Y[:, k:]
        else:
            out[:, -k:] = self.Y[:, :self.Y.shape[1] + k]
        return out

    def _mean(self, span) -> np.ndarray:
        stack = np.stack([self._shifted(k) for k in range(span[0], span[1] + 1)])
        count = np.sum(~np.isnan(stack), axis=0)
        with np.errstate(all="ignore"):
            m = np.nanmean(stack, axis=0)
        m[count < COVERAGE * (span[1] - span[0] + 1)] = np.nan
        return m

    def _max(self, span) -> np.ndarray:
        stack = np.stack([self._shifted(k) for k in range(span[0], span[1] + 1)])
        with np.errstate(all="ignore"):
            m = np.nanmax(np.where(np.isnan(stack), -np.inf, stack), axis=0)
        m[np.isinf(m)] = np.nan
        return m

    def at(self, house: str, d: date) -> tuple[float, float] | None:
        t = (d - self.start).days
        if house not in self.row or not 0 <= t < len(self.days):
            return None
        sp, la = self.spike[self.row[house], t], self.lasting[self.row[house], t]
        return None if np.isnan(sp) or np.isnan(la) else (float(sp), float(la))


def events(panel: Panel, dates_by_house: dict[str, list[date]]) -> list[dict]:
    """One record per event: spike, surprise (the spike against the house's earlier events) and lasting
    attention net of the median house's change over the same days."""
    out = []
    for h, dates in dates_by_house.items():
        spikes = []
        for d in sorted(dates):
            m = panel.at(h, d)
            if m is None:
                continue
            surprise = m[0] - float(np.mean(spikes)) if len(spikes) >= MIN_PRIOR_SHOWS else None
            spikes.append(m[0])
            out.append({"house": h, "date": d, "spike": m[0], "surprise": surprise, "lasting": m[1]})
    return out


def within_slope(records: list[dict], x: str = "surprise", y: str = "lasting") -> float | None:
    """Slope of y on x after removing each house's own averages: house fixed effects."""
    by: dict[str, list[dict]] = {}
    for r in records:
        if r.get(x) is not None and r.get(y) is not None:
            by.setdefault(r["house"], []).append(r)
    xs, ys = [], []
    for group in by.values():
        if len(group) < 2:
            continue
        mx, my = np.mean([g[x] for g in group]), np.mean([g[y] for g in group])
        xs += [g[x] - mx for g in group]
        ys += [g[y] - my for g in group]
    xs, ys = np.array(xs), np.array(ys)
    if len(xs) < 6 or float(xs @ xs) == 0:
        return None
    return float(xs @ ys / (xs @ xs))


def placebo_dates(panel: Panel, house: str, shows: list[date], n: int, rng: np.random.Generator) -> list[date]:
    i = panel.row.get(house)
    if i is None:
        return []
    ok = ~np.isnan(panel.spike[i]) & ~np.isnan(panel.lasting[i])
    pool = [panel.days[t] for t in np.nonzero(ok)[0]
            if all(abs((panel.days[t] - s).days) > PLACEBO_GAP for s in shows)]
    if len(pool) < n:
        return []
    return sorted(pool[j] for j in rng.choice(len(pool), size=n, replace=False))


def sticky_test(panel: Panel, shows_by_house: dict[str, list[date]], reps: int = PLACEBO_REPS, seed: int = 31) -> dict:
    """Does a show's surprise predict lasting attention better than an equal surprise on an ordinary day?"""
    rng = np.random.default_rng(seed)
    real = events(panel, shows_by_house)
    slope = within_slope(real)
    out = {"n_events": len(real), "n_with_surprise": sum(1 for r in real if r["surprise"] is not None),
           "slope": None if slope is None else round(slope, 4)}
    if slope is None:
        return {**out, "status": "insufficient"}
    null = []
    for _ in range(reps):
        fake = {h: placebo_dates(panel, h, d, len(d), rng) for h, d in shows_by_house.items()}
        sl = within_slope(events(panel, {h: d for h, d in fake.items() if d}))
        if sl is not None:
            null.append(sl)
    if len(null) < 50:
        return {**out, "status": "insufficient", "reason": "too few placebo replicates"}
    null = np.array(null)
    return {**out, "status": "ok", "placebo_mean": round(float(null.mean()), 4),
            "placebo_q95": round(float(np.quantile(null, 0.95)), 4),
            "p": round(float((1 + np.sum(null >= slope)) / (1 + len(null))), 4)}


# ---------- the advertising half ----------

def _cos(a, b) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na and nb else 0.0


def bridge_features(concepts, d: date) -> dict | None:
    """Reach pushed after the show, and how closely the campaign resembles the show-period advertising."""
    def within(span):
        return [c for c in concepts if span[0] <= (c.first_seen - d).days <= span[1]]
    show, campaign, after = within(SHOW_ADS), within(CAMPAIGN_ADS), within(REACH_AFTER)
    if len(show) < 3 or len(campaign) < 3:
        return None
    return {"log_reach_after": float(np.log1p(sum(c.reach or 0 for c in after))),
            "alignment": _cos(np.mean([c.vec for c in show], axis=0), np.mean([c.vec for c in campaign], axis=0)),
            "n_show_ads": len(show), "n_campaign_ads": len(campaign)}


def bridge(records: list[dict], concepts_by_house, boot: int = 2000, seed: int = 37) -> dict:
    """Lasting attention on surprise, reach pushed afterwards and campaign alignment, all standardised.
    Predictive, not causal: houses push more after a show that went well."""
    rows = []
    for r in records:
        if r["house"] in EXCLUDE_ADS or r["surprise"] is None:
            continue
        f = bridge_features(concepts_by_house.get(r["house"], []), r["date"])
        if f:
            rows.append({**r, **f})
    out = {"n_shows": len(rows), "excluded": list(EXCLUDE_ADS)}
    if len(rows) < 8:
        return {**out, "status": "insufficient"}
    names = ["surprise", "log_reach_after", "alignment"]
    X = np.array([[r[k] for k in names] for r in rows])
    X = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-12)
    y = np.array([r["lasting"] for r in rows])
    A = np.column_stack([np.ones(len(rows)), X])
    coef = np.linalg.lstsq(A, y, rcond=None)[0][1:]
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(boot):
        idx = rng.integers(0, len(rows), len(rows))
        draws.append(np.linalg.lstsq(A[idx], y[idx], rcond=None)[0][1:])
    lo, hi = np.quantile(np.array(draws), [0.05, 0.95], axis=0)
    return {**out, "status": "ok" if len(rows) >= MIN_BRIDGE_SHOWS else "descriptive: fewer shows than registered",
            "coefficients": {n: {"estimate": round(float(c), 4), "interval_90": [round(float(a), 4), round(float(b), 4)]}
                             for n, c, a, b in zip(names, coef, lo, hi)}}


def main(argv: list[str] | None = None) -> int:
    from .analysis import amendment_frozen, load_concepts
    from .attention import load_series
    if not amendment_frozen():
        print("runway analysis waits until Amendment 2 is frozen: its hypotheses are registered there")
        return 0
    reg = registry.load()
    houses = [h.id for h in reg.houses]
    all_series = {h: logged(load_series(h)) for h in houses}
    panel = Panel({h: s for h, s in all_series.items() if s})
    shows = load_shows()
    by_house: dict[str, list[date]] = {}
    for r in shows:
        by_house.setdefault(r["house"], []).append(r["date"])
    out = {"generated_at": store.utc_now(), "n_show_dates": len(shows),
           "attention_half": sticky_test(panel, by_house)}
    try:
        concepts, _ = load_concepts(config.instrument(), config.EMBED_TAG, reg)
    except Exception as e:   # no archive yet: the attention half stands alone
        concepts = []
        out["advertising_half"] = {"status": f"no advertising data: {e}"}
    if concepts:
        cb: dict[str, list] = {}
        for c in concepts:
            cb.setdefault(c.house_id, []).append(c)
        out["advertising_half"] = bridge(events(panel, by_house), cb)
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.RESULTS_DIR / "runway.json").write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(json.dumps({k: v.get("status") if isinstance(v, dict) else v for k, v in out.items()}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
