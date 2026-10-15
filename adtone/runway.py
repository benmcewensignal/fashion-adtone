"""Runway to attention: does a show's attention last, and does the campaign that follows carry it?

    python -m adtone.runway        # after the attention run; writes data/results/runway.json

Two halves. The attention half needs no advertising: Wikipedia page views reach back to July 2015 and
show dates are public, so whether a show that drew more attention than the house usually gets lifts the
months after it can be read across many shows. The advertising half needs Meta's archive, which holds a
year, and runs on the campaign's own clock: the day the season's campaign lands, how much attention rose
from then, how much reach the house pushed, and how closely the campaign resembled its own show-period
advertising.

The comparison that matters is with ordinary attention. A house's page views jump for many reasons, and
any jump is followed by some lasting change. So the same relation is measured at placebo dates, away from
any show, and a show's attention counts as sticky only if it lasts better than a jump of the same size
on an ordinary day.

Fashion ambassadors appear at the shows and in the advertising, and the industry's own measures credit
single ambassadors with a quarter to two fifths of a show's media value. Their appointments in the 180
days before a show (the Thread's scene) are held constant in what the clothes add, and in a declared
extra run of the sticky test, added after its registered result had been seen.

The attention half runs on every main show since 2015 in the Thread's calendar (data/results/thread.json),
or on the verified dates in reference/shows.csv where the calendar is not on file. Descriptive. Saint
Laurent is left out of the advertising half until its forward prediction has been judged.
"""
from __future__ import annotations

import csv
import json
import sys
import warnings
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from . import config, registry, store
from .thread import AMBASSADORS_FROM, STAGES, campaign_window

SHOWS_FILE = config.ROOT / "reference" / "shows.csv"
BASELINE = (-60, -10)      # days around the show, inclusive
PEAK = (-1, 3)
LASTING = (30, 120)
SHOW_ADS = (-7, 21)        # the house's own show-period advertising: the stand-in for the runway
# the campaign that follows: from six weeks after the show to a week before the house's next show on file
# (thread.campaign_window, shared with the collections)
# The bridge runs on the campaign's own clock (Amendment 2, section 13, as amended on 11 October 2026): the season's
# campaign mostly arrives as the lasting window closes or after it, so its lift is read from the day it lands.
LANDING_CONCEPTS = 3       # a campaign has landed when its third concept for the season's collection is first seen
LIFT_DAYS = 60             # its lift: from the landing to 60 days after,
LIFT_STOP = 10             # cut 10 days before the house's next main show,
LIFT_MIN_DAYS = 30         # and at least 30 days long,
LIFT_BASE = (-60, -10)     # against the mean from 60 to 10 days before the landing
SCENE = STAGES["scene"]    # fashion ambassador appointments in the 180 days before a date
COVERAGE = 0.8             # share of days a window needs
MIN_PRIOR_SHOWS = 2        # before a show's surprise can be measured
PLACEBO_GAP = 30           # placebo dates stay this far from any show of the house
PLACEBO_REPS = 1000
MIN_BRIDGE_SHOWS = 30      # shows with advertising data before the bridge is reported as more than description
MIN_DECOMPOSE = 60         # shows with every term before what the clothes add is read
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

    def lift(self, house: str, start: date, end: date, base: tuple[int, int] = LIFT_BASE) -> float | None:
        """The mean log page views from `start` to `end` over the mean from base[0] to base[1] days before
        `start`, less the median house's change over the same days. Each window needs COVERAGE of its days."""
        i = self.row.get(house)
        t0, t1 = (start - self.start).days, (end - self.start).days
        b0, b1 = t0 + base[0], t0 + base[1]
        if i is None or b0 < 0 or t1 >= len(self.days) or t1 < t0:
            return None

        def mean(a, b):
            seg = self.Y[:, a:b + 1]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)   # a house with no views in the window
                m = np.nanmean(seg, axis=1)
            m[np.sum(~np.isnan(seg), axis=1) < COVERAGE * (b - a + 1)] = np.nan
            return m
        raw = mean(t0, t1) - mean(b0, b1)
        if np.isnan(raw[i]):
            return None
        others = np.delete(raw, i)
        others = others[~np.isnan(others)]
        return float(raw[i] - (np.median(others) if len(others) else 0.0))



MOMENTUM_EARLY = (-150, -90)   # page views well before the show, against the baseline: the trend coming in
PRESS_AFTER = (0, 3)
TONE_MIN_DAYS = (2, 10)        # days with coverage needed after the show, and in the baseline


class PressPanel:
    """Daily press tone and article counts on the attention panel's index. Tone is missing on days with
    no matching articles, so tone windows need a minimum number of covered days rather than a share."""

    def __init__(self, panel: Panel, press: dict[str, dict[date, dict]]):
        n = len(panel.days)
        self.panel = panel
        T = np.full((len(panel.houses), n), np.nan)
        A = np.full((len(panel.houses), n), np.nan)
        for i, h in enumerate(panel.houses):
            for d, r in press.get(h, {}).items():
                t = (d - panel.start).days
                if 0 <= t < n:
                    A[i, t] = np.log1p(r.get("articles") or 0)
                    if r.get("tone") is not None:
                        T[i, t] = float(r["tone"])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            self.reception = self._mean(T, PRESS_AFTER, TONE_MIN_DAYS[0]) - self._mean(T, BASELINE, TONE_MIN_DAYS[1])
            base_a = self._mean(A, BASELINE, int(COVERAGE * (BASELINE[1] - BASELINE[0] + 1)))
            peak_a = np.nanmax(np.where(np.isnan(st := self._stack(A, PEAK)), -np.inf, st), axis=0)
            peak_a[np.isinf(peak_a)] = np.nan
            self.press_spike = peak_a - base_a
            Y = panel.Y
            self.momentum = (self._mean(Y, BASELINE, int(COVERAGE * 51))
                             - self._mean(Y, MOMENTUM_EARLY, int(COVERAGE * 61)))

    @staticmethod
    def _stack(M, span):
        out = []
        for k in range(span[0], span[1] + 1):
            sh = np.full_like(M, np.nan)
            if k >= 0:
                sh[:, :M.shape[1] - k] = M[:, k:]
            else:
                sh[:, -k:] = M[:, :M.shape[1] + k]
            out.append(sh)
        return np.stack(out)

    def _mean(self, M, span, min_days):
        st = self._stack(M, span)
        m = np.nanmean(st, axis=0)
        m[np.sum(~np.isnan(st), axis=0) < min_days] = np.nan
        return m

    def at(self, house: str, d: date) -> dict:
        i, t = self.panel.row.get(house), (d - self.panel.start).days
        if i is None or not 0 <= t < len(self.panel.days):
            return {}
        vals = {"reception": self.reception[i, t], "press_spike": self.press_spike[i, t], "momentum": self.momentum[i, t]}
        return {k: (None if np.isnan(v) else float(v)) for k, v in vals.items()}


def load_appointments(path: Path | None = None) -> dict[str, list[date]]:
    """Fashion ambassador appointments by house, read as the Thread reads them (reference/ambassadors.csv)."""
    from . import readings
    path = Path(path or readings.AMBASSADORS_FILE)
    out: dict[str, list[date]] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            d = r.get("announced_date") or ""
            if r.get("category") in readings.FASHION and len(d) >= 7:
                out.setdefault(r["house_id"], []).append(date.fromisoformat(d if len(d) == 10 else f"{d[:7]}-01"))
    return out


class Scene:
    """Fashion ambassador appointments in the 180 days before a date: the Thread's scene stage. None where the
    appointments file does not cover all 180 days (it starts in 2019)."""

    def __init__(self, appointments: dict[str, list[date]]):
        self.appointments = appointments

    def at(self, house: str, d: date) -> float | None:
        a0, a1 = d + timedelta(days=SCENE[0]), d + timedelta(days=SCENE[1])
        if a0 < AMBASSADORS_FROM:
            return None
        return float(sum(1 for x in self.appointments.get(house, []) if a0 <= x <= a1))


def event_table(panel: Panel, dates_by_house: dict[str, list[date]], press: PressPanel | None = None,
                concepts_by_house: dict | None = None, scene: Scene | None = None,
                is_collection=lambda c: True) -> list[dict]:
    """The thread: one row per show, every source joined on house and date. Attention gives the spike,
    the surprise and the lasting lift; the press gives reception and its own spike; page views before
    the show give momentum; the appointments give the scene; the archive gives the campaign's landing, the
    lift in attention from it, the reach pushed from it and the campaign's alignment."""
    rows = events(panel, dates_by_house)
    nxt = next_shows(dates_by_house)
    for r in rows:
        if press is not None:
            r.update(press.at(r["house"], r["date"]))
        if scene is not None:
            r["scene"] = scene.at(r["house"], r["date"])
        if concepts_by_house is not None and r["house"] not in EXCLUDE_ADS:
            n = nxt.get((r["house"], r["date"]))
            w = campaign_window(r["date"], n, _half(r["date"]))
            f = bridge_features(concepts_by_house.get(r["house"], []), r["date"], w, is_collection)
            if f:
                r.update({"landing": f["landing"], "log_reach_campaign": f["log_reach_campaign"],
                          "alignment": f["alignment"], "campaign_lift": campaign_lift(panel, r["house"], f["landing"], n)})
    return rows


def what_the_clothes_add(rows: list[dict], controls=("spike", "momentum", "scene"), term: str = "reception",
                         target: str = "lasting", perms: int = PLACEBO_REPS, boot: int = 1000, seed: int = 41) -> dict:
    """How much lasting attention reception predicts once spectacle, momentum and the scene are accounted
    for, within houses. The increment in R-squared is set against the same increment with reception shuffled
    among each house's own shows, so a house's general press climate cannot pass for its clothes. The scene,
    ambassador appointments in the 180 days before, limits it to shows from mid-2019."""
    names = [term, *controls]
    use = [r for r in rows if all(r.get(k) is not None for k in (*names, target))]
    by: dict[str, list[int]] = {}
    for i, r in enumerate(use):
        by.setdefault(r["house"], []).append(i)
    keep = [i for g in by.values() if len(g) >= 3 for i in g]
    out = {"n_shows": len(keep), "n_houses": sum(1 for g in by.values() if len(g) >= 3), "term": term,
           "controls": list(controls), "target": target}
    if len(keep) < MIN_DECOMPOSE:
        return {**out, "status": f"insufficient: fewer than {MIN_DECOMPOSE} shows with every term"}
    use = [use[i] for i in keep]
    groups = [r["house"] for r in use]

    def demean(vals):
        v = np.array(vals, float)
        for h in set(groups):
            idx = [i for i, g in enumerate(groups) if g == h]
            v[idx] -= v[idx].mean()
        return v

    X = np.column_stack([demean([r[k] for r in use]) for k in names])
    y = demean([r[target] for r in use])
    X = X / (X.std(axis=0) + 1e-12)

    def r2(M):
        beta = np.linalg.lstsq(M, y, rcond=None)[0]
        resid = y - M @ beta
        return 1 - float(resid @ resid) / float(y @ y), beta

    full, beta = r2(X)
    base, _ = r2(X[:, 1:])
    inc = full - base
    rng = np.random.default_rng(seed)
    null = []
    idx_by = {h: [i for i, g in enumerate(groups) if g == h] for h in sorted(set(groups))}   # a set's order changes between runs
    for _ in range(perms):
        Xp = X.copy()
        for idx in idx_by.values():
            Xp[idx, 0] = Xp[rng.permutation(idx), 0]
        null.append(r2(Xp)[0] - base)
    draws = []
    for _ in range(boot):
        b = rng.integers(0, len(y), len(y))
        draws.append(np.linalg.lstsq(X[b], y[b], rcond=None)[0][0])
    lo, hi = np.quantile(draws, [0.05, 0.95])
    p = (1 + sum(n >= inc for n in null)) / (1 + len(null))
    return {**out, "status": "ok", "r2_without_term": round(base, 4), "r2_with_term": round(full, 4),
            "increment": round(inc, 4), "p": round(float(p), 4),
            "coefficient": round(float(beta[0]), 4), "interval_90": [round(float(lo), 4), round(float(hi), 4)],
            "supported": bool(p <= 0.05 and beta[0] > 0)}

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


def within_slope(records: list[dict], x: str = "surprise", y: str = "lasting", control: str | None = None) -> float | None:
    """Slope of y on x after removing each house's own averages: house fixed effects. With `control`, that
    measure is held constant too: its within-house part is taken out of x first, so the slope is the
    coefficient on x with the control beside it."""
    by: dict[str, list[dict]] = {}
    for r in records:
        if r.get(x) is not None and r.get(y) is not None and (control is None or r.get(control) is not None):
            by.setdefault(r["house"], []).append(r)
    xs, ys, cs = [], [], []
    for group in by.values():
        if len(group) < 2:
            continue
        mx, my = np.mean([g[x] for g in group]), np.mean([g[y] for g in group])
        xs += [g[x] - mx for g in group]
        ys += [g[y] - my for g in group]
        if control is not None:
            mc = np.mean([g[control] for g in group])
            cs += [g[control] - mc for g in group]
    xs, ys = np.array(xs), np.array(ys)
    if control is not None and cs:
        c = np.array(cs)
        if float(c @ c) > 0:
            xs = xs - c * float(c @ xs) / float(c @ c)
    if len(xs) < 6 or float(xs @ xs) == 0:
        return None
    return float(xs @ ys / (xs @ xs))


def placebo_pool(panel: Panel, house: str, shows: list[date]) -> list[date]:
    """Every day with a spike and a lasting value that is more than PLACEBO_GAP days from any show of the house."""
    i = panel.row.get(house)
    if i is None:
        return []
    ok = ~np.isnan(panel.spike[i]) & ~np.isnan(panel.lasting[i])
    for s in shows:
        t = (s - panel.start).days
        ok[max(0, t - PLACEBO_GAP):max(0, t + PLACEBO_GAP + 1)] = False
    return [panel.days[t] for t in np.nonzero(ok)[0]]


def placebo_dates(panel: Panel, house: str, shows: list[date], n: int, rng: np.random.Generator,
                  pool: list[date] | None = None) -> list[date]:
    pool = placebo_pool(panel, house, shows) if pool is None else pool
    if len(pool) < n:
        return []
    return sorted(pool[j] for j in rng.choice(len(pool), size=n, replace=False))


def sticky_test(panel: Panel, shows_by_house: dict[str, list[date]], reps: int = PLACEBO_REPS, seed: int = 31,
                scene: Scene | None = None) -> dict:
    """Does a show's surprise predict lasting attention better than an equal surprise on an ordinary day?

    With `scene`, the same test with ambassador appointments in the 180 days before each date, show or
    placebo, held constant, on the dates the appointments cover. The placebo dates are drawn exactly as in
    the registered run. A declared extra run, added after the registered result was seen: reported beside
    it, never in its place."""
    rng = np.random.default_rng(seed)
    control = None if scene is None else "scene"

    def with_scene(rows: list[dict]) -> list[dict]:
        if scene is not None:
            for r in rows:
                r["scene"] = scene.at(r["house"], r["date"])
        return rows

    real = with_scene(events(panel, shows_by_house))
    slope = within_slope(real, control=control)
    counted = [r for r in real if control is None or r.get("scene") is not None]
    out = {"n_events": len(counted), "n_with_surprise": sum(1 for r in counted if r["surprise"] is not None),
           "slope": None if slope is None else round(slope, 4)}
    if scene is not None:
        out["held_constant"] = "fashion ambassador appointments in the 180 days before"
    if slope is None:
        return {**out, "status": "insufficient"}
    null = []
    pools = {h: placebo_pool(panel, h, d) for h, d in shows_by_house.items()}   # the same every replicate
    for _ in range(reps):
        fake = {h: placebo_dates(panel, h, d, len(d), rng, pools[h]) for h, d in shows_by_house.items()}
        sl = within_slope(with_scene(events(panel, {h: d for h, d in fake.items() if d})), control=control)
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


def _half(d: date) -> str | None:
    """The season a main show presents: autumn-winter in February and March, spring-summer in September and October."""
    return "AW" if d.month in (1, 2, 3, 4) else "SS" if d.month in (8, 9, 10, 11) else None


def next_shows(dates_by_house: dict[str, list[date]]) -> dict[tuple[str, date], date]:
    out = {}
    for h, ds in dates_by_house.items():
        ds = sorted(set(ds))
        for a, b in zip(ds, ds[1:]):
            out[(h, a)] = b
    return out


NAMED_LINES = ("beauty", "jewellery_watches", "accessories", "capsule", "holiday")


def concept_line(lines: list[str | None]) -> str:
    """What a concept sells, from what its ads' own text said at collection time (adtone/thread.py, ad_line;
    Amendment 3, section 2): a named line where any of its ads names one, the commonest; else the season's
    collection where any of them names it; else other."""
    named = [x for x in lines if x in NAMED_LINES]
    if named:
        return Counter(named).most_common(1)[0][0]
    return "collection" if "collection" in lines else "other"


def collection_sorter(ads: dict[str, dict], worn_full: set[str] | frozenset = frozenset()):
    """Whether a concept advertises the season's collection: by its ads' own text, or, where the text names
    nothing, by a picture of a full outfit worn (the clothes rubric's subject). Leather goods, beauty, jewellery
    and watches, capsules and the holidays never count."""
    def is_collection(c) -> bool:
        line = concept_line([(ads.get(a) or {}).get("line") for a in c.ad_ids])
        return line == "collection" or (line == "other" and any(s in worn_full for s in c.shas))
    return is_collection


def landing(concepts, d: date, window: tuple[int, int], is_collection=lambda c: True) -> date | None:
    """The day a show's campaign lands: when the third concept for the season's collection is first seen in
    the show's campaign window."""
    firsts = sorted(c.first_seen for c in concepts
                    if window[0] <= (c.first_seen - d).days <= window[1] and is_collection(c))
    return firsts[LANDING_CONCEPTS - 1] if len(firsts) >= LANDING_CONCEPTS else None


def campaign_lift(panel: Panel, house: str, landed: date, next_show: date | None = None) -> float | None:
    """The rise in attention from the day the campaign lands: from the landing to 60 days after it, cut 10 days
    before the house's next main show and at least 30 days long, over the mean from 60 to 10 days before the
    landing, net of the median house."""
    end = landed + timedelta(days=LIFT_DAYS)
    if next_show is not None:
        end = min(end, next_show - timedelta(days=LIFT_STOP))
    if (end - landed).days + 1 < LIFT_MIN_DAYS:
        return None
    return panel.lift(house, landed, end)


def bridge_features(concepts, d: date, window: tuple[int, int] | None = None, is_collection=lambda c: True) -> dict | None:
    """The day the campaign lands, the reach the house pushed from then, and how closely the campaign for the
    season's collection resembles the show-period advertising."""
    window = window or campaign_window(d, None, _half(d))
    show = [c for c in concepts if SHOW_ADS[0] <= (c.first_seen - d).days <= SHOW_ADS[1]]
    campaign = [c for c in concepts if window[0] <= (c.first_seen - d).days <= window[1] and is_collection(c)]
    landed = landing(concepts, d, window, is_collection)
    if len(show) < 3 or len(campaign) < 3 or landed is None:
        return None
    pushed = [c for c in concepts if 0 <= (c.first_seen - landed).days <= LIFT_DAYS]
    return {"landing": landed, "log_reach_campaign": float(np.log1p(sum(c.reach or 0 for c in pushed))),
            "alignment": _cos(np.mean([c.vec for c in show], axis=0), np.mean([c.vec for c in campaign], axis=0)),
            "n_show_ads": len(show), "n_campaign_ads": len(campaign)}


def bridge(records: list[dict], concepts_by_house, panel: Panel, is_collection=lambda c: True,
           boot: int = 2000, seed: int = 37) -> dict:
    """The lift in attention from the day the campaign lands, on the show's surprise, the reach pushed from
    the landing and the campaign's alignment with the show-period advertising, all standardised. Predictive,
    not causal: houses push more after a show that went well."""
    rows = []
    nxt = next_shows({h: [r["date"] for r in records if r["house"] == h] for h in {r["house"] for r in records}})
    for r in records:
        if r["house"] in EXCLUDE_ADS or r["surprise"] is None:
            continue
        n = nxt.get((r["house"], r["date"]))
        f = bridge_features(concepts_by_house.get(r["house"], []), r["date"],
                            campaign_window(r["date"], n, _half(r["date"])), is_collection)
        lift = campaign_lift(panel, r["house"], f["landing"], n) if f else None
        if lift is not None:
            rows.append({**r, **f, "campaign_lift": lift})
    out = {"n_shows": len(rows), "excluded": list(EXCLUDE_ADS), "target": "campaign_lift"}
    if len(rows) < 8:
        return {**out, "status": "insufficient"}
    names = ["surprise", "log_reach_campaign", "alignment"]
    X = np.array([[r[k] for k in names] for r in rows])
    X = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-12)
    y = np.array([r["campaign_lift"] for r in rows])
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


TABLE_FIELDS = ["house", "date", "spike", "surprise", "lasting", "reception", "press_spike", "momentum", "scene",
                "landing", "campaign_lift", "log_reach_campaign", "alignment"]


def write_table(rows: list[dict], path) -> None:
    """The thread as a file anyone can open: one row per show, every joined measure, blanks where a
    source has no data for that show. Derived numbers only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TABLE_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["house"], r["date"])):
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})


def calendar_shows() -> tuple[dict[str, list[date]], str]:
    """Each house's main shows: the Thread's calendar where it is on file (every main show since 2015 with
    a jump in attention measured), else the verified dates."""
    path = config.RESULTS_DIR / "thread.json"
    by_house: dict[str, list[date]] = {}
    if path.exists():
        for r in json.loads(path.read_text()).get("rows", []):
            if r.get("main") and r.get("heat") is not None:
                by_house.setdefault(r["house"], []).append(date.fromisoformat(r["date"]))
        if by_house:
            return by_house, "the Thread's calendar, main shows with a measured jump"
    for r in load_shows():
        by_house.setdefault(r["house"], []).append(r["date"])
    return by_house, "verified dates in reference/shows.csv"


def registered_houses(reg: registry.Registry) -> list[str]:
    """The houses Amendment 2, sections 13 and 13b, were set on and their results seen with: the wider field of
    15 October 2026 came after, so its shows and its attention stay out of these tests and out of their median."""
    return [h.id for h in reg.houses if h.tier != "wider"]


def main(argv: list[str] | None = None) -> int:
    from .analysis import load_concepts
    from .wikiviews import load_series
    reg = registry.load()
    houses = registered_houses(reg)
    # English views with a renamed article's earlier titles joined: six houses' articles moved, and the
    # current title alone would start their series years after July 2015
    all_series = {h: logged(load_series(h, "en")) for h in houses}
    panel = Panel({h: s for h, s in all_series.items() if s})
    by_house, source = calendar_shows()
    by_house = {h: ds for h, ds in by_house.items() if h in houses}
    from .press import load_series as press_series
    press = PressPanel(panel, {h: press_series(h) for h in panel.houses})
    scene = Scene(load_appointments())
    out = {"generated_at": store.utc_now(), "shows_from": source,
           "n_show_dates": sum(len(v) for v in by_house.values()),
           "attention_half": sticky_test(panel, by_house),
           "attention_half_scene": {**sticky_test(panel, by_house, scene=scene),
                                    "declared": "an extra run added on 11 October 2026, after the registered result "
                                                "above had been seen (Amendment 2, section 13); never in its place"}}
    try:
        concepts, _ = load_concepts(config.instrument(), config.EMBED_TAG, reg)
    except Exception as e:   # no archive yet: the attention half stands alone
        concepts = []
        out["advertising_half"] = {"status": f"no advertising data: {e}"}
    cb: dict[str, list] = {}
    for c in concepts:
        cb.setdefault(c.house_id, []).append(c)
    is_collection = lambda c: True   # noqa: E731  (replaced below whenever there are ads to sort)
    if concepts:
        from . import clothes
        worn = clothes.readings(clothes.DIR / "readings.jsonl") if (clothes.DIR / "readings.jsonl").exists() else {}
        is_collection = collection_sorter(store.ShardedTable(config.ADS_DIR, "ad_id").rows,
                                          {s for s, a in worn.items() if (a or {}).get("subject") == "worn_full"})
        out["advertising_half"] = bridge(events(panel, by_house), cb, panel, is_collection)
    table = event_table(panel, by_house, press, cb or None, scene, is_collection)
    out["what_the_clothes_add"] = what_the_clothes_add(table)
    write_table(table, config.RESULTS_DIR / "runway_events.csv")
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.RESULTS_DIR / "runway.json").write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(json.dumps({k: v.get("status") if isinstance(v, dict) else v for k, v in out.items()}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
