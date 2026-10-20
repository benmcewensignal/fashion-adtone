"""Splice the back catalogue onto the live series.

    python -m adtone.calibrate

A campaign photographed once can reach both a house's own site and its paid Meta ads, cropped
and formatted differently. In the overlap year those reappearances are matched pairs: the same
image seen through two channels. Their average difference in embedding space is the channel's
offset, measured directly rather than modelled (the matched-pair method from the photographer
work). A source is spliced only with enough pairs; otherwise it stays descriptive.

The live side is every paid social picture with a fingerprint, from the libraries' collectors
(adtone/adlibs.py: Meta's EU archive through the vendor, Snapchat's, Pinterest's) and the first Meta
collector's files where they hold any. The house's site comes from two places, reported apart: the
campaign pages the back catalogue names (adtone/backcat.py), which brought no pictures while their
links sat behind the listing site's login, and the homepages as the archive holds them
(adtone/homepages.py), each picture dated by the first capture that shows it. The homepages are the
house's own site too, and their first months in the overlap year meet the ads.
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta

import numpy as np

from . import config, store
from .media import hamming

MIN_PAIRS = 20
MAX_DAYS = 90
COS_MIN = 0.92
PHASH_MAX = 10


def _unit(v):
    return v / (np.linalg.norm(v) or 1.0)


def match(meta: list[dict], back: list[dict], max_days: int = MAX_DAYS, cos_min: float = COS_MIN,
          phash_max: int = PHASH_MAX) -> list[tuple[dict, dict, float]]:
    """Items: {house, date, phash, vec}. Each back item takes its best live match in the same house and window."""
    out = []
    for b in back:
        best, best_cos = None, -1.0
        for m in meta:
            if m["house"] != b["house"] or abs((m["date"] - b["date"]).days) > max_days:
                continue
            cos = float(_unit(m["vec"]) @ _unit(b["vec"]))
            if cos > best_cos:
                best, best_cos = m, cos
        if best is not None and (best_cos >= cos_min or hamming(best["phash"], b["phash"]) <= phash_max):
            out.append((best, b, best_cos))
    return out


def match_fast(meta: list[dict], back: list[dict], max_days: int = MAX_DAYS, cos_min: float = COS_MIN,
               phash_max: int = PHASH_MAX) -> list[tuple[dict, dict, float]]:
    """`match`, with each house's live pictures stacked so the cosines are one product: the same pairs, the same
    order, for tens of thousands of pictures."""
    by_house: dict[str, list[dict]] = {}
    for m in meta:
        by_house.setdefault(m["house"], []).append(m)
    stacks = {}
    for h, ms in by_house.items():
        V = np.array([_unit(np.asarray(m["vec"], float)) for m in ms])
        D = np.array([m["date"].toordinal() for m in ms])
        stacks[h] = (ms, V, D)
    out = []
    for b in back:
        if b["house"] not in stacks:
            continue
        ms, V, D = stacks[b["house"]]
        ok = np.nonzero(np.abs(D - b["date"].toordinal()) <= max_days)[0]
        if not len(ok):
            continue
        cos = V[ok] @ _unit(np.asarray(b["vec"], float))
        k = int(np.argmax(cos))
        best, best_cos = ms[int(ok[k])], float(cos[k])
        if best_cos >= cos_min or hamming(best["phash"], b["phash"]) <= phash_max:
            out.append((best, b, best_cos))
    return out


def live_items(libraries: tuple[str, ...] = ("metaweb", "snap", "pinterest")) -> list[dict]:
    """Every paid social picture on file with a fingerprint, once per house: the day its ad was first shown (the
    earliest, where the same picture ran in several), its perceptual hash and its fingerprint."""
    from .adlibs import paths
    from .embed import VectorStore
    first: dict[tuple[str, str], dict] = {}
    for name in libraries:
        P = paths(name)
        if not P["ads"].exists():
            continue
        vecs = VectorStore(config.EMBED_TAG, root=P["vectors"]).vecs
        for p in sorted(P["ads"].glob("*.jsonl")):
            for r in store.read_jsonl(p):
                start = (r.get("first_shown") or r.get("start") or "")[:10]
                if r.get("division", "fashion") != "fashion" or not r.get("house_id") or len(start) != 10:
                    continue
                d = date.fromisoformat(start)
                for m in r.get("media") or []:
                    k = (r["house_id"], m.get("sha"))
                    if m.get("sha") in vecs and m.get("phash") and (k not in first or d < first[k]["date"]):
                        first[k] = {"house": r["house_id"], "date": d, "phash": m["phash"], "vec": vecs[m["sha"]],
                                    "sha": m["sha"], "library": name}
    return list(first.values())


def homepage_items(since: date | None = None) -> list[dict]:
    """The houses' own sites as the archive holds their homepages: every picture with a fingerprint, at the day of
    the first capture that shows it, with its perceptual hash; from `since` on, where given."""
    from . import homepages
    from .embed import VectorStore
    from .thread import _capture_day
    P = homepages.paths()
    vecs = VectorStore(config.EMBED_TAG, root=P["vectors"]).vecs
    first: dict[tuple[str, str], tuple[str, str]] = {}
    for f in sorted(P["captures"].glob("*.jsonl")) if P["captures"].exists() else []:
        for row in store.read_jsonl(f):
            if row.get("status") != "resolved" or not row.get("house_id"):
                continue
            day = _capture_day(row)
            for im in row.get("images") or []:
                k = (row["house_id"], im.get("sha"))
                if im.get("sha") in vecs and im.get("phash") and (k not in first or day < first[k][0]):
                    first[k] = (day, im["phash"])
    out = []
    for (h, sha), (day, ph) in first.items():
        d = date.fromisoformat(day)
        if since is None or d >= since:
            out.append({"house": h, "date": d, "phash": ph, "vec": vecs[sha], "sha": sha})
    return out


def offset(pairs) -> np.ndarray:
    return np.mean([_unit(m["vec"]) - _unit(b["vec"]) for m, b, _ in pairs], axis=0)


def splice(vec: np.ndarray, off: np.ndarray) -> np.ndarray:
    return _unit(_unit(vec) + off)


def calibrate(meta: list[dict], back: list[dict], matcher=None) -> dict:
    pairs = (matcher or match)(meta, back)
    by_house: dict[str, int] = {}
    for m, _, _ in pairs:
        by_house[m["house"]] = by_house.get(m["house"], 0) + 1
    out = {"n_pairs": len(pairs), "by_house": by_house, "min_pairs": MIN_PAIRS, "calibrated": len(pairs) >= MIN_PAIRS}
    if pairs:
        off = offset(pairs)
        out.update({"offset_norm": round(float(np.linalg.norm(off)), 4), "offset": [round(float(x), 6) for x in off],
                    "median_pair_cos": round(float(np.median([c for _, _, c in pairs])), 4)})
    return out


def _items(media_rows: dict, dates: dict, vecs: dict) -> list[dict]:
    items = []
    for key, m in media_rows.items():
        d = dates.get(key)
        if m.get("status") != "resolved" or not d:
            continue
        for im in m.get("images", []):
            if im["sha"] in vecs:
                items.append({"house": m["house_id"], "date": d, "phash": im["phash"], "vec": vecs[im["sha"]]})
    return items


def main(argv: list[str] | None = None) -> int:
    from .backcat import paths
    from .embed import VectorStore
    ads = store.ShardedTable(config.ADS_DIR, "ad_id").rows
    meta = _items(store.ShardedTable(config.MEDIA_DIR, "ad_id").rows,
                  {k: date.fromisoformat(a["start"][:10]) for k, a in ads.items() if a.get("start")},
                  VectorStore(config.EMBED_TAG).vecs)
    P = paths()
    camps = store.ShardedTable(P["campaigns"], "campaign_id").rows
    back = _items(store.ShardedTable(P["media"], "campaign_id").rows,
                  {k: date.fromisoformat(c["published"]) for k, c in camps.items() if c.get("published")},
                  VectorStore(config.EMBED_TAG, root=P["vectors"]).vecs)
    meta += live_items()
    since = min((m["date"] for m in meta), default=None)
    homes = homepage_items(since - timedelta(days=MAX_DAYS)) if since else []
    out = {"source": "house sites via the Wayback Machine", "generated_at": store.utc_now(),
           "n_live_images": len(meta), "n_backcat_images": len(back), **calibrate(meta, back, match_fast),
           "homepages": {"source": "the houses' homepages via the Wayback Machine, each picture at its first capture",
                         "n_images": len(homes), **calibrate(meta, homes, match_fast)}}
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.RESULTS_DIR / "calibration.json").write_text(json.dumps(out, indent=2) + "\n")
    print({k: v for k, v in out.items() if k != "offset"} | {"homepages": {k: v for k, v in out["homepages"].items()
                                                                          if k != "offset"}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
