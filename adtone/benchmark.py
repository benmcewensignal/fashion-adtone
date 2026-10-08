"""The season benchmark: each brand's collection followed through the stages and set against the other
brands of the same season, like for like.

    python -m adtone.benchmark        # after `python -m adtone.thread build`
                                      # -> data/results/benchmark.json and data/results/benchmark.csv

For every main collection in data/thread/collections.jsonl:

  the show          the jump in attention against the season, whether it lasted (30 to 120 days after, net
                    of the median brand), the momentum the brand brought in, press volume and tone;
  the scene         ambassador appointments in the 180 days before;
  the shop window   the homepage pictures first shown after the show and before the brand's next main show
                    (six months at most), so that one season's window never overlaps the next. Pictures are
                    dated by month: a month belongs to the window if its fifteenth day does. Each measure is
                    like for like, campaign pictures with campaign pictures and product with product, and is
                    also given as a percentile among the brands of the same season:
      distinctness  one less the cosine between the brand's centroid and the mean of the other brands'
                    centroids for the same months, by the fingerprint (OpenCLIP ViT-B-32);
      movement      one less the cosine between the brand's centroid and its own in the previous window;
      mix           the shares of campaign pictures, product and everything else, and how far they moved
                    from the previous window (total variation distance);
  the campaigns     what the brand published for the season, as the Thread joins it;
  the advertising   none until Meta's archive opens;
  transmission      how much of the runway's clothes reach each later stage. It waits on the description of
                    the clothes; `transmission` below is the measure, tested on made-up profiles.

Then, across collections, which shop-window measures go with attention that lasts: within brands, with
momentum held constant, with bootstrap intervals. Descriptive throughout.

Picture kind is the larger reader's (Qwen3-VL-32B) where it has read the picture and the pinned smaller
reader's otherwise. The two agree on campaign picture or not at kappa 0.73; they differ mostly between
product on a model and packshot, which this groups together as product.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from . import config, store

THREAD_DIR = config.DATA / "thread"
HOMEPAGES = config.DATA / "homepages"
VECTORS = HOMEPAGES / "vectors" / "openclip-vitb32-laion2b"
LARGER_READER = config.DATA / "luxury" / "readings" / "qwen3-tone.jsonl"
RESULTS = config.RESULTS_DIR / "benchmark.json"
TABLE = config.RESULTS_DIR / "benchmark.csv"

SHOP_DAYS = 182          # the shop window runs at most six months after the show
MIN_KIND = 3             # pictures of a kind, in a window, before a brand gets a value for that kind
MIN_PEERS = 3            # other brands with that kind in the same months, before distinctness is given
MIN_MIX = 3              # pictures in a window before its mix is given
GROUPS = ("image", "product")
GROUP_OF = {"brand_image": "image", "product_on_model": "product", "product_packshot": "product",
            "catalogue_grid": "product"}      # promotional, events, text and other: "other"
BOOT = 2000


# ---------- the pictures ----------

def kind_group(creative_type: str | None) -> str:
    return GROUP_OF.get(creative_type or "", "other")


def load_pictures(obs_path: Path | None = None, larger: Path | None = None, vectors: Path | None = None) -> list[dict]:
    """Every homepage picture read: brand, the month it was first shown, its kind group and fingerprint."""
    obs_path = obs_path or HOMEPAGES / "obs" / "tone-v1.jsonl"
    larger = larger or LARGER_READER
    vectors = vectors or VECTORS
    vec = {}
    for f in sorted(Path(vectors).glob("*.npz")):
        z = np.load(f, allow_pickle=True)
        for s, v in zip(z["shas"], z["vecs"]):
            n = float(np.linalg.norm(v))
            if n:
                vec[str(s)] = np.asarray(v, float) / n
    big = {}
    if Path(larger).exists():
        for r in store.read_jsonl(Path(larger)):
            big[r.get("sha")] = (r.get("out") or {}).get("creative_type")
    out = []
    for r in store.read_jsonl(Path(obs_path)):
        if r.get("status") != "ok" or r.get("sha") not in vec:
            continue
        ct = big.get(r["sha"]) or (r.get("output") or {}).get("creative_type")
        out.append({"sha": r["sha"], "house": r["house_id"], "month": r["month"], "kind": ct,
                    "group": kind_group(ct), "vec": vec[r["sha"]]})
    return out


def window_months(show: date, end: date) -> list[str]:
    """The months whose fifteenth day falls after the show and on or before the window's end."""
    months, y, m = [], show.year, show.month
    while True:
        mid = date(y, m, 15)
        if mid > end:
            break
        if mid > show:
            months.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


def shop_window_end(show: date, next_main: date | None) -> date:
    end = show + timedelta(days=SHOP_DAYS)
    return min(end, next_main - timedelta(days=1)) if next_main and next_main > show else end


# ---------- the measures ----------

def _centroid(vs: list[np.ndarray]) -> np.ndarray:
    c = np.mean(vs, axis=0)
    n = float(np.linalg.norm(c))
    return c / n if n else c


def distinctness(brand: list[dict], peers: dict[str, list[dict]], min_kind: int = MIN_KIND,
                 min_peers: int = MIN_PEERS) -> dict:
    """Like for like: for each kind, one less the cosine between the brand's centroid and the mean of the
    other brands' centroids of the same kind; then the kinds averaged, weighted by the brand's pictures."""
    out = {}
    for g in GROUPS:
        mine = [p["vec"] for p in brand if p["group"] == g]
        others = [_centroid([p["vec"] for p in ps if p["group"] == g]) for ps in peers.values()
                  if sum(p["group"] == g for p in ps) >= min_kind]
        if len(mine) >= min_kind and len(others) >= min_peers:
            out[g] = {"value": round(1 - float(_centroid(mine) @ _centroid(others)), 4), "n": len(mine),
                      "peers": len(others)}
    if out:
        w = np.array([v["n"] for v in out.values()], float)
        out["value"] = round(float(np.average([v["value"] for v in out.values()], weights=w)), 4)
    return out


def movement(now: list[dict], before: list[dict], min_kind: int = MIN_KIND) -> dict:
    """Like for like: for each kind, one less the cosine between this window's centroid and the previous one's."""
    out = {}
    for g in GROUPS:
        a = [p["vec"] for p in now if p["group"] == g]
        b = [p["vec"] for p in before if p["group"] == g]
        if len(a) >= min_kind and len(b) >= min_kind:
            out[g] = {"value": round(1 - float(_centroid(a) @ _centroid(b)), 4), "n": min(len(a), len(b))}
    if out:
        w = np.array([v["n"] for v in out.values() if isinstance(v, dict)], float)
        out["value"] = round(float(np.average([v["value"] for v in out.values() if isinstance(v, dict)], weights=w)), 4)
    return out


def mix(pictures: list[dict]) -> dict | None:
    if len(pictures) < MIN_MIX:
        return None
    c = Counter(p["group"] for p in pictures)
    return {g: round(c[g] / len(pictures), 3) for g in ("image", "product", "other")}


def tvd(a: dict, b: dict) -> float:
    return round(0.5 * sum(abs(a.get(k, 0) - b.get(k, 0)) for k in set(a) | set(b)), 3)


def profile(answers: list[dict], questions: list[str]) -> dict[str, Counter]:
    """The answers given to each question across a stage's pictures, a list answer counting each item."""
    out = {q: Counter() for q in questions}
    for a in answers:
        for q in questions:
            v = a.get(q)
            for x in (v if isinstance(v, list) else [v]):
                if x is not None:
                    out[q][str(x)] += 1
    return out


def transmission(runway: dict[str, Counter], stage: dict[str, Counter]) -> float | None:
    """How much of the runway's clothes reach a later stage: one less the mean, over the questions both
    stages answer, of the total variation distance between the shares of each answer. One means the stage
    shows the clothes in the runway's proportions; nought means nothing in common."""
    d = []
    for q, a in runway.items():
        b = stage.get(q) or Counter()
        na, nb = sum(a.values()), sum(b.values())
        if na and nb:
            d.append(0.5 * sum(abs(a[k] / na - b[k] / nb) for k in set(a) | set(b)))
    return round(1 - float(np.mean(d)), 4) if d else None


def percentiles(values: dict[str, float]) -> dict[str, int]:
    """Each brand's place among the season's brands, 0 the least and 100 the most."""
    keys = sorted(values, key=lambda k: values[k])
    if len(keys) < 2:
        return {k: 50 for k in keys}
    return {k: round(100 * i / (len(keys) - 1)) for i, k in enumerate(keys)}


# ---------- the benchmark ----------

def season_order(s: str) -> tuple:
    """Spring-summer of a year is shown the autumn before its autumn-winter: 2026 SS, then 2026 AW."""
    p = s.split()
    return (int(p[0]), 0 if p[1] == "SS" else 1) if len(p) >= 2 and p[0].isdigit() else (0, 0)


def season_of(c: dict) -> str:
    """Year and half, across categories: a brand that shows menswear joins the season it shows in."""
    p = c["season"].split()
    return f"{p[0]} {p[1]}" if len(p) >= 2 else c["season"]


def build(collections: list[dict], pictures: list[dict], last_month: str | None = None) -> dict:
    mains = sorted((c for c in collections if c.get("main")), key=lambda c: (c["house"], c["date"]))
    by_house_month = defaultdict(lambda: defaultdict(list))
    for p in pictures:
        by_house_month[p["house"]][p["month"]].append(p)
    last_month = last_month or max((p["month"] for p in pictures), default=None)
    by_house = defaultdict(list)
    for c in mains:
        by_house[c["house"]].append(c)

    rows = []
    for h, cs in by_house.items():
        prev_pics, prev_mix, prev_date = None, None, None
        for i, c in enumerate(cs):
            d = date.fromisoformat(c["date"])
            nxt = date.fromisoformat(cs[i + 1]["date"]) if i + 1 < len(cs) else None
            end = shop_window_end(d, nxt)
            months = window_months(d, end)
            complete = bool(months) and last_month is not None and months[-1] <= last_month and (
                nxt is not None or end <= date.fromisoformat(f"{last_month}-28"))
            mine = [p for m in months for p in by_house_month[h].get(m, [])]
            peers = {o: [p for m in months for p in pm.get(m, [])] for o, pm in by_house_month.items() if o != h}
            peers = {o: ps for o, ps in peers.items() if ps}
            mx = mix(mine)
            row = {"id": c["id"], "house": h, "season": season_of(c), "date": c["date"], "category": c["category"],
                   "show": {k: c["show"].get(k) for k in ("heat", "heat_z", "surprise", "lasting", "lasting_z",
                                                           "momentum", "press", "press_z", "tone", "tone_z")},
                   "scene": c.get("scene"),
                   "shop_window": {"months": months, "complete": complete, "pictures": len(mine),
                                   "by_kind": dict(Counter(p["group"] for p in mine)), "mix": mx,
                                   "mix_shift": tvd(mx, prev_mix) if mx and prev_mix else None,
                                   "distinctness": distinctness(mine, peers),
                                   "movement": movement(mine, prev_pics) if prev_pics else {},
                                   "previous": prev_date},
                   "campaigns": {"entries": c.get("campaign", {}).get("entries", 0),
                                 "by_line": c.get("campaign", {}).get("by_line", {})},
                   "advertising": {"status": "waiting on Meta's archive",
                                   "show_period": c.get("advertising", {}).get("show_period", 0),
                                   "campaign_period": c.get("advertising", {}).get("campaign_period", 0)},
                   "transmission": {"status": "waiting on the description of the clothes"}}
            rows.append(row)
            if mine:
                prev_pics, prev_mix, prev_date = mine, mx, c["date"]

    # places within each season
    seasons = defaultdict(list)
    for r in rows:
        seasons[r["season"]].append(r)
    for rs in seasons.values():
        for name in ("distinctness", "movement"):
            # each kind is placed among the brands showing that kind, and a brand's place is the average of its
            # kinds' places weighted by its pictures, so campaign pictures are only ever ranked against campaign
            # pictures and product against product
            for g in GROUPS:
                pct = percentiles({r["id"]: r["shop_window"][name][g]["value"] for r in rs if g in r["shop_window"][name]})
                for r in rs:
                    if r["id"] in pct:
                        r["shop_window"][name][g]["percentile"] = pct[r["id"]]
            for r in rs:
                m = r["shop_window"][name]
                parts = [(m[g]["percentile"], m[g]["n"]) for g in GROUPS if g in m]
                if parts:
                    m["percentile"] = round(sum(p * n for p, n in parts) / sum(n for _, n in parts))
        for strand in ("heat", "lasting"):
            pct = percentiles({r["id"]: r["show"][strand] for r in rs if r["show"].get(strand) is not None})
            for r in rs:
                r["show"][f"{strand}_percentile"] = pct.get(r["id"])

    summary = {}
    for s, rs in sorted(seasons.items(), key=lambda kv: season_order(kv[0])):
        summary[s] = {"brands": len(rs),
                      "show": sum(r["show"]["heat"] is not None for r in rs),
                      "lasting": sum(r["show"]["lasting"] is not None for r in rs),
                      "shop_window": sum(bool(r["shop_window"]["pictures"]) for r in rs),
                      "shop_window_complete": sum(r["shop_window"]["complete"] for r in rs),
                      "distinctness": sum("value" in r["shop_window"]["distinctness"] for r in rs),
                      "movement": sum("value" in r["shop_window"]["movement"] for r in rs),
                      "campaigns": sum(bool(r["campaigns"]["entries"]) for r in rs),
                      "scene": sum(bool((r["scene"] or {}).get("covered")) for r in rs)}
    return {"generated_at": store.utc_now(), "status": "descriptive",
            "windows": {"shop_window": f"months after the show, to the day before the next main show, {SHOP_DAYS} days at most",
                        "lasting": "30 to 120 days after the show, net of the median brand"},
            "pictures": len(pictures), "last_month": last_month, "seasons": summary,
            "association": association(rows), "collections": rows}


def association(rows: list[dict], boot: int = BOOT, seed: int = 43) -> dict:
    """Which shop-window measures go with attention that lasts: lasting attention on the season places of
    distinctness and movement, the share of campaign pictures and momentum, within brands (each brand's own
    averages removed), standardised, with intervals from resampling collections. Descriptive."""
    names = ["distinctness", "movement", "image_share", "momentum"]
    data = []
    for r in rows:
        sw, sh = r["shop_window"], r["show"]
        x = [sw["distinctness"].get("percentile"), sw["movement"].get("percentile"),
             (sw["mix"] or {}).get("image"), sh.get("momentum")]
        if sh.get("lasting") is None or any(v is None for v in x):
            continue
        data.append((r["house"], sh["lasting"], x))
    by = defaultdict(list)
    for h, y, x in data:
        by[h].append((y, x))
    X, Y = [], []
    for h, items in by.items():
        if len(items) < 2:
            continue
        ys = np.array([y for y, _ in items]); xs = np.array([x for _, x in items], float)
        Y += list(ys - ys.mean()); X += list(xs - xs.mean(axis=0))
    out = {"n": len(Y), "brands": sum(1 for v in by.values() if len(v) >= 2), "terms": names}
    if len(Y) < 30:
        return {**out, "status": "insufficient: fewer than 30 collections with every term in brands with two"}
    X, Y = np.array(X), np.array(Y)
    sd = X.std(axis=0)
    keep = sd > 0
    Xs = X[:, keep] / sd[keep]
    beta = np.linalg.lstsq(Xs, Y, rcond=None)[0]
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(boot):
        k = rng.integers(0, len(Y), len(Y))
        draws.append(np.linalg.lstsq(Xs[k], Y[k], rcond=None)[0])
    lo, hi = np.quantile(np.array(draws), [0.05, 0.95], axis=0)
    terms = [n for n, k in zip(names, keep) if k]
    return {**out, "status": "ok",
            "coefficients": {n: {"estimate": round(float(b), 4), "interval_90": [round(float(a), 4), round(float(c), 4)]}
                             for n, b, a, c in zip(terms, beta, lo, hi)},
            "reading": "lasting attention in log views per standard deviation of each term, within brands"}


def write_table(rows: list[dict], path: Path = TABLE) -> None:
    fields = ["house", "season", "date", "category", "heat", "heat_z", "lasting", "lasting_z", "momentum", "press",
              "tone", "appointments", "pictures", "complete", "image", "product", "other", "mix_shift",
              "distinctness", "distinctness_percentile", "movement", "movement_percentile", "campaigns"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            sw, sh = r["shop_window"], r["show"]
            m = sw["mix"] or {}
            w.writerow({"house": r["house"], "season": r["season"], "date": r["date"], "category": r["category"],
                        "heat": sh.get("heat"), "heat_z": sh.get("heat_z"), "lasting": sh.get("lasting"),
                        "lasting_z": sh.get("lasting_z"), "momentum": sh.get("momentum"), "press": sh.get("press"),
                        "tone": sh.get("tone"), "appointments": (r["scene"] or {}).get("appointments"),
                        "pictures": sw["pictures"], "complete": sw["complete"], "image": m.get("image"),
                        "product": m.get("product"), "other": m.get("other"), "mix_shift": sw["mix_shift"],
                        "distinctness": sw["distinctness"].get("value"),
                        "distinctness_percentile": sw["distinctness"].get("percentile"),
                        "movement": sw["movement"].get("value"), "movement_percentile": sw["movement"].get("percentile"),
                        "campaigns": r["campaigns"]["entries"]})


def main(argv: list[str] | None = None) -> int:
    collections = store.read_jsonl(THREAD_DIR / "collections.jsonl")
    if not collections:
        print("no collections on file: run `python -m adtone.thread build` first")
        return 0
    out = build(collections, load_pictures())
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(out, indent=1, default=float) + "\n", encoding="utf-8")
    write_table(out["collections"])
    latest = [s for s, v in out["seasons"].items() if v["shop_window_complete"] >= 10]
    print(json.dumps({"collections": len(out["collections"]), "pictures": out["pictures"],
                      "latest full season": latest[-1] if latest else None,
                      "association": {k: out["association"].get(k) for k in ("status", "n", "brands")}}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
