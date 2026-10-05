"""Splice the back catalogue onto the live series.

    python -m adtone.calibrate

A campaign photographed once can reach both a house's own site and its paid Meta ads, cropped
and formatted differently. In the overlap year those reappearances are matched pairs: the same
image seen through two channels. Their average difference in embedding space is the channel's
offset, measured directly rather than modelled (the matched-pair method from the photographer
work). A source is spliced only with enough pairs; otherwise it stays descriptive.
"""
from __future__ import annotations

import json
import sys
from datetime import date

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


def offset(pairs) -> np.ndarray:
    return np.mean([_unit(m["vec"]) - _unit(b["vec"]) for m, b, _ in pairs], axis=0)


def splice(vec: np.ndarray, off: np.ndarray) -> np.ndarray:
    return _unit(_unit(vec) + off)


def calibrate(meta: list[dict], back: list[dict]) -> dict:
    pairs = match(meta, back)
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
    out = {"source": "house sites via the Wayback Machine", "generated_at": store.utc_now(),
           "n_live_images": len(meta), "n_backcat_images": len(back), **calibrate(meta, back)}
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.RESULTS_DIR / "calibration.json").write_text(json.dumps(out, indent=2) + "\n")
    print({k: v for k, v in out.items() if k != "offset"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
