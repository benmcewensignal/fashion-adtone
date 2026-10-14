"""Measures from the catalogue (reference/measures.yml) computed as series, for the reports and any feed.

    python -m adtone.measures        # writes data/results/measures.json

recognition: "still itself". Six of a house's pictures from a later year set against six from an earlier year
of every house with enough pictures in both, each set drawn afresh, 200 times; the share of draws in which the
nearest earlier set, by the cosine between mean fingerprints, is the house's own. A picture counts once, in the
year it was first shown, and a later picture that is a crop of an earlier one (cosine at least 0.95) is left
out of the later year, so a campaign left up does not recognise itself. Chance is one over the number of houses
in the pair of years. Reported for every house and pair of years, and summed by the gap in years.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict

import numpy as np

from . import config, readings, store

K = 6              # pictures a side
DRAWS = 200
MIN_HOUSES = 6     # houses a pair of years needs
SEED = 29


def _unit(M: np.ndarray) -> np.ndarray:
    return M / np.linalg.norm(M, axis=-1, keepdims=True)


def by_year(images: list[dict]) -> dict[str, dict[str, list[dict]]]:
    """Each picture once, in the year it was first shown: year -> house -> pictures."""
    first: dict[tuple, dict] = {}
    for im in images:
        if im["type"] not in readings.LED or im["vec"] is None:
            continue
        k = (im["house"], im["sha"])
        if k not in first or im["month"] < first[k]["month"]:
            first[k] = im
    yr: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for im in first.values():
        yr[im["month"][:4]][im["house"]].append(im)
    return yr


def recognition(images: list[dict], years: list[str] | None = None, k: int = K, draws: int = DRAWS,
                seed: int = SEED) -> dict:
    yr = by_year(images)
    ys = sorted(years if years is not None else yr)
    rng = np.random.default_rng(seed)
    pairs, per_house = [], defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for a in ys:
        for b in ys:
            if b <= a:
                continue
            cand = {}
            for h in set(yr[a]) & set(yr[b]):
                A = np.vstack([i["vec"] for i in yr[a][h]])
                later = [i for i in yr[b][h] if float((A @ i["vec"]).max()) < readings.DUP_COS]
                if len(yr[a][h]) >= k and len(later) >= k:
                    cand[h] = (A, np.vstack([i["vec"] for i in later]))
            hh = sorted(cand)
            if len(hh) < MIN_HOUSES:
                continue
            hits = np.zeros(len(hh))
            for _ in range(draws):
                ca = _unit(np.vstack([cand[h][0][rng.choice(len(cand[h][0]), k, replace=False)].mean(0) for h in hh]))
                cb = _unit(np.vstack([cand[h][1][rng.choice(len(cand[h][1]), k, replace=False)].mean(0) for h in hh]))
                hits += np.argmax(ca @ cb.T, axis=1) == np.arange(len(hh))
            gap = int(b) - int(a)
            pairs.append({"earlier": a, "later": b, "gap": gap, "houses": len(hh),
                          "rates": {h: round(float(hits[i] / draws), 3) for i, h in enumerate(hh)}})
            for i, h in enumerate(hh):
                per_house[h][gap][0] += int(hits[i])
                per_house[h][gap][1] += draws

    def summary(lo: int, hi: int | None) -> dict:
        ps = [p for p in pairs if p["gap"] >= lo and (hi is None or p["gap"] <= hi)]
        n = sum(p["houses"] for p in ps)
        if not n:
            return {}
        houses = {}
        for h, gaps in per_house.items():
            num = sum(x[0] for g, x in gaps.items() if g >= lo and (hi is None or g <= hi))
            den = sum(x[1] for g, x in gaps.items() if g >= lo and (hi is None or g <= hi))
            if den:
                houses[h] = {"per_100": round(100 * num / den, 1), "pairs_of_years": den // draws}
        return {"typical_per_100": round(100 * sum(sum(p["rates"].values()) for p in ps) / n, 1),
                "chance_per_100": round(100 * len(ps) / n, 1), "pairs_of_years": len(ps), "houses": houses}

    return {"k": k, "draws": draws, "seed": seed, "years": ys, "pairs": pairs,
            "one_or_two_years": summary(1, 2), "five_years_or_more": summary(5, None)}


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(prog="adtone.measures").parse_args(argv)
    rec = recognition(readings.load_images())
    out = {"generated_at": store.utc_now(), "catalogue": "reference/measures.yml", "recognition": rec}
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.RESULTS_DIR / "measures.json").write_text(json.dumps(out, indent=1) + "\n")
    f = rec["five_years_or_more"]
    print(f"recognition at five years or more: typical {f.get('typical_per_100')} in 100, chance {f.get('chance_per_100')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
