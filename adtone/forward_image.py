"""Forward tests on the image: every change of creative lead whose first show falls after the template
was frozen, read on the houses' homepage pictures and their campaign credits.

    python -m adtone.forward            # runs every frozen design, this template included

The rules are in forward/designer-change-v1.md, frozen by hash. This file is frozen with them
(forward/designer-change-v1.code.sha256): it imports nothing from the rest of the package, so a later
change to the analysis code cannot change what a registered call computes. A change to either file is a
new version, never an edit.

Each change is one event: a house, the newly named creative lead, the date the appointment was announced
and D, the date of the first womenswear or co-ed show credited to them. Facts arrive in forward/events.yml
under `designer_changes`, each with a source. Nothing about a house is computed before its adjudication
date, and once a result is recorded it is never recomputed.

Four calls per event, each judged against houses without a change over the same months:

1. Magnitude. The house's homepage moves more, like for like, between the year before D and months 3 to
   14 after it, than 90% of houses without a change of creative lead.
2. Still itself. The house's later pictures pick out its own earlier pictures, among every house's, less
   often than they do at 90% of houses without a change.
3. The crew. None of the photographers credited on the house's fashion advertising in the 24 months before
   the announcement is credited on it in the 12 months from D.
4. Transfer, when the new lead comes straight from a house Focal reads. The house's pictures move towards
   that house's pictures of the year before the announcement, beyond chance, and more than towards any
   other house's.
"""
from __future__ import annotations

import csv
import hashlib
import itertools
import json
import math
import re
import unicodedata
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
INSTRUMENT = "tone-v1@qwen2.5-vl-7b-instruct@cc594898137f"   # the reader's answers that give each picture's kind
FINGERPRINT = "openclip-vitb32-laion2b"                       # the pinned fingerprint every comparison uses
KINDS = {"brand_image": "campaign", "product_on_model": "on_model"}
SCOPES = ("womenswear", "both")

MIN_PICTURES = 6        # pictures on each side of a comparison
MIN_MONTHS = 2          # and months, since months are what chance moves
MIN_CONTROLS = 5        # houses without a change, for calls 1 and 2
MIN_CANDIDATES = 8      # houses an earlier set can be matched to, for call 2
MIN_OTHERS = 5          # houses besides the origin, for call 4
N_SPLITS = 999          # ways of moving whole months between the two windows
N_DRAWS = 200           # draws of six pictures a side, for call 2
DRAW = 6
SEED = 20261014
PRE = (-12, -1)         # months relative to the month of D
POST = (3, 14)
CONTROL_MARGIN = 6      # a control has no change of creative lead from 6 months before PRE to 6 after POST
Q_MAGNITUDE = 0.9
Q_ITSELF = 0.1
ALPHA = 0.05
ORIGIN_MONTHS = 12      # the origin's pictures: the 12 months before the announcement
ORIGIN_LEFT_DAYS = 180  # the new lead left the origin no more than this before the announcement
CREW_BEFORE = 24        # months before the announcement
CREW_AFTER = 12         # months from the month of D
ADJUDICATE = 18         # months after the month of D: POST ends at 14, and captures arrive late
DEADLINE = 24           # insufficient data then is recorded as no result
VOID = 18               # months after the predecessor's departure, or the announcement, with no first show
UNJUDGED = 99.0


# ---------- months ----------

def month_of(d) -> str:
    return str(d)[:7]


def shift(month: str, n: int) -> str:
    k = int(month[:4]) * 12 + int(month[5:7]) - 1 + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def first_day(month: str) -> date:
    return date(int(month[:4]), int(month[5:7]), 1)


def _date(v) -> date | None:
    if v in (None, ""):
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


# ---------- the pictures ----------

def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n else v


def load_pictures(root: Path = ROOT) -> dict[str, list[dict]]:
    """Every homepage picture read as a campaign picture or as product on a model, with its pinned
    fingerprint: house -> [{month, sha, kind, vec}], one row per house, month and picture."""
    hp = root / "data" / "homepages"
    kind_of: dict[str, str] = {}
    with (hp / "obs" / "tone-v1.jsonl").open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            out = r.get("output") or {}
            if r.get("instrument") == INSTRUMENT and r.get("status") == "ok" and out.get("creative_type") in KINDS:
                kind_of[r["sha"]] = KINDS[out["creative_type"]]
    vecs: dict[str, np.ndarray] = {}
    for p in sorted((hp / "vectors" / FINGERPRINT).glob("*.npz")):
        with np.load(p, allow_pickle=False) as z:
            for sha, v in zip(z["shas"], z["vecs"]):
                vecs.setdefault(str(sha), _unit(v.astype(np.float64)))
    seen, out = set(), {}
    for p in sorted((hp / "captures").glob("*.jsonl")):
        with p.open(encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                if row.get("status") != "resolved":
                    continue
                for im in row.get("images") or []:
                    sha = im["sha"]
                    key = (row["house_id"], row["month"], sha)
                    if sha in kind_of and sha in vecs and key not in seen:
                        seen.add(key)
                        out.setdefault(row["house_id"], []).append(
                            {"month": row["month"], "sha": sha, "kind": kind_of[sha], "vec": vecs[sha]})
    return out


def window(pics: list[dict], lo: str, hi: str) -> list[dict]:
    """The pictures first shown from month lo to month hi, each once, at the month it was first shown."""
    seen, out = set(), []
    for p in sorted(pics, key=lambda p: (p["month"], p["sha"])):
        if lo <= p["month"] <= hi and p["sha"] not in seen:
            seen.add(p["sha"])
            out.append(p)
    return out


def enough(pics: list[dict]) -> bool:
    return len(pics) >= MIN_PICTURES and len({p["month"] for p in pics}) >= MIN_MONTHS


# ---------- moving whole months ----------

def _splits(g: int, k: int, n: int, rng) -> tuple[np.ndarray, bool]:
    """Ways to put k of g months in the first window: all of them when there are at most n, else n at random."""
    if math.comb(g, k) <= n:
        C = np.zeros((math.comb(g, k), g), bool)
        for r, c in enumerate(itertools.combinations(range(g), k)):
            C[r, list(c)] = True
        return C, True
    return rng.random((n, g)).argsort(1).argsort(1) < k, False


def _contrast(items: list[dict], kinds: list[str], w: np.ndarray, C: np.ndarray,
              months: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each split (rows of C, True for the first window): the change in the mean fingerprint, kind by kind,
    weighed by w and summed, and its variance with the pictures of one month taken together; and which
    splits leave every kind in both windows."""
    mi = {m: j for j, m in enumerate(months)}
    dim = len(items[0]["vec"])
    S = np.zeros((len(kinds), len(months), dim))
    N = np.zeros((len(kinds), len(months)))
    for it in items:
        t = kinds.index(it["kind"])
        S[t, mi[it["month"]]] += it["vec"]
        N[t, mi[it["month"]]] += 1
    out_d = np.zeros((len(C), dim))
    out_v = np.zeros((len(C), dim))
    ok = np.ones(len(C), bool)
    for F, sign in ((C.astype(float), -1.0), (1.0 - C.astype(float), 1.0)):
        n = F @ N.T
        ok &= (n > 0).all(1)
        n = np.where(n > 0, n, 1)
        m = [(F @ S[t]) / n[:, t:t + 1] for t in range(len(kinds))]
        g = F.sum(1)
        factor = np.where(g > 1, g / np.maximum(g - 1, 1), 1.0)[:, None]
        var = np.zeros_like(out_v)
        for t in range(len(kinds)):
            out_d += sign * w[t] * m[t]
            for u in range(len(kinds)):
                cross = (F @ (S[t] * S[u]) - m[u] * (F @ (N[u][:, None] * S[t])) - m[t] * (F @ (N[t][:, None] * S[u]))
                         + m[t] * m[u] * (F @ (N[t] * N[u]))[:, None])
                var += (w[t] / n[:, t:t + 1]) * (w[u] / n[:, u:u + 1]) * cross
        out_v += factor * np.maximum(var, 0.0)
    return out_d, out_v, ok


def _ratio(d: np.ndarray, V: np.ndarray) -> np.ndarray:
    num, den = (d ** 2).sum(1), V.sum(1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(den > 1e-15, num / np.where(den > 1e-15, den, 1), np.where(num > 1e-15, np.inf, 0.0))


def _p(null: np.ndarray, obs: float, exact: bool) -> float:
    ge = int((null >= obs - 1e-12).sum())
    return ge / len(null) if exact else (1 + ge) / (1 + len(null))


def _kinds(a: list[dict], b: list[dict]) -> list[str]:
    return [k for k in ("campaign", "on_model") if any(p["kind"] == k for p in a) and any(p["kind"] == k for p in b)]


# ---------- call 1: magnitude ----------

def movement(a: list[dict], b: list[dict], rng, n_splits: int = N_SPLITS) -> dict | None:
    """Like for like: the change in the mean fingerprint within each kind of picture, the kinds weighed by the
    house's own mix over both windows, scaled by its variance and set against the edge of chance (the 95th
    percentile of the same statistic when whole months are moved between the windows at random). Above 1 is
    beyond what chance gives one time in twenty. None when either side is too thin to judge."""
    kinds = _kinds(a, b)
    a2, b2 = [p for p in a if p["kind"] in kinds], [p for p in b if p["kind"] in kinds]
    if not (kinds and enough(a2) and enough(b2)):
        return None
    items = a2 + b2
    months = sorted({p["month"] for p in items})
    first = {p["month"] for p in a2}
    w = np.array([sum(p["kind"] == k for p in items) for k in kinds], float)
    w /= w.sum()
    C, exact = _splits(len(months), len(first), n_splits, rng)
    d_o, V_o, _ = _contrast(items, kinds, w, np.array([[m in first for m in months]]), months)
    d_n, V_n, ok = _contrast(items, kinds, w, C, months)
    null = _ratio(d_n[ok], V_n[ok])
    if len(null) < 20:
        return None
    obs = float(_ratio(d_o, V_o)[0])
    fin = null[np.isfinite(null)]
    edge = float(np.quantile(fin, 0.95)) if len(fin) else 0.0
    score = round(obs / edge, 4) if edge > 0 and np.isfinite(obs) else (UNJUDGED if obs > 0 else 1.0)
    return {"score": score, "p": round(_p(null, obs, exact), 4), "splits": int(ok.sum()), "exact": exact,
            "kinds": kinds, "n": [len(a2), len(b2)], "months": [len(first), len(months) - len(first)]}


# ---------- call 2: still itself ----------

def self_match(post: list[dict], pres: dict[str, list[dict]], house: str, rng, n_draws: int = N_DRAWS) -> float:
    """How often six of the house's later pictures sit closer to six of its own earlier pictures than to six
    of any other house's earlier pictures, every set drawn afresh each time."""
    names = sorted(pres)
    P = np.vstack([p["vec"] for p in post])
    M = {g: np.vstack([p["vec"] for p in pres[g]]) for g in names}
    hits = 0
    for _ in range(n_draws):
        q = _unit(P[rng.random(len(P)).argsort()[:DRAW]].mean(0))
        best, best_sim = None, -np.inf
        for g in names:
            c = _unit(M[g][rng.random(len(M[g])).argsort()[:DRAW]].mean(0))
            s = float(q @ c)
            if s > best_sim:
                best, best_sim = g, s
        hits += best == house
    return hits / n_draws


# ---------- call 4: transfer ----------

def _weighted_means(items: list[dict], kinds: list[str], w: np.ndarray, C: np.ndarray, months: list[str]):
    """Mix-weighted mean fingerprint of each window, for each split of the months."""
    mi = {m: j for j, m in enumerate(months)}
    dim = len(items[0]["vec"])
    S = np.zeros((len(kinds), len(months), dim))
    N = np.zeros((len(kinds), len(months)))
    for it in items:
        t = kinds.index(it["kind"])
        S[t, mi[it["month"]]] += it["vec"]
        N[t, mi[it["month"]]] += 1
    out = []
    for F in (C.astype(float), 1.0 - C.astype(float)):
        n = F @ N.T
        ok = (n > 0).all(1)
        n = np.where(n > 0, n, 1)
        out.append((sum(w[t] * (F @ S[t]) / n[:, t:t + 1] for t in range(len(kinds))), ok))
    (A, ok_a), (B, ok_b) = out
    return A, B, ok_a & ok_b


def _toward(A: np.ndarray, B: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """How much nearer the second window's mean sits to ref than the first window's, by cosine."""
    r = _unit(ref)
    na, nb = np.linalg.norm(A, axis=1), np.linalg.norm(B, axis=1)
    return (B @ r) / np.where(nb > 0, nb, 1) - (A @ r) / np.where(na > 0, na, 1)


def transfer(a: list[dict], b: list[dict], origin: list[dict], others: dict[str, list[dict]], rng,
             n_splits: int = N_SPLITS) -> dict | None:
    kinds = _kinds(a, b)
    a2, b2 = [p for p in a if p["kind"] in kinds], [p for p in b if p["kind"] in kinds]
    if not (kinds and enough(a2) and enough(b2) and enough(origin)):
        return None
    items = a2 + b2
    months = sorted({p["month"] for p in items})
    first = {p["month"] for p in a2}
    w = np.array([sum(p["kind"] == k for p in items) for k in kinds], float)
    w /= w.sum()
    o = np.vstack([p["vec"] for p in origin]).mean(0)
    A, B, _ = _weighted_means(items, kinds, w, np.array([[m in first for m in months]]), months)
    t_obs = float(_toward(A, B, o)[0])
    C, exact = _splits(len(months), len(first), n_splits, rng)
    An, Bn, ok = _weighted_means(items, kinds, w, C, months)
    null = _toward(An[ok], Bn[ok], o)
    p = _p(null, t_obs, exact)
    rival = {g: round(float(_toward(A, B, np.vstack([q["vec"] for q in pics]).mean(0))[0]), 4)
             for g, pics in sorted(others.items())}
    strongest = max(rival.values()) if rival else None
    hit = bool(t_obs > 0 and p <= ALPHA and strongest is not None and t_obs > strongest)
    return {"toward_origin": round(t_obs, 4), "p": round(p, 4), "splits": int(ok.sum()), "exact": exact,
            "toward_others": rival, "strongest_other": strongest, "hit": hit}


# ---------- call 3: the crew ----------

def norm_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s.replace("&", " and "))
    return " ".join(s.split())


def people(credit: str) -> set[str]:
    """The people in one credit: a duo credited together counts as each of its members, so one of them
    staying counts as a photographer staying."""
    return {p.strip() for p in norm_name(credit).split(" and ") if p.strip()}


def load_credits(root: Path = ROOT) -> list[dict]:
    with (root / "reference" / "credits.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def photographers(rows: list[dict], house: str, lo: str, hi: str) -> set[str]:
    """Photographers credited on the house's fashion advertising published from month lo to month hi.
    Rows dated to the year only are left out: they cannot be placed in a window of months."""
    out: set[str] = set()
    for r in rows:
        if (r["house"] == house and r["scope"] == "fashion" and r["work_type"] == "advertising"
                and r["role"] == "photographer" and len(r["published"]) >= 7 and lo <= r["published"][:7] <= hi):
            out |= people(r["person"])
    return out


def crew(rows: list[dict], house: str, announced: date, d: date, before: list[str] | None = None) -> dict:
    """before, when the event's record gives it, is the list of photographers recorded before D from the
    credits of the 24 months before the announcement; otherwise it is read from the credits."""
    a = month_of(announced)
    if before is not None:
        b0 = set().union(*(people(x) for x in before)) if before else set()
    else:
        b0 = photographers(rows, house, shift(a, -CREW_BEFORE), shift(a, -1))
    after = photographers(rows, house, month_of(d), shift(month_of(d), CREW_AFTER - 1))
    if not b0 or not after:
        return {"status": "insufficient", "before": len(b0), "after": len(after)}
    stayed = sorted(b0 & after)
    return {"before": len(b0), "after": len(after), "stayed": len(stayed), "hit": not stayed}


# ---------- which houses count as controls ----------

def lead_months(root: Path = ROOT, events: list[dict] | None = None) -> dict[str, list[str]]:
    """Every dated move of a creative lead, womenswear or co-ed: appointments announced, first shows and
    departures announced, from reference/designers.csv and the facts recorded for this template."""
    out: dict[str, list[str]] = {}
    with (root / "reference" / "designers.csv").open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["scope"] in SCOPES:
                for k in ("announced_date", "first_show_date", "left_announced_date"):
                    if len(r[k]) >= 7:
                        out.setdefault(r["house_id"], []).append(r[k][:7])
    for e in events or []:
        for k in ("predecessor_left_announced", "announced", "first_show"):
            if e.get(k):
                out.setdefault(e["house"], []).append(str(e[k])[:7])
    return out


def controls(pictures: dict[str, list[dict]], house: str, pre: tuple[str, str], post: tuple[str, str],
             moves: dict[str, list[str]], exclude: set[str]) -> list[str]:
    lo, hi = shift(pre[0], -CONTROL_MARGIN), shift(post[1], CONTROL_MARGIN)
    return sorted(h for h in pictures
                  if h != house and h not in exclude and not any(lo <= m <= hi for m in moves.get(h, [])))


# ---------- one event ----------

def windows_for(d: date) -> tuple[tuple[str, str], tuple[str, str]]:
    m = month_of(d)
    return (shift(m, PRE[0]), shift(m, PRE[1])), (shift(m, POST[0]), shift(m, POST[1]))


def compute(event: dict, pictures: dict[str, list[dict]], credit_rows: list[dict], moves: dict[str, list[str]],
            seed: int = SEED, n_splits: int = N_SPLITS, n_draws: int = N_DRAWS) -> dict:
    """The four calls for one event. Called only on or after its adjudication date, or on events whose
    windows closed before the template was frozen (the base rates)."""
    rng = np.random.default_rng(seed)
    house, d = event["house"], _date(event["first_show"])
    announced = _date(event.get("announced")) or d
    pre, post = windows_for(d)
    origin = event.get("previous_house") or None
    ctrl = controls(pictures, house, pre, post, moves, {origin} if origin else set())
    side = lambda h: (window(pictures.get(h, []), *pre), window(pictures.get(h, []), *post))
    a, b = side(house)
    out = {"house": house, "first_show": d.isoformat(), "pre": list(pre), "post": list(post), "controls": ctrl}

    # 1. magnitude
    own = movement(a, b, rng, n_splits)
    scores = {}
    for h in ctrl:
        ca, cb = side(h)
        r = movement(ca, cb, rng, n_splits)
        if r and r["score"] != UNJUDGED:
            scores[h] = r["score"]
    if own is None or own["score"] == UNJUDGED:
        out["magnitude"] = {"status": "insufficient", "reason": "too few pictures on one side"}
    elif len(scores) < MIN_CONTROLS:
        out["magnitude"] = {"status": "insufficient", "reason": f"{len(scores)} controls with pictures on both sides"}
    else:
        q = float(np.quantile(list(scores.values()), Q_MAGNITUDE))
        out["magnitude"] = {**own, "controls": scores, "q90": round(q, 4),
                            "share_at_or_above": round(float(np.mean([s >= own["score"] for s in scores.values()])), 3),
                            "hit": bool(own["score"] > q)}

    # 2. still itself
    pres = {h: window(pics, *pre) for h, pics in pictures.items()}
    pres = {h: p for h, p in pres.items() if len(p) >= DRAW}
    posts = {h: window(pictures.get(h, []), *post) for h in [house] + ctrl}
    if house not in pres or len(posts[house]) < DRAW:
        out["itself"] = {"status": "insufficient", "reason": "too few pictures on one side"}
    elif len(pres) < MIN_CANDIDATES:
        out["itself"] = {"status": "insufficient", "reason": f"{len(pres)} houses with pictures before"}
    else:
        own_r = self_match(posts[house], pres, house, rng, n_draws)
        rates = {h: self_match(posts[h], pres, h, rng, n_draws) for h in ctrl if h in pres and len(posts[h]) >= DRAW}
        if len(rates) < MIN_CONTROLS:
            out["itself"] = {"status": "insufficient", "reason": f"{len(rates)} controls with pictures on both sides"}
        else:
            q = float(np.quantile(list(rates.values()), Q_ITSELF))
            out["itself"] = {"rate": round(own_r, 3), "chance": round(1 / len(pres), 3), "candidates": len(pres),
                             "controls": {h: round(r, 3) for h, r in rates.items()}, "q10": round(q, 3),
                             "hit": bool(own_r < q)}

    # 3. the crew
    out["crew"] = crew(credit_rows, house, announced, d, event.get("crew_before"))

    # 4. transfer
    left = _date(event.get("previous_left_announced"))
    if not origin:
        out["transfer"] = {"status": "not_applicable", "reason": "the new lead does not come from a house Focal reads"}
    elif origin == house:
        out["transfer"] = {"status": "not_applicable", "reason": "a promotion from inside the house"}
    elif left is None or (announced - left).days > ORIGIN_LEFT_DAYS:
        out["transfer"] = {"status": "not_applicable",
                           "reason": f"left {origin} more than {ORIGIN_LEFT_DAYS} days before the announcement, or the date is not known"}
    else:
        am = month_of(announced)
        lo, hi = shift(am, -ORIGIN_MONTHS), shift(am, -1)
        o = window(pictures.get(origin, []), lo, hi)
        others = {h: window(pics, lo, hi) for h, pics in pictures.items() if h not in (house, origin)}
        others = {h: p for h, p in others.items() if enough(p)}
        if not enough(o):
            out["transfer"] = {"status": "insufficient", "reason": f"{len(o)} pictures of {origin} in the year before the announcement"}
        elif len(others) < MIN_OTHERS:
            out["transfer"] = {"status": "insufficient", "reason": f"{len(others)} other houses with pictures then"}
        else:
            r = transfer(a, b, o, others, rng, n_splits)
            out["transfer"] = {"origin": origin, **r} if r else {"status": "insufficient", "reason": "too few pictures on one side"}
    return out


CALLS = ("magnitude", "itself", "crew", "transfer")


def evaluate(spec: dict, event: dict, today: date, load=None) -> dict:
    """Where one event stands. load() returns (pictures, credit rows, moves) and is called only once the
    adjudication date has come."""
    d = _date(event.get("first_show"))
    if d is None:
        known = [x for x in (_date(event.get("announced")), _date(event.get("predecessor_left_announced"))) if x]
        if known and today >= first_day(shift(month_of(max(known)), spec["void_months"])):
            return {"status": "void", "reason": f"no first show by a newly named creative lead within {spec['void_months']} months"}
        return {"status": "awaiting_event"}
    if d <= date.fromisoformat(spec["first_show_after"]):
        return {"status": "not_covered", "reason": f"first show on or before {spec['first_show_after']}, when the template was frozen"}
    adjudicate_on = first_day(shift(month_of(d), spec["adjudicate_months"]))
    deadline = first_day(shift(month_of(d), spec["deadline_months"]))
    base = {"first_show": d.isoformat(), "adjudicate_on": adjudicate_on.isoformat()}
    if today < adjudicate_on:
        return {"status": "awaiting_adjudication", **base}
    pictures, rows, moves = load()
    res = compute(event, pictures, rows, moves)
    open_ = [c for c in CALLS if res[c].get("status") == "insufficient"]
    if open_ and today < deadline:
        return {"status": "awaiting_data", "waiting_on": open_, "deadline": deadline.isoformat(), **base}
    return {"status": "adjudicated", **base, **res}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
