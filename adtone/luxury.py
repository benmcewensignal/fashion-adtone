"""A second, exploratory reading of the homepage pictures, as luxury pictures, by a larger reader.

Exploratory, not in the pre-registration: tone-v1 read by Qwen2.5-VL-7B at its pinned weights stays the
registered instrument. Which reader, which questions and which image model this reading uses are
decided by the bake-off (adtone/bakeoff.py).

    python -m adtone.luxury corpus      # every homepage picture and where it was seen -> data/luxury/corpus.json
    python -m adtone.luxury fetch       # fetched again from the archive: copies for the readers to the
                                        #   private Modal volume, pixel measures      -> data/luxury/pictures.jsonl

No picture is ever written to the repository, which is public: the readers' copies live on a private
Modal volume (adtone-pictures:/pictures-v1), and only measurements come back.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from . import bakeoff, config, store

DIR = config.DATA / "luxury"
PROV = config.DATA / "provenance" / "luxury.jsonl"
LOCAL = config.ROOT / "luxury_pictures"          # ignored by git; on the runner only
VOL_DIR = "/pictures-v1"
TYPES = {"brand_image": "campaign", "product_on_model": "on_model", "product_packshot": "packshot"}
ALSO = 2                                         # later captures to try when the first showing fails


def _log(event: str, **kw) -> None:
    store.append_jsonl(PROV, [{"at": store.utc_now(), "event": event, **kw}])


def corpus(write: bool = True) -> dict:
    """Every picture any homepage capture found, once, at the capture where it was first shown, with up
    to two later captures to try if the archive no longer serves it there, and the kind of picture
    today's reader took it for."""
    from . import homepages
    P = homepages.paths()
    seen: dict[str, list] = defaultdict(list)
    for f in sorted(P["captures"].glob("*.jsonl")):
        for r in store.read_jsonl(f):
            if r.get("status") != "resolved":
                continue
            page = r.get("page") or r.get("url")
            for im in r.get("images", []):
                seen[im["sha"]].append((r["month"], r["capture"], r["house_id"], page))
    kinds = {}
    obs = P["obs"] / "tone-v1.jsonl"
    for r in store.read_jsonl(obs) if obs.exists() else []:
        if r.get("status") == "ok":
            kinds[r["sha"]] = TYPES.get((r.get("output") or {}).get("creative_type"), "other")
    pictures = []
    for sha, occ in seen.items():
        occ = sorted(set(occ))
        month, capture, house, page = occ[0]
        also, places = [], {(capture, page)}
        for _, c, _, pg in occ[1:]:
            if (c, pg) not in places and len(also) < ALSO:
                places.add((c, pg))
                also.append([c, pg])
        pictures.append({"sha": sha, "house": house, "month": month, "kind": kinds.get(sha), "capture": capture,
                         "page": page, "also": also, "shown": len({o[0] for o in occ})})
    pictures.sort(key=lambda p: (p["house"], p["month"], p["sha"]))
    out = {"pictures": pictures, "brands": sorted({p["house"] for p in pictures}),
           "pages": len({(p["capture"], p["page"]) for p in pictures})}
    if write:
        DIR.mkdir(parents=True, exist_ok=True)
        (DIR / "corpus.json").write_text(json.dumps(out, indent=0) + "\n", encoding="utf-8")
    _log("corpus", pictures=len(pictures), brands=len(out["brands"]), pages=out["pages"])
    return out


def _seed_from_bakeoff(wanted: set[str], present: set[str]) -> dict[str, dict]:
    """Pictures the bake-off already fetched are copied across from its folder on the volume rather
    than fetched from the archive again."""
    path = bakeoff.DIR / "pictures.jsonl"
    rows = {r["sha"]: r for r in store.read_jsonl(path) if r.get("found") and r["sha"] in wanted} if path.exists() else {}
    todo = [sha for sha in rows if sha not in present]
    if not todo:
        return {}
    (LOCAL / "read").mkdir(parents=True, exist_ok=True)
    got = bakeoff.from_volume(todo)
    files = []
    for sha, data in got.items():
        f = LOCAL / "read" / f"{sha}.jpg"
        f.write_bytes(data)
        files.append(f)
    bakeoff.to_volume(vol_dir=VOL_DIR, files=files)
    return {sha: {**rows[sha], "from": "bakeoff"} for sha in got}


def fetch(budget_min: float = 150, workers: int = 4) -> list[dict]:
    """Every picture of the corpus fetched again (resumable): what is already on the volume is kept,
    what the bake-off holds is copied across, the rest is sought in the archive, and the readers'
    copies go to the volume whatever happens."""
    c = json.loads((DIR / "corpus.json").read_text(encoding="utf-8")) if (DIR / "corpus.json").exists() else corpus()
    out = DIR / "pictures.jsonl"
    present = bakeoff.on_volume(VOL_DIR)
    prev = {r["sha"]: r for r in store.read_jsonl(out)} if out.exists() else {}
    seeded = _seed_from_bakeoff({p["sha"] for p in c["pictures"]}, present)
    if seeded:
        prev.update(seeded)
        present |= set(seeded)
        store.write_jsonl(out, [prev[p["sha"]] for p in c["pictures"] if p["sha"] in prev])
        _log("seed", from_bakeoff=len(seeded))
    try:
        rows = bakeoff.fetch(c, workers=workers, budget_min=budget_min, out=out, local=LOCAL, thumbs=False,
                             present=present, log=_log)
    finally:
        new = [f for f in sorted((LOCAL / "read").glob("*.jpg")) if f.stem not in present]
        n = bakeoff.to_volume(vol_dir=VOL_DIR, files=new)
        _log("to_volume", files=n)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m adtone.luxury")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("corpus")
    f = sub.add_parser("fetch")
    f.add_argument("--budget", type=float, default=150, help="minutes before no new page is started")
    a = ap.parse_args(argv)
    if a.cmd == "corpus":
        c = corpus()
        print(f"{len(c['pictures'])} pictures, {len(c['brands'])} brands, {c['pages']} pages")
    elif a.cmd == "fetch":
        corpus()
        rows = fetch(budget_min=a.budget)
        print(f"{sum(r['found'] for r in rows)} of {len(rows)} pictures found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
