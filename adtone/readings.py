"""Readings: each brand's character, how it shifts, and what it contributes to success.

    python -m adtone.readings        # writes data/results/readings.json

Exploratory and descriptive throughout: nothing here is in the pre-registration and nothing here is a
call. It runs on the homepage history (adtone.homepages) because that needs no one's permission; the
same reading applies to ads, films and TikTok as they are collected, each source kept apart.

Two rules hold everywhere. A picture counts once in any window however many months it stayed up: a
campaign picture left up for four months is one choice, not four (counting it four times made shifts out
of nothing in the first draft of this module). And chance is judged by moving whole months, never single
pictures, because one month's homepage is one campaign in several crops.

The analysis goes in this order, because each step limits what the next can say.

1. What can be tracked. An answer that hardly varies cannot show a shift, and one the reader gives
   differently to the same picture is noise. For every answer: how often the commonest value is given,
   and how often two crops of the same picture (two pictures from one brand's homepage in one month
   whose fingerprints are near identical) get the same answer, as Cohen's kappa against chance. Answers
   that pass both go forward. Also reported: how strongly each answer separates brands.

2. Brands over time. Each brand's pictures in one half-year against the next, and in one calendar year
   against the next, by the answers and by the fingerprint, read three ways: everything on its homepage;
   like for like (campaign pictures against campaign pictures, product on a model against product on a
   model, the kinds weighed the same on both sides, so showing more product and fewer campaigns is not a
   change of style); and like for like net of the market (each kind's change less the same kind's change
   among the other brands with pictures in both windows). Each change is scaled by its own variance, with
   the pictures of one month taken together, and judged against splits that move whole months between the
   windows; for the market reading, months are moved for every other brand at once too, so the market's
   own noise counts. Scaling matters: the same change looks larger between twenty pictures and five than
   between twelve and twelve, and moving months evens the numbers out. Then corrected for how many changes
   were looked at (Benjamini-Hochberg). How large a change the test could have seen: part of a brand's
   pictures replaced by another brand's of the same kind from the same months, and the share caught.
   Turnover or drift: how far a brand's pictures are from its own at one, two, three and more half-years
   apart, beside two draws from one half-year and another brand's pictures.

3. Across brands. Which brands look alike (fingerprint centroids, and a map of them), whether brands of
   one owner look more alike than brands of different owners, how far each brand stands from the rest
   (on equal numbers of pictures, since a small sample looks more distinct than it is), and whether
   brands are converging or pulling apart.

4. The market as a whole. What the homepages show (campaign pictures, product on a model, packshots),
   and how the market's answers and fingerprint move half-year by half-year, each with an interval from
   resampling brands.

5. Events. Each change of creative lead (reference/designers.csv) and each change of owner (Wikidata):
   the brand's pictures in the twelve months before the first show against months three to fourteen
   after it, beside the same two windows for brands with no change of lead near them. Crew turnover
   from the credits.

6. Success. Each brand's shift and its distinctness beside the next half-year's organic revenue growth
   (adtone.revenue) and Wikipedia attention, each brand against its own usual and net of the market,
   with the current half-year's outcome held constant. Houses change their image when they are
   struggling as well as when they are thriving: any association is a pattern to examine.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import sys
from collections import Counter, defaultdict

import numpy as np

from . import character, config, registry, store
from .character import QUESTIONS, next_period, period_of

TYPES = {"brand_image": "campaign", "product_on_model": "on_model", "product_packshot": "packshot"}
LED = ("campaign", "on_model")
DUP_COS = 0.95          # two pictures of one brand-month this close are the same picture in another crop
MAX_TOP = 0.90          # an answer whose commonest value covers more than this cannot show a shift
MIN_KAPPA = 0.40        # nor one the reader gives differently to the same picture
MIN_PAIRS = 20
MIN_IMAGES = 6          # pictures in each window before a brand's change is measured
MIN_MONTHS = 2          # and months, since months are what the null moves
MIN_PEERS = 5           # other brands needed to stand for the market
MIN_PEER_IMAGES = 3
MIN_TYPE_PEERS = 3      # other brands with pictures of a kind in both windows, for that kind's baseline
N_PERM = 999
N_BOOT = 400
N_SUB = 50              # equal-size draws for distinctness and spread
PLANT = (0.25, 0.5, 1.0)
DESIGNERS_FILE = config.ROOT / "reference" / "designers.csv"


def _unit(v):
    n = np.linalg.norm(v)
    return v / n if n else v


# The fingerprint is a direction: brands are compared by the cosine between their centroids. A reading
# made of measures on their own scales (positions on an axis, colour and light) is compared by plain
# distance instead; run(euclid=True) switches every comparison between centroids to that.
EUCLID = False


def _norm(v):
    return v if EUCLID else _unit(v)


def _away(c, ref) -> float:
    """How far one centroid stands from another: one minus the cosine, or the distance between measures."""
    return float(np.linalg.norm(c - ref)) if EUCLID else float(1 - _unit(c) @ _unit(ref))


def _similarity(C: np.ndarray) -> np.ndarray:
    return -np.linalg.norm(C[:, None, :] - C[None, :, :], axis=2) if EUCLID else C @ C.T


def _dupvec(im):
    """The vector that says two pictures are crops of one picture: the fingerprint, kept apart under "dup"
    when the picture's vector is something else."""
    return im["dup"] if im.get("dup") is not None else im["vec"]


def _bh(ps: list[float]) -> list[float]:
    """Benjamini-Hochberg q-values, in the order given."""
    n = len(ps)
    if not n:
        return []
    order = np.argsort(ps)
    q = np.empty(n)
    run = 1.0
    for rank, i in reversed(list(enumerate(order, start=1))):
        run = min(run, ps[i] * n / rank)
        q[i] = run
    return [round(float(x), 4) for x in q]


# ---------- the pictures ----------

def load_images() -> list[dict]:
    """Every answered homepage picture, once per brand and month, with its kind and its fingerprint."""
    seen, out = set(), []
    for im in character.load_homepage_images(None):
        k = (im["house"], im["month"], im["sha"])
        if k in seen:
            continue
        seen.add(k)
        kind = TYPES.get(im["out"].get("creative_type"), "other")
        out.append({**im, "type": kind, "period": period_of(im["month"]),
                    "vec": _unit(np.asarray(im["vec"], float)) if im["vec"] is not None else None})
    return out


def once(images: list[dict], window=lambda m: m[:0]) -> list[dict]:
    """Each picture once per brand and window (by default the whole set), at the month it was first shown."""
    seen, out = set(), []
    for im in sorted(images, key=lambda i: (i["month"], i["sha"])):
        k = (im["house"], window(im["month"]), im["sha"])
        if k not in seen:
            seen.add(k)
            out.append(im)
    return out


def by_half(images: list[dict]) -> list[dict]:
    return once(images, period_of)


# ---------- 1. what can be tracked ----------

def _kappa(pairs: list[tuple], marg: Counter) -> float | None:
    if len(pairs) < MIN_PAIRS:
        return None
    po = sum(a == b for a, b in pairs) / len(pairs)
    n = sum(marg.values())
    pe = sum((c / n) ** 2 for c in marg.values())
    return None if pe >= 1 else (po - pe) / (1 - pe)


def _cramers_v(rows: list[tuple[str, str]]) -> float | None:
    """Bias-corrected Cramér's V between brand and answer (Bergsma 2013)."""
    if len(rows) < 30:
        return None
    brands = sorted({b for b, _ in rows})
    vals = sorted({str(v) for _, v in rows})
    if len(brands) < 2 or len(vals) < 2:
        return 0.0
    t = np.zeros((len(brands), len(vals)))
    bi, vi = {b: i for i, b in enumerate(brands)}, {v: i for i, v in enumerate(vals)}
    for b, v in rows:
        t[bi[b], vi[str(v)]] += 1
    n = t.sum()
    exp = t.sum(1, keepdims=True) * t.sum(0, keepdims=True) / n
    chi2 = float(((t - exp) ** 2 / np.where(exp > 0, exp, 1)).sum())
    r, k = t.shape
    phi2 = max(0.0, chi2 / n - (k - 1) * (r - 1) / (n - 1))
    rc, kc = r - (r - 1) ** 2 / (n - 1), k - (k - 1) ** 2 / (n - 1)
    d = min(kc - 1, rc - 1)
    return float(math.sqrt(phi2 / d)) if d > 0 else 0.0


def duplicate_pairs(images: list[dict]) -> list[tuple[dict, dict]]:
    by: dict[tuple, list] = defaultdict(list)
    for im in images:
        if _dupvec(im) is not None:
            by[(im["house"], im["month"])].append(im)
    out = []
    for ims in by.values():
        for a, b in itertools.combinations(ims, 2):
            if a["sha"] != b["sha"] and float(_dupvec(a) @ _dupvec(b)) >= DUP_COS:
                out.append((a, b))
    return out


def screen(images: list[dict], spec: dict) -> dict:
    """Per answer: how often its commonest value is given, how reliably the same picture gets the same
    answer (kappa over near-duplicate pairs), how strongly it separates brands, and whether it goes forward.
    On the pictures that lead (campaign pictures and product on a model), each once per half-year."""
    led = by_half([i for i in images if i["type"] in LED])
    pairs = duplicate_pairs(led)
    rows = {}

    def judge(name, get):
        values = [get(i["out"]) for i in led]
        marg = Counter(values)
        top = max(marg.values()) / len(values) if values else 1.0
        pair_vals = [(get(a["out"]), get(b["out"])) for a, b in pairs]
        kap = _kappa(pair_vals, marg)
        keep = top < MAX_TOP and (kap >= MIN_KAPPA if kap is not None else len(pairs) < MIN_PAIRS)
        v = _cramers_v([(i["house"], get(i["out"])) for i in led])
        rows[name] = {"top_value": marg.most_common(1)[0][0] if marg else None, "top_share": round(top, 3),
                      "values_used": sum(1 for c in marg.values() if c / max(1, len(values)) >= 0.02),
                      "kappa": None if kap is None else round(kap, 3), "pairs": len(pair_vals),
                      "brand_v": None if v is None else round(v, 3), "keep": bool(keep)}
    for q in QUESTIONS:
        judge(q, lambda o, q=q: o.get(q))
    for m in spec["lists"]["mood"]["options"]:
        judge(f"mood:{m}", lambda o, m=m: m in (o.get("mood") or []))
    judge("street_couture_axis", lambda o: o.get("street_couture_axis"))
    return {"answers": rows, "duplicate_pairs": len(pairs), "images": len(led),
            "rule": f"kept when the commonest value covers under {MAX_TOP:.0%} of pictures and two crops of the same "
                    f"picture agree beyond chance (kappa at least {MIN_KAPPA}) over at least {MIN_PAIRS} pairs"}


class Answers:
    """The answers that went forward, as one row of numbers per picture: a one-hot block per question and a
    yes/no block per mood. Distance: the mean over blocks of half the summed absolute difference (total
    variation for shares), each mood counting as a question of its own."""

    def __init__(self, spec: dict, keep: dict):
        self.qs = [q for q in QUESTIONS if keep.get(q, {}).get("keep")]
        self.vals = {q: list(spec["enums"][q]) for q in self.qs}
        self.moods = [m for m in spec["lists"]["mood"]["options"] if keep.get(f"mood:{m}", {}).get("keep")]
        self.blocks, i = [], 0
        for q in self.qs:
            self.blocks.append((q, i, i + len(self.vals[q])))
            i += len(self.vals[q])
        for m in self.moods:
            self.blocks.append((f"mood:{m}", i, i + 2))
            i += 2
        self.width = i
        self.labels = [(q, v) for q in self.qs for v in self.vals[q]] + \
                      [(f"mood:{m}", x) for m in self.moods for x in ("yes", "no")]

    def row(self, out: dict) -> np.ndarray:
        v = np.zeros(self.width)
        for k, q in enumerate(self.qs):
            ans = out.get(q)
            if ans in self.vals[q]:
                v[self.blocks[k][1] + self.vals[q].index(ans)] = 1.0
        for k, m in enumerate(self.moods):
            a = self.blocks[len(self.qs) + k][1]
            v[a if m in (out.get("mood") or []) else a + 1] = 1.0
        return v

    def rows(self, ims: list[dict]) -> np.ndarray:
        return np.vstack([self.row(i["out"]) for i in ims]) if ims else np.zeros((0, self.width))

    def block_tv(self, D: np.ndarray) -> np.ndarray:
        """Per block, half the summed absolute difference; D is one difference vector or a matrix of them."""
        D = np.atleast_2d(D)
        if not self.blocks:
            return np.zeros((len(D), 0))
        return np.stack([0.5 * np.abs(D[:, s:e]).sum(1) for _, s, e in self.blocks], axis=1)

    def distance(self, a: np.ndarray, b: np.ndarray) -> float:
        return float(self.block_tv(a - b).mean()) if self.blocks else float("nan")

    def changes(self, a: np.ndarray, b: np.ndarray, top: int = 4, floor: float = 0.1) -> list[dict]:
        d = b - a
        rows = [(float(d[j]), q, v) for j, (q, v) in enumerate(self.labels) if not (q.startswith("mood:") and v == "no")]
        rows.sort(key=lambda r: -abs(r[0]))
        return [{"answer": f"{q}={v}" if not q.startswith("mood:") else q.replace(":", "="), "change": round(x, 3)}
                for x, q, v in rows[:top] if abs(x) >= floor]

    def shares(self, m: np.ndarray) -> dict[str, float]:
        return {(f"{q}={v}" if not q.startswith("mood:") else q.replace(":", "=")): round(float(m[j]), 3)
                for j, (q, v) in enumerate(self.labels) if not (q.startswith("mood:") and v == "no")}


# ---------- 2. brands over time ----------

def by_house(images: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for im in images:
        if im["type"] in LED and im["vec"] is not None:
            out[im["house"]].append(im)
    return dict(out)


def _enough(items: list[dict]) -> bool:
    return len(items) >= MIN_IMAGES and len({i["month"] for i in items}) >= MIN_MONTHS


def _splits(g: int, k: int, n: int, rng, exact_ok: bool = True) -> tuple[np.ndarray, bool]:
    """Ways to put k of g months in the first window: every way when there are at most n, else n at random.
    Rows are splits over the months, True marks the first window."""
    if exact_ok and math.comb(g, k) <= n:
        C = np.zeros((math.comb(g, k), g), bool)
        for r, c in enumerate(itertools.combinations(range(g), k)):
            C[r, list(c)] = True
        return C, True
    return rng.random((n, g)).argsort(1).argsort(1) < k, False


def _contrast(items: list[dict], Z: np.ndarray, kinds: list[str] | None, w: np.ndarray, C: np.ndarray,
              months: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each split of the months (rows of C, True for the first window): the change d = sum over kinds of
    w_kind * (mean in the second window - mean in the first), and its variance, column by column, with the
    pictures of one month taken together (a month's homepage is one campaign in several crops, so the
    variance is clustered by month, with the small-sample factor G/(G-1) for each window's G months).
    kinds None treats all pictures as one kind. Also which splits leave every kind in both windows."""
    mi = {m: j for j, m in enumerate(months)}
    ks = kinds or ["all"]
    S = np.zeros((len(ks), len(months), Z.shape[1]))
    N = np.zeros((len(ks), len(months)))
    for i, it in enumerate(items):
        t = ks.index(it["type"]) if kinds else 0
        S[t, mi[it["month"]]] += Z[i]
        N[t, mi[it["month"]]] += 1
    out_d = np.zeros((len(C), Z.shape[1]))
    out_v = np.zeros((len(C), Z.shape[1]))
    ok = np.ones(len(C), bool)
    for F, sign in ((C.astype(float), -1.0), (1.0 - C.astype(float), 1.0)):
        n = F @ N.T                                             # splits x kinds
        ok &= (n > 0).all(1)
        n = np.where(n > 0, n, 1)
        m = [(F @ S[t]) / n[:, t:t + 1] for t in range(len(ks))]
        g = F.sum(1)
        factor = np.where(g > 1, g / np.maximum(g - 1, 1), 1.0)[:, None]
        var = np.zeros_like(out_v)
        for t in range(len(ks)):
            out_d += sign * w[t] * m[t]
            for u in range(len(ks)):
                # sum over this window's months of (S_t - N_t m_t)(S_u - N_u m_u)
                cross = (F @ (S[t] * S[u]) - m[u] * (F @ (N[u][:, None] * S[t])) - m[t] * (F @ (N[t][:, None] * S[u]))
                         + m[t] * m[u] * (F @ (N[t] * N[u]))[:, None])
                var += (w[t] / n[:, t:t + 1]) * (w[u] / n[:, u:u + 1]) * cross
        out_v += factor * np.maximum(var, 0.0)
    return out_d, out_v, ok


def _ratio(d: np.ndarray, V: np.ndarray, cols=slice(None)) -> np.ndarray:
    """Squared change over its variance, summed over the columns given: about one when nothing changed,
    whatever the numbers of pictures and months on each side."""
    num, den = (d[:, cols] ** 2).sum(1), V[:, cols].sum(1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(den > 1e-15, num / np.where(den > 1e-15, den, 1), np.where(num > 1e-15, np.inf, 0.0))


def _p(null: np.ndarray, obs: float, exact: bool) -> float:
    ge = int((null >= obs - 1e-12).sum())
    return ge / len(null) if exact else (1 + ge) / (1 + len(null))


def _score(obs: float, null: np.ndarray) -> float:
    """The change against the edge of chance: the observed statistic over the 95th percentile of the null,
    so above 1 is beyond what moving months at random gives one time in twenty. (Against the median it
    overstated changes whose null has a long tail: a brand whose campaigns differ a lot within each year.)"""
    edge = float(np.quantile(null[np.isfinite(null)], 0.95)) if np.isfinite(null).any() else 0.0
    return round(obs / edge, 3) if edge > 0 and np.isfinite(obs) else (99.0 if obs > 0 else 1.0)


def _report(out: dict, ans: Answers, d_o, V_o, d_n, V_n, exact: bool) -> dict:
    """Effect sizes in plain units (answers: mean over questions of the change in shares, as total variation;
    fingerprint: length of the change in the mean fingerprint) and p-values from the variance-scaled
    change, which does not grow or shrink with the numbers of pictures on each side. The columns are the
    answers' first, then the fingerprint's."""
    w = ans.width
    if ans.blocks:
        tb_o = np.stack([_ratio(d_o, V_o, slice(s, e)) for _, s, e in ans.blocks], 1)
        tb_n = np.stack([_ratio(d_n, V_n, slice(s, e)) for _, s, e in ans.blocks], 1)
        o, n = float(tb_o.mean()), tb_n.mean(1)
        tv = ans.block_tv(d_o[:, :w])[0]
        out.update({"answers": round(float(tv.mean()), 4), "answers_score": _score(o, n), "answers_p": round(_p(n, o, exact), 4)})
        out["by_answer"] = {name: {"change": round(float(tv[k]), 3), "p": round(_p(tb_n[:, k], tb_o[0, k], exact), 4)}
                            for k, (name, _, _) in enumerate(ans.blocks)}
    o, n = float(_ratio(d_o, V_o, slice(w, None))[0]), _ratio(d_n, V_n, slice(w, None))
    out.update({"print": round(float(np.linalg.norm(d_o[0, w:])), 4), "print_score": _score(o, n),
                "print_p": round(_p(n, o, exact), 4)})
    out["moved"] = ans.changes(np.zeros(w), d_o[0, :w])
    return out


def _matrix(items: list[dict], ans: Answers) -> np.ndarray:
    return np.hstack([ans.rows(items), np.vstack([i["vec"] for i in items])])


def own_test(a: list[dict], b: list[dict], ans: Answers, rng, n_perm: int = N_PERM) -> dict:
    """How far everything on the brand's homepage moved between two windows, by the answers (and each
    answer on its own) and by the fingerprint, against splits that move whole months between the windows."""
    items = a + b
    months = sorted({i["month"] for i in items})
    first = {i["month"] for i in a}
    Z = _matrix(items, ans)
    C, exact = _splits(len(months), len(first), n_perm, rng)
    d_o, V_o, _ = _contrast(items, Z, None, np.ones(1), np.array([[m in first for m in months]]), months)
    d_n, V_n, _ = _contrast(items, Z, None, np.ones(1), C, months)
    return _report({"splits": len(C), "exact": exact}, ans, d_o, V_o, d_n, V_n, exact)


def _peer_change(ta: list[dict], tb: list[dict], ans: Answers, rng, n_perm: int) -> tuple:
    """Another brand's change within one kind between the two windows: observed, and under random splits
    of its months."""
    its = ta + tb
    pm = sorted({i["month"] for i in its})
    pf = {i["month"] for i in ta}
    Z = _matrix(its, ans)
    C, _ = _splits(len(pm), len(pf), n_perm, rng, exact_ok=False)
    d_o, V_o, _ = _contrast(its, Z, None, np.ones(1), np.array([[m in pf for m in pm]]), pm)
    d_n, V_n, _ = _contrast(its, Z, None, np.ones(1), C, pm)
    return d_o, V_o, d_n, V_n


def kind_test(a: list[dict], b: list[dict], ans: Answers, rng, n_perm: int = N_PERM,
              peers: dict[str, tuple[list, list]] | None = None, cache: dict | None = None) -> dict | None:
    """Like for like: the change within each kind of picture (campaign pictures against campaign pictures,
    product on a model against product on a model), the kinds weighed by the brand's own mix over both
    windows together, the same weights on both sides, so showing more product and fewer campaigns is not a
    change. Splits that leave a kind in one window only are not used.

    With peers, net of the market: each kind's change less the same kind's change among the other brands
    (each brand weighing the same). The null then moves whole months for the brand and, independently,
    for every other brand and kind, so noise in the market's change counts as well as the brand's. A
    cache (one per pair of windows) keeps each other brand's draws, so they are made once."""
    kinds = [t for t in LED if any(i["type"] == t for i in a) and any(i["type"] == t for i in b)]
    parts = None
    if peers is not None:
        parts = {t: [] for t in kinds}
        for g, (pa, pb) in sorted(peers.items()):
            for t in kinds:
                ta, tb = [i for i in pa if i["type"] == t], [i for i in pb if i["type"] == t]
                if ta and tb:
                    parts[t].append((g, ta, tb))
        kinds = [t for t in kinds if len(parts[t]) >= MIN_TYPE_PEERS]
    a2, b2 = [i for i in a if i["type"] in kinds], [i for i in b if i["type"] in kinds]
    if not (kinds and _enough(a2) and _enough(b2)):
        return None
    items = a2 + b2
    months = sorted({i["month"] for i in items})
    first = {i["month"] for i in a2}
    Z = _matrix(items, ans)
    w = np.array([sum(i["type"] == t for i in items) for t in kinds], float)
    w /= w.sum()
    C, exact = _splits(len(months), len(first), n_perm, rng, exact_ok=peers is None)
    d_o, V_o, _ = _contrast(items, Z, kinds, w, np.array([[m in first for m in months]]), months)
    d_n, V_n, ok = _contrast(items, Z, kinds, w, C, months)
    out = {"kinds": kinds, "mix": {t: round(float(x), 3) for t, x in zip(kinds, w)}, "n0": len(a2), "n1": len(b2)}
    if peers is not None:
        out["baseline_brands"] = {t: len(parts[t]) for t in kinds}
        for j, t in enumerate(kinds):
            k = len(parts[t])
            for g, ta, tb in parts[t]:
                got = None if cache is None else cache.get((g, t))
                if got is None or len(got[2]) != n_perm:
                    got = _peer_change(ta, tb, ans, rng, n_perm)
                    if cache is not None:
                        cache[(g, t)] = got
                pd_o, pV_o, pd_n, pV_n = got
                # the market's change for this kind is the mean of the brands' changes, weighed w[j] in the whole
                d_o, d_n = d_o - w[j] * pd_o / k, d_n - w[j] * pd_n / k
                V_o, V_n = V_o + (w[j] / k) ** 2 * pV_o, V_n + (w[j] / k) ** 2 * pV_n
    d_n, V_n = d_n[ok], V_n[ok]
    if len(d_n) < 20:
        return None
    out.update({"splits": int(ok.sum()), "exact": exact})
    return _report(out, ans, d_o, V_o, d_n, V_n, exact)


SIDES = ("own", "like_for_like", "against_market")


def compare(houses: dict[str, list[dict]], h: str, sel0, sel1, ans: Answers, rng, n_perm: int = N_PERM,
            market: bool = True, cache: dict | None = None) -> dict | None:
    """Three readings of a brand's change between two windows of months: everything on its homepage
    ("own"), like for like within each kind of picture, and (if asked) like for like net of the market.
    Pass one cache per pair of windows to share the other brands' draws between brands."""
    a = once([i for i in houses.get(h, []) if sel0(i["month"])])
    b = once([i for i in houses.get(h, []) if sel1(i["month"])])
    if not (_enough(a) and _enough(b)):
        return None
    out = {"n0": len(a), "n1": len(b), "months0": len({i["month"] for i in a}), "months1": len({i["month"] for i in b}),
           "mix0": {t: round(sum(i["type"] == t for i in a) / len(a), 3) for t in LED},
           "mix1": {t: round(sum(i["type"] == t for i in b) / len(b), 3) for t in LED},
           "own": own_test(a, b, ans, rng, n_perm), "like_for_like": kind_test(a, b, ans, rng, n_perm)}
    if not market:
        return out
    peers = {}
    for g, ims in houses.items():
        if g == h:
            continue
        pa, pb = once([i for i in ims if sel0(i["month"])]), once([i for i in ims if sel1(i["month"])])
        if len(pa) >= MIN_PEER_IMAGES and len(pb) >= MIN_PEER_IMAGES:
            peers[g] = (pa, pb)
    out["market_brands"] = len(peers)
    out["against_market"] = kind_test(a, b, ans, rng, n_perm, peers, cache) if len(peers) >= MIN_PEERS else None
    return out


def reading(c: dict, key: str, alpha: float = 0.05) -> str:
    """Like for like, on its own and against the market."""
    lfl, am = c.get("like_for_like"), c.get("against_market")
    own = lfl is not None and lfl.get(f"{key}_p", 1) <= alpha
    rel = am is not None and am.get(f"{key}_p", 1) <= alpha
    if own and rel:
        return "moved on its own"
    if own:
        return "moved with the market"
    if rel:
        return "held while the market moved"
    return "no change beyond chance"


def shifts(houses: dict[str, list[dict]], ans: Answers, rng, grain: str = "half") -> dict[str, list[dict]]:
    """Consecutive windows for every brand: half-years, or calendar years."""
    key = period_of if grain == "half" else (lambda m: m[:4])
    step = next_period if grain == "half" else (lambda y: str(int(y) + 1))
    windows = sorted({key(i["month"]) for ims in houses.values() for i in ims})
    out: dict[str, list[dict]] = defaultdict(list)
    caches: dict[tuple, dict] = defaultdict(dict)
    for h in sorted(houses):
        for w0, w1 in zip(windows, windows[1:]):
            if step(w0) != w1:
                continue
            c = compare(houses, h, lambda m, w=w0: key(m) == w, lambda m, w=w1: key(m) == w, ans, rng,
                        cache=caches[(w0, w1)])
            if c:
                out[h].append({"from": w0, "to": w1, **c, "reading": {k: reading(c, k) for k in ("answers", "print")}})
    rows = [s for r in out.values() for s in r]
    for side in SIDES:
        for k in ("answers", "print"):
            have = [s for s in rows if s.get(side) and f"{k}_p" in s[side]]
            for s, q in zip(have, _bh([s[side][f"{k}_p"] for s in have])):
                s[side][f"{k}_q"] = q
    return dict(out)


def shift_summary(rows: dict[str, list[dict]]) -> dict:
    al = [s for r in rows.values() for s in r]
    out = {"measured": len(al), "expected_by_chance": round(0.05 * len(al), 1)}
    for side in SIDES:
        for k in ("answers", "print"):
            have = [s[side] for s in al if s.get(side) and f"{k}_p" in s[side]]
            out[f"{side}_{k}"] = {"tested": len(have), "expected_by_chance": round(0.05 * len(have), 1),
                                  "p05": sum(1 for t in have if t[f"{k}_p"] <= 0.05),
                                  "q10": sum(1 for t in have if t.get(f"{k}_q", 1) <= 0.10)}
    out["readings"] = {k: dict(Counter(s["reading"][k] for s in al)) for k in ("answers", "print")}
    # each answer on its own, for each brand's change: how many beyond chance, and which survive a
    # false-discovery rate of 10% across all of them
    for side in SIDES:
        cells = [(h, s["from"], s["to"], name, t["p"], t["change"]) for h, r in rows.items() for s in r if s.get(side)
                 for name, t in s[side].get("by_answer", {}).items()]
        qs = _bh([c[4] for c in cells])
        out[f"{side}_by_answer"] = {
            "tested": len(cells), "expected_by_chance": round(0.05 * len(cells), 1),
            "p05": sum(1 for c in cells if c[4] <= 0.05),
            "surviving": [{"brand": h, "from": a, "to": b, "answer": n, "p": p, "q": q, "change": ch}
                          for (h, a, b, n, p, ch), q in zip(cells, qs) if q <= 0.10],
            "by_question": {n: sum(1 for c in cells if c[3] == n and c[4] <= 0.05) for n in sorted({c[3] for c in cells})}}
    return out


def _lag(p: str, q: str) -> int:
    return abs((int(q[:4]) * 2 + int(q[-1])) - (int(p[:4]) * 2 + int(p[-1])))


def by_lag(houses: dict[str, list[dict]], ans: Answers, rng, draws: int = N_SUB) -> dict:
    """Turnover or drift. For each brand, the distance between its pictures in two half-years against how
    far apart the half-years are: none (two separate draws from one half-year), one (consecutive), two
    (the same season a year apart), and so on. Every side is the same number of pictures, drawn again and
    again, so small samples do not inflate any lag more than another. A brand whose look fluctuates around
    a settled centre is as far from itself at four half-years as at two; a brand that drifts is farther.
    Across brands: the two differences (one half-year against none: turnover; four against two, both the
    same season: drift), each with a null that flips the sign of each brand's difference."""
    allcells: dict[str, dict[str, list]] = {}
    for h, ims in houses.items():
        allcells[h] = defaultdict(list)
        for i in by_half(ims):
            allcells[h][i["period"]].append(i)
    rows = {}
    for h in sorted(houses):
        cells = allcells[h]
        ps = sorted(p for p, c in cells.items() if _enough(c))
        m = min((len(cells[p]) for p in ps), default=0) // 2
        if len(ps) < 3 or m < 3:
            continue
        acc_v: dict[int, list] = defaultdict(list)
        acc_a: dict[int, list] = defaultdict(list)
        for _ in range(draws):
            sub, sub2 = {}, {}
            for p in ps:
                order = rng.permutation(len(cells[p]))
                sub[p], sub2[p] = [cells[p][j] for j in order[:m]], [cells[p][j] for j in order[m:2 * m]]
            cv = {p: np.vstack([i["vec"] for i in sub[p]]).mean(0) for p in ps}
            ca = {p: ans.rows(sub[p]).mean(0) for p in ps}
            for p in ps:
                acc_v[0].append(float(np.linalg.norm(cv[p] - np.vstack([i["vec"] for i in sub2[p]]).mean(0))))
                if ans.blocks:
                    acc_a[0].append(ans.distance(ca[p], ans.rows(sub2[p]).mean(0)))
                others = [g for g in houses if g != h and len(allcells[g].get(p, [])) >= m]
                if others:
                    g = others[rng.integers(len(others))]
                    oth = [allcells[g][p][j] for j in rng.choice(len(allcells[g][p]), m, replace=False)]
                    acc_v[-1].append(float(np.linalg.norm(cv[p] - np.vstack([i["vec"] for i in oth]).mean(0))))
                    if ans.blocks:
                        acc_a[-1].append(ans.distance(ca[p], ans.rows(oth).mean(0)))
            for p, q in itertools.combinations(ps, 2):
                acc_v[_lag(p, q)].append(float(np.linalg.norm(cv[p] - cv[q])))
                if ans.blocks:
                    acc_a[_lag(p, q)].append(ans.distance(ca[p], ca[q]))
        name = lambda k: "another brand" if k < 0 else str(k)
        rows[h] = {"pictures_per_side": m, "half_years": ps,
                   "print": {name(k): round(float(np.mean(v)), 4) for k, v in sorted(acc_v.items())},
                   "answers": {name(k): round(float(np.mean(v)), 4) for k, v in sorted(acc_a.items())} if acc_a else {}}

    def paired(key, hi, lo):
        d = np.array([r[key][hi] - r[key][lo] for r in rows.values() if hi in r[key] and lo in r[key]])
        if len(d) < 4:
            return {"brands": len(d)}
        flips = np.array([np.mean(d * rng.choice([-1, 1], len(d))) for _ in range(N_PERM)])
        return {"brands": len(d), "mean_difference": round(float(d.mean()), 4), "larger": int((d > 0).sum()),
                "p_two_sided": round((1 + int((np.abs(flips) >= abs(d.mean()) - 1e-12).sum())) / (1 + N_PERM), 4)}
    pooled = {}
    for key in ("print", "answers"):
        lags = sorted({k for r in rows.values() for k in r[key]}, key=lambda k: int(k) if k.isdigit() else 99)
        pooled[key] = {"by_lag": {k: {"brands": sum(1 for r in rows.values() if k in r[key]),
                                      "mean": round(float(np.mean([r[key][k] for r in rows.values() if k in r[key]])), 4)}
                                  for k in lags},
                       "turnover_1_vs_0": paired(key, "1", "0"), "season_1_vs_2": paired(key, "1", "2"),
                       "drift_4_vs_2": paired(key, "4", "2"), "drift_3_vs_1": paired(key, "3", "1"),
                       "another_brand_vs_3": paired(key, "another brand", "3")}
    return {"brands": rows, "pooled": pooled}


def power(houses: dict[str, list[dict]], half_rows: dict[str, list[dict]], ans: Answers, rng,
          fractions=PLANT, draws: int = 4) -> dict:
    """How large a change the half-year test catches. In each measured pair, a share of the later
    half-year's pictures is replaced by pictures another brand showed in the same half-year, each by one of
    the same kind and in the same month, so the mix stays as it was; then the like-for-like test is run
    again. The share of plants caught, by the answers and by the fingerprint."""
    halves = defaultdict(dict)
    for g, ims in houses.items():
        for p in {i["period"] for i in ims}:
            halves[p][g] = once([i for i in ims if i["period"] == p])
    out = {}
    for f in fractions:
        hit_a = hit_v = n = 0
        for h, rows in half_rows.items():
            for s in rows:
                a = once([i for i in houses[h] if i["period"] == s["from"]])
                b = once([i for i in houses[h] if i["period"] == s["to"]])
                swap_n = max(1, int(round(f * len(b))))
                for _ in range(draws):
                    swap = rng.choice(len(b), swap_n, replace=False)
                    need = Counter(b[j]["type"] for j in swap)
                    donors = [g for g, ims in halves[s["to"]].items()
                              if g != h and all(sum(i["type"] == t for i in ims) >= c for t, c in need.items())]
                    if not donors:
                        continue
                    g = donors[rng.integers(len(donors))]
                    pool = {t: [i for i in halves[s["to"]][g] if i["type"] == t] for t in need}
                    order = {t: list(rng.permutation(len(pool[t]))) for t in need}
                    b2 = list(b)
                    for j in swap:
                        t = b[j]["type"]
                        b2[j] = {**pool[t][order[t].pop()], "month": b[j]["month"], "house": h}
                    got = kind_test(a, b2, ans, rng)
                    if got is None:
                        continue
                    hit_a += got.get("answers_p", 1) <= 0.05
                    hit_v += got["print_p"] <= 0.05
                    n += 1
        out[f"{f:.2f}"] = {"plants": n, "caught_by_answers": round(hit_a / n, 3) if n else None,
                           "caught_by_print": round(hit_v / n, 3) if n else None}
    return out


# ---------- 3. across brands ----------

def _centroid(ims: list[dict]) -> np.ndarray:
    return _norm(np.vstack([i["vec"] for i in ims]).mean(0))


def _distinct(cells: dict[str, list[dict]], ans: Answers, rng, n_sub: int = MIN_IMAGES, draws: int = N_SUB) -> dict:
    """How far each brand stands from the rest, on n_sub pictures per brand drawn again and again, so that
    brands with many pictures and brands with few are measured alike."""
    hs = sorted(cells)
    acc_v, acc_a = defaultdict(list), defaultdict(list)
    for _ in range(draws):
        picks = {h: [cells[h][j] for j in rng.choice(len(cells[h]), n_sub, replace=False)] for h in hs}
        C = np.vstack([_centroid(picks[h]) for h in hs])
        A = np.vstack([ans.rows(picks[h]).mean(0) for h in hs])
        for i, h in enumerate(hs):
            rest = [j for j in range(len(hs)) if j != i]
            acc_v[h].append(_away(C[i], C[rest].mean(0)))
            if ans.blocks:
                acc_a[h].append(ans.distance(A[i], A[rest].mean(0)))
    return {h: {"print": round(float(np.mean(acc_v[h])), 4),
                "answers": round(float(np.mean(acc_a[h])), 4) if acc_a[h] else None} for h in hs}


def cross_brand(images: list[dict], ans: Answers, reg: registry.Registry, rng) -> dict:
    owners = {h.id: h.owner for h in reg.houses}
    led = once([i for i in images if i["type"] in LED and i["vec"] is not None], lambda m: m[:4])
    years: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for im in led:
        years[im["month"][:4]][im["house"]].append(im)
    out: dict = {"years": {}}
    for y, by in sorted(years.items()):
        hs = sorted(h for h, ims in by.items() if _enough(ims))
        if len(hs) < 4:
            continue
        C = np.vstack([_centroid(by[h]) for h in hs])
        S = _similarity(C)
        near = {}
        for i, h in enumerate(hs):
            order = [j for j in np.argsort(-S[i]) if j != i][:3]
            near[h] = [{"brand": hs[j], "similarity": round(float(S[i, j]), 3)} for j in order]
        Cc = C - C.mean(0)
        _, s, vt = np.linalg.svd(Cc, full_matrices=False)
        xy = Cc @ vt[:2].T
        share = (s[:2] ** 2 / (s ** 2).sum()).tolist() if s.sum() else [0, 0]
        grouped = [h for h in hs if owners.get(h) and sum(1 for g in hs if owners.get(g) == owners.get(h)) >= 2]
        sib = None
        if len(grouped) >= 4 and len({owners[h] for h in grouped}) >= 2:
            idx = [hs.index(h) for h in grouped]
            labs = np.array([owners[h] for h in grouped])
            sub = S[np.ix_(idx, idx)]
            iu = np.triu_indices(len(idx), 1)

            def gap(lab):
                same = lab[iu[0]] == lab[iu[1]]
                return float(sub[iu][same].mean() - sub[iu][~same].mean()) if same.any() and (~same).any() else 0.0
            obs = gap(labs)
            null = np.array([gap(rng.permutation(labs)) for _ in range(N_PERM)])
            sib = {"brands": len(grouped), "owners": sorted({owners[h] for h in grouped}), "gap": round(obs, 4),
                   "p": round((1 + int((null >= obs - 1e-12).sum())) / (1 + len(null)), 4)}
        out["years"][y] = {"brands": hs, "images": {h: len(by[h]) for h in hs}, "nearest": near,
                           "similarity": [[round(float(x), 3) for x in row] for row in S],
                           "distinct": _distinct({h: by[h] for h in hs}, ans, rng),
                           "map": {h: [round(float(xy[i, 0]), 4), round(float(xy[i, 1]), 4)] for i, h in enumerate(hs)},
                           "map_variance": [round(x, 3) for x in share], "same_owner": sib}
    return out


def spread(images: list[dict], rng, n_perm: int = N_PERM) -> dict:
    """Whether brands are converging or pulling apart: the mean of one minus the cosine between each brand's
    centroid and the market's, by half-year. A small sample sits farther from the market than its brand
    does, so each change is measured on the brands present in both half-years with the same number of
    pictures on both sides (drawn again and again), against a null that swaps each brand's two half-years."""
    led = by_half([i for i in images if i["type"] in LED and i["vec"] is not None])
    cell: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for im in led:
        cell[im["period"]][im["house"]].append(im)
    periods = sorted(cell)

    def disp(C):
        if EUCLID:
            return float(np.linalg.norm(C - C.mean(0), axis=1).mean())
        return float(np.mean(1 - C @ _unit(C.mean(0))))
    changes = {}
    for p0, p1 in zip(periods, periods[1:]):
        if next_period(p0) != p1:
            continue
        common = sorted(h for h in set(cell[p0]) & set(cell[p1]) if _enough(cell[p0][h]) and _enough(cell[p1][h]))
        if len(common) < 5:
            continue
        sizes = {h: min(len(cell[p0][h]), len(cell[p1][h])) for h in common}
        A, B = [], []
        for _ in range(N_SUB):
            A.append(np.vstack([_centroid([cell[p0][h][j] for j in rng.choice(len(cell[p0][h]), sizes[h], replace=False)])
                                for h in common]))
            B.append(np.vstack([_centroid([cell[p1][h][j] for j in rng.choice(len(cell[p1][h]), sizes[h], replace=False)])
                                for h in common]))
        lev0, lev1 = float(np.mean([disp(x) for x in A])), float(np.mean([disp(x) for x in B]))
        obs = lev1 - lev0
        null = []
        for _ in range(n_perm):
            r = rng.integers(N_SUB)
            sw = rng.random(len(common)) < 0.5
            null.append(disp(np.where(sw[:, None], A[r], B[r])) - disp(np.where(sw[:, None], B[r], A[r])))
        null = np.array(null)
        changes[f"{p0}->{p1}"] = {"brands": len(common), "spread_from": round(lev0, 4), "spread_to": round(lev1, 4),
                                  "change": round(obs, 4),
                                  "p_two_sided": round((1 + int((np.abs(null) >= abs(obs) - 1e-12).sum())) / (1 + n_perm), 4)}
    return {"changes": changes}


# ---------- 4. the market as a whole ----------

def market(images: list[dict], ans: Answers, rng, n_boot: int = N_BOOT, n_perm: int = N_PERM) -> dict:
    """What the homepages show and how the market's pictures move, each brand weighing the same."""
    uniq = by_half(images)
    by: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for im in uniq:
        by[im["period"]][im["house"]].append(im)
    periods = sorted(by)
    kinds = ("campaign", "on_model", "packshot", "other")
    comp, trends, per_brand = {}, {"campaign": {}, "on_model": {}}, defaultdict(dict)
    for p in periods:
        hs = sorted(h for h, ims in by[p].items() if len(ims) >= 3)
        for h in hs:
            per_brand[h][p] = {t: round(sum(1 for i in by[p][h] if i["type"] == t) / len(by[p][h]), 3) for t in kinds} | \
                              {"pictures": len(by[p][h])}
        if len(hs) < 5:
            continue
        shares = np.array([[sum(1 for i in by[p][h] if i["type"] == t) / len(by[p][h]) for t in kinds] for h in hs])
        boot = np.array([shares[rng.integers(0, len(hs), len(hs))].mean(0) for _ in range(n_boot)])
        comp[p] = {"brands": len(hs), **{t: round(float(shares[:, k].mean()), 3) for k, t in enumerate(kinds)},
                   **{f"{t}_low": round(float(np.percentile(boot[:, k], 5)), 3) for k, t in enumerate(kinds[:3])},
                   **{f"{t}_high": round(float(np.percentile(boot[:, k], 95)), 3) for k, t in enumerate(kinds[:3])}}
        for t in ("campaign", "on_model"):
            rows = [ans.rows([i for i in by[p][h] if i["type"] == t]).mean(0) for h in hs
                    if sum(1 for i in by[p][h] if i["type"] == t) >= 3]
            if len(rows) >= 5 and ans.width:
                M = np.vstack(rows)
                bm = np.array([M[rng.integers(0, len(M), len(M))].mean(0) for _ in range(n_boot)])
                trends[t][p] = {"brands": len(rows), "mean": M.mean(0).round(3).tolist(),
                                "low": np.percentile(bm, 5, axis=0).round(3).tolist(),
                                "high": np.percentile(bm, 95, axis=0).round(3).tolist()}
    # the market's move between consecutive half-years, on the brands present in both
    drift = {}
    for p0, p1 in zip(periods, periods[1:]):
        if next_period(p0) != p1:
            continue
        common = sorted(h for h in set(by[p0]) & set(by[p1])
                        if sum(1 for i in by[p0][h] if i["type"] in LED) >= 3 and sum(1 for i in by[p1][h] if i["type"] in LED) >= 3)
        if len(common) < 5:
            continue
        A = np.vstack([_centroid([i for i in by[p0][h] if i["type"] in LED]) for h in common])
        B = np.vstack([_centroid([i for i in by[p1][h] if i["type"] in LED]) for h in common])
        RA = np.vstack([ans.rows([i for i in by[p0][h] if i["type"] in LED]).mean(0) for h in common])
        RB = np.vstack([ans.rows([i for i in by[p1][h] if i["type"] in LED]).mean(0) for h in common])
        sw = rng.random((n_perm, len(common))) < 0.5
        o_v = _away(A.mean(0), B.mean(0))
        n_v = np.array([_away(np.where(s[:, None], B, A).mean(0), np.where(s[:, None], A, B).mean(0)) for s in sw])
        row = {"brands": len(common), "print": round(o_v, 4), "print_chance": round(float(np.median(n_v)), 4),
               "print_p": round((1 + int((n_v >= o_v - 1e-12).sum())) / (1 + n_perm), 4)}
        if ans.blocks:
            o_a = ans.distance(RA.mean(0), RB.mean(0))
            n_a = np.array([ans.distance(np.where(s[:, None], RB, RA).mean(0), np.where(s[:, None], RA, RB).mean(0)) for s in sw])
            row.update({"answers": round(o_a, 4), "answers_chance": round(float(np.median(n_a)), 4),
                        "answers_p": round((1 + int((n_a >= o_a - 1e-12).sum())) / (1 + n_perm), 4),
                        "moved": ans.changes(RA.mean(0), RB.mean(0), top=5, floor=0.05)})
        drift[f"{p0}->{p1}"] = row
    return {"composition": comp, "brands": dict(per_brand),
            "answers": {"labels": [f"{q}={v}" for q, v in ans.labels], **trends}, "drift": drift}


# ---------- 5. events ----------

def _shift_months(month: str, n: int) -> str:
    y, m = int(month[:4]), int(month[5:7])
    k = y * 12 + (m - 1) + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def designer_events(path=DESIGNERS_FILE) -> list[dict]:
    out = []
    if not path.exists():
        return out
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["scope"] in ("womenswear", "both") and r["first_show_date"] >= "2014-01-01":
                out.append({"house": r["house_id"], "kind": "creative lead", "who": r["designer"], "date": r["first_show_date"],
                            "verified": r["verified"] == "true"})
    return out


def owner_events() -> list[dict]:
    facts = store.read_state(config.DATA / "wikidata" / "houses.json")
    out = []
    for h, f in facts.items():
        for prop in ("owned_by", "parent"):
            for r in f.get(prop) or []:
                s = str(r.get("start") or "")
                if len(s) >= 7 and s[:4] >= "2014" and s[5:7] != "00":
                    out.append({"house": h, "kind": "owner", "who": str(r.get("value")), "date": s[:7] + "-01", "verified": False})
    seen, uniq = set(), []
    for e in out:
        k = (e["house"], e["date"][:7])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    return uniq


def event_study(houses: dict[str, list[dict]], events: list[dict], ans: Answers, rng) -> dict:
    """Each event: the brand's pictures in the twelve months before the first show against months three to
    fourteen after it (campaigns follow shows), like for like on its own and against the market; and the
    same two windows, like for like, for every brand with no change of creative lead within a year of
    either window. A campaign runs for months, so moving months between two long windows finds some change
    for most brands: the brands without a change are the yardstick."""
    lead_dates: dict[str, list[str]] = defaultdict(list)
    for e in events:
        if e["kind"] == "creative lead":
            lead_dates[e["house"]].append(e["date"][:7])
    rows = []
    for e in sorted(events, key=lambda e: (e["date"], e["house"])):
        m = e["date"][:7]
        pre = (_shift_months(m, -12), _shift_months(m, -1))
        post = (_shift_months(m, 3), _shift_months(m, 14))
        s0 = lambda x, pre=pre: pre[0] <= x <= pre[1]
        s1 = lambda x, post=post: post[0] <= x <= post[1]
        row = {**e, "pre": f"{pre[0]} to {pre[1]}", "post": f"{post[0]} to {post[1]}"}
        if e["house"] not in houses:
            row["note"] = "no homepage pictures"
            rows.append(row)
            continue
        c = compare(houses, e["house"], s0, s1, ans, rng)
        if c is None or c.get("like_for_like") is None:
            row["note"] = "too few pictures on one side" if c is None else "too few pictures of one kind on one side"
            if c is not None:
                row.update(c)
            rows.append(row)
            continue
        row.update(c)
        row["reading"] = {k: reading(c, k) for k in ("answers", "print")}
        lo, hi = _shift_months(pre[0], -12), _shift_months(post[1], 12)
        ctrl = []
        for h in sorted(houses):
            if h == e["house"] or any(lo <= d <= hi for d in lead_dates.get(h, [])):
                continue
            cc = compare(houses, h, s0, s1, ans, rng, n_perm=199, market=False)
            if cc and cc.get("like_for_like"):
                ctrl.append((h, cc["like_for_like"]))
        if ctrl and c.get("like_for_like"):
            own = c["like_for_like"]
            row["controls"] = [h for h, _ in ctrl]
            for k in ("answers", "print"):
                if f"{k}_score" in own:
                    sc = [t[f"{k}_score"] for _, t in ctrl if f"{k}_score" in t]
                    row[f"own_{k}_score"] = own[f"{k}_score"]
                    row[f"controls_{k}_score"] = round(float(np.median(sc)), 3)
                    row[f"controls_{k}_above"] = round(float(np.mean([x >= own[f"{k}_score"] for x in sc])), 3)
        rows.append(row)
    tested = [r for r in rows if "controls" in r]
    summary = {"events": len(rows), "tested": len(tested)}
    for key in ("answers", "print"):
        d = np.array([math.log(max(r[f"own_{key}_score"], 1e-3) / max(r[f"controls_{key}_score"], 1e-3))
                      for r in tested if f"own_{key}_score" in r])
        if len(d) >= 3:
            flips = np.array([np.mean(d * rng.choice([-1, 1], len(d))) for _ in range(N_PERM)])
            summary[f"{key}_ratio_to_controls"] = round(float(math.exp(d.mean())), 3)
            summary[f"{key}_above_controls"] = int((d > 0).sum())
            summary[f"{key}_p"] = round((1 + int((flips >= d.mean() - 1e-12).sum())) / (1 + N_PERM), 4)
    return {"rows": rows, "summary": summary}


def crew_turnover(path=None) -> dict[str, dict[str, float]]:
    """Share of a brand's photographers in a year who did not shoot for it the year before."""
    from . import credits as cr
    rows = cr.select(cr.load() if path is None else cr.load(path), "photographer")
    by: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for c in rows:
        if c.published[:4].isdigit():
            by[c.house][c.published[:4]].add(cr.norm(c.person))
    out: dict[str, dict[str, float]] = {}
    for h, ys in by.items():
        for y in sorted(ys):
            prev = ys.get(str(int(y) - 1))
            if prev:
                out.setdefault(h, {})[y] = round(1 - len(ys[y] & prev) / len(ys[y] | prev), 3)
    return out


# ---------- 6. success ----------

def attention_halves(houses: list[str]) -> dict[str, dict[str, float]]:
    """Mean log daily English Wikipedia views by half-year."""
    out: dict[str, dict[str, float]] = {}
    for h in houses:
        acc: dict[str, list] = defaultdict(list)
        for r in store.read_jsonl(config.DATA / "attention" / f"{h}.jsonl"):
            try:
                acc[period_of(r["date"][:7])].append(math.log1p(int(r["views"])))
            except (KeyError, ValueError, TypeError):
                continue
        out[h] = {p: float(np.mean(v)) for p, v in acc.items() if len(v) >= 150}
    return out


def _net_change(series: dict[str, dict[str, float]], kind: str) -> dict[str, dict[str, float]]:
    """Outcome per brand and half-year as a change (attention: on the half before; revenue growth is already
    a change on a year before), net of the market's median for that half-year."""
    ch: dict[str, dict[str, float]] = defaultdict(dict)
    for h, s in series.items():
        for p, v in s.items():
            if kind == "attention":
                prev = [q for q in s if next_period(q) == p]
                if prev:
                    ch[h][p] = v - s[prev[0]]
            else:
                ch[h][p] = v
    by_p: dict[str, list] = defaultdict(list)
    for h, s in ch.items():
        for p, v in s.items():
            by_p[p].append(v)
    return {h: {p: v - float(np.median(by_p[p])) for p, v in s.items() if len(by_p[p]) >= 3} for h, s in ch.items()}


def associate(pairs: list[tuple[str, float, float, float | None]], rng) -> dict:
    """Spearman between a reading and the next half-year's outcome, both demeaned within brand, with the
    current outcome partialled out where known; the null permutes outcomes within brand."""
    if len(pairs) < 8:
        return {"pairs": len(pairs), "note": "too few brand-half-years with both"}
    hs = np.array([p[0] for p in pairs])
    x = np.array([p[1] for p in pairs], float)
    y = np.array([p[2] for p in pairs], float)
    z = np.array([np.nan if p[3] is None else p[3] for p in pairs], float)

    def demean(v):
        v = v.copy()
        for h in set(hs):
            m = hs == h
            v[m] = v[m] - np.nanmean(v[m])
        return v
    x, y = demean(x), demean(y)
    if np.isfinite(z).sum() >= 8:
        z = demean(np.where(np.isfinite(z), z, np.nanmean(z)))
        beta = float(np.dot(z, y) / np.dot(z, z)) if np.dot(z, z) else 0.0
        y = y - beta * z

    def rho(a, b):
        ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
        return float(np.corrcoef(ra, rb)[0, 1]) if np.std(ra) and np.std(rb) else 0.0
    obs = rho(x, y)
    idx = {h: np.where(hs == h)[0] for h in sorted(set(hs))}     # sorted: a set's order changes between runs
    null = []
    for _ in range(N_PERM):
        yp = y.copy()
        for ix in idx.values():
            yp[ix] = y[rng.permutation(ix)]
        null.append(rho(x, yp))
    null = np.array(null)
    return {"pairs": len(pairs), "brands": len(idx), "spearman": round(obs, 3),
            "p_two_sided": round((1 + int((np.abs(null) >= abs(obs) - 1e-12).sum())) / (1 + N_PERM), 4)}


def success(half_rows: dict[str, list[dict]], cross: dict, reg: registry.Registry, rng) -> dict:
    houses = [h.id for h in reg.houses]
    att = _net_change(attention_halves(houses), "attention")
    rev = _net_change({h: character.half_growth(h) for h in houses}, "revenue")
    out = {"outcomes": {"attention": {h: len(s) for h, s in att.items() if s}, "revenue": {h: len(s) for h, s in rev.items() if s}}}
    for name, outcome in (("attention", att), ("revenue", rev)):
        for side in ("like_for_like", "against_market"):
            for k in ("answers", "print"):
                pairs = []
                for h, rows in half_rows.items():
                    for s in rows:
                        t = s.get(side)
                        if not t:
                            continue
                        n = next_period(s["to"])
                        if n in outcome.get(h, {}) and f"{k}_score" in t:
                            pairs.append((h, t[f"{k}_score"], outcome[h][n], outcome.get(h, {}).get(s["to"])))
                out[f"{side}_shift_{k}_vs_next_{name}"] = associate(pairs, rng)
        pairs = []
        for y, rec in cross.get("years", {}).items():
            for h, d in rec["distinct"].items():
                for half in (f"{int(y) + 1}H1", f"{int(y) + 1}H2"):
                    if half in outcome.get(h, {}):
                        pairs.append((h, d["print"], outcome[h][half], outcome.get(h, {}).get(f"{y}H2")))
        out[f"distinct_vs_next_{name}"] = associate(pairs, rng)
    return out


# ---------- 7. the brand of the moment ----------

def attention_momentum(houses: list[str]) -> dict[str, dict[str, float]]:
    """How fast each brand's attention is rising: mean log daily views in a half-year against the same
    half-year a year before (so the season cancels), net of the market's median rise that half-year."""
    att = attention_halves(houses)
    ch: dict[str, dict[str, float]] = defaultdict(dict)
    for h, s in att.items():
        for p, v in s.items():
            prev = next((q for q in s if next_period(next_period(q)) == p), None)
            if prev is not None:
                ch[h][p] = v - s[prev]
    by_p: dict[str, list] = defaultdict(list)
    for s in ch.values():
        for p, v in s.items():
            by_p[p].append(v)
    return {h: {p: v - float(np.median(by_p[p])) for p, v in s.items() if len(by_p[p]) >= 5} for h, s in ch.items()}


def moment(images: list[dict], rng, momentum: dict[str, dict[str, float]] | None = None, min_pics: int = 4,
           n_perm: int = N_PERM) -> dict:
    """Do other brands' pictures move towards the brand of the moment? The brand of the moment in a half-year
    is the one whose attention rose most against a year before, net of the market. For every other brand
    with pictures in that half-year and the next, the change in its distance to the leader's pictures
    (negative: it moved closer), beside the same change towards a brand drawn at random from those present,
    which carries any general drift towards the middle. Campaign pictures and product on a model, once a
    half-year."""
    led = by_half([i for i in images if i["type"] in LED and i["vec"] is not None])
    cell: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for im in led:
        cell[im["period"]][im["house"]].append(im)
    houses = sorted({im["house"] for im in led})
    mom = momentum if momentum is not None else attention_momentum(houses)
    rows, draws = [], []
    for p in sorted(cell):
        q = next_period(p)
        if q not in cell:
            continue
        here = {h for h, ims in cell[p].items() if len(ims) >= min_pics}
        scored = {h: mom.get(h, {}).get(p) for h in here}
        scored = {h: v for h, v in scored.items() if v is not None}
        if len(scored) < 5:
            continue
        leader = max(scored, key=scored.get)
        movers = sorted(h for h in here if h != leader and len(cell[q].get(h, [])) >= min_pics)
        if len(movers) < 4:
            continue
        C = {h: _centroid(cell[p][h]) for h in here}
        N = {h: _centroid(cell[q][h]) for h in movers}
        toward = {h: _away(N[h], C[leader]) - _away(C[h], C[leader]) for h in movers}
        # the same change towards every other brand present, for the null
        others = {h: {g: _away(N[h], C[g]) - _away(C[h], C[g]) for g in here if g != h} for h in movers}
        rows.append({"from": p, "to": q, "leader": leader, "leader_momentum": round(scored[leader], 3),
                     "brands": len(movers), "mean_change": round(float(np.mean(list(toward.values()))), 4),
                     "mean_change_any_brand": round(float(np.mean([v for d in others.values() for v in d.values()])), 4),
                     "moved_closer": sum(v < 0 for v in toward.values())})
        draws.append((here, movers, others, leader))
    if not rows:
        return {"half_years": [], "note": "too few half-years with attention and pictures on both sides"}
    obs = float(np.mean([r["mean_change"] - r["mean_change_any_brand"] for r in rows]))
    null = []
    for _ in range(n_perm):
        vals = []
        for here, movers, others, leader in draws:
            fake = rng.choice(sorted(here))
            ch = [others[h][fake] for h in movers if h != fake]
            base = np.mean([v for d in others.values() for v in d.values()])
            if ch:
                vals.append(float(np.mean(ch)) - float(base))
        null.append(float(np.mean(vals)))
    null = np.array(null)
    return {"half_years": rows, "transitions": len(rows),
            "towards_leader_less_any_brand": round(obs, 4),
            "p_one_sided": round((1 + int((null <= obs + 1e-12).sum())) / (1 + n_perm), 4),
            "rule": "the leader is the brand whose attention rose most on a year before, net of the market; a "
                    "negative difference means brands moved towards the leader more than towards a brand drawn "
                    "at random; p is the share of random leaders with a difference as negative"}


# ---------- coverage and the run ----------

def coverage(images: list[dict]) -> dict:
    led = by_half([i for i in images if i["type"] in LED])
    by: dict[str, Counter] = defaultdict(Counter)
    for im in led:
        by[im["house"]][im["period"]] += 1
    periods = sorted({im["period"] for im in images})
    months = sorted({im["month"] for im in images})
    return {"periods": periods, "first_month": months[0] if months else None, "last_month": months[-1] if months else None,
            "brands": {h: [by[h].get(p, 0) for p in periods] for h in sorted(by)},
            "pictures_once_per_half_year": len(led)}


def run(images: list[dict], spec: dict, reg: registry.Registry, seed: int = 20261007, euclid: bool = False) -> dict:
    global EUCLID
    before, EUCLID = EUCLID, euclid
    try:
        return _run(images, spec, reg, seed)
    finally:
        EUCLID = before


def _run(images: list[dict], spec: dict, reg: registry.Registry, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    scr = screen(images, spec)
    ans = Answers(spec, scr["answers"])
    houses = by_house(images)
    half = shifts(houses, ans, rng, "half")
    year = shifts(houses, ans, rng, "year")
    cross = cross_brand(images, ans, reg, rng)
    events = designer_events() + owner_events()
    led = [i for i in images if i["type"] in LED]
    return {
        "generated_at": store.utc_now(), "source": "homepages",
        "status": "exploratory: descriptive readings, not in the pre-registration",
        "images": len(images), "image_led": len(led), "coverage": coverage(images),
        "screen": scr, "tracked": {"questions": ans.qs, "moods": ans.moods, "labels": [f"{q}={v}" for q, v in ans.labels]},
        "identity": character.identity(led, character.Encoder(spec), euclid=EUCLID),
        "half_years": half, "half_year_summary": shift_summary(half),
        "years": year, "year_summary": shift_summary(year),
        "by_lag": by_lag(houses, ans, rng), "power": power(houses, half, ans, rng),
        "cross_brand": cross, "spread": spread(images, rng), "market": market(images, ans, rng),
        "events": event_study(houses, events, ans, rng), "crew_turnover": crew_turnover(),
        "success": success(half, cross, reg, rng), "moment": moment(images, rng),
    }


def main(argv: list[str] | None = None) -> int:
    from .score import load_rubric
    argparse.ArgumentParser(prog="adtone.readings").parse_args(argv)
    spec = load_rubric().spec
    out = run(load_images(), spec, registry.load())
    path = config.RESULTS_DIR / "readings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    h, y = out["half_year_summary"], out["year_summary"]

    def line(s):
        return (f"{s['measured']} measured (chance about {s['expected_by_chance']} each): everything "
                f"{s['own_answers']['p05']} by the answers, {s['own_print']['p05']} by the fingerprint; like for like "
                f"{s['like_for_like_answers']['p05']} and {s['like_for_like_print']['p05']}; against the market "
                f"{s['against_market_answers']['p05']} and {s['against_market_print']['p05']}")
    print(f"readings: {out['images']} pictures; tracked {len(out['tracked']['questions'])} questions and "
          f"{len(out['tracked']['moods'])} moods. Half-years: {line(h)}. Years: {line(y)}. "
          f"Plants caught (answers, fingerprint): " +
          ", ".join(f"{k}: {v['caught_by_answers']}, {v['caught_by_print']}" for k, v in out["power"].items()) +
          f". Events tested {out['events']['summary']['tested']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
