"""A second, exploratory reading of the homepage pictures, as luxury pictures, by a larger reader.

Exploratory, not in the pre-registration: tone-v1 read by Qwen2.5-VL-7B at its pinned weights stays the
registered instrument. Which reader, which questions and which image model this reading uses are
decided by the bake-off (adtone/bakeoff.py).

    python -m adtone.luxury corpus      # every homepage picture and where it was seen -> data/luxury/corpus.json
    python -m adtone.luxury fetch       # fetched again from the archive: copies for the readers to the
                                        #   private Modal volume, pixel measures      -> data/luxury/pictures.jsonl
    python -m adtone.luxury read --reader qwen3 --what tone|pairs
                                        # every picture, and every comparison of the design in both orders
                                        #                                           -> data/luxury/readings/
    python -m adtone.luxury read --reader claude --what check
                                        # the standing check: a tenth of the pictures and a twentieth of
                                        #   the comparisons read again by Claude
    python -m adtone.luxury embed --model csd|dino|fashion           -> data/luxury/vectors/<model>.npz
    python -m adtone.luxury positions   # each picture's place on each axis           -> data/luxury/positions.jsonl

Readings are written as they arrive and a run picks up where the last stopped. No picture is ever
written to the repository, which is public: the readers' copies live on a private Modal volume
(adtone-pictures:/pictures-v1), the open reader reads them there by their sha, and only measurements
come back.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import bakeoff, config, store

DIR = config.DATA / "luxury"
PROV = config.DATA / "provenance" / "luxury.jsonl"
LOCAL = config.ROOT / "luxury_pictures"          # ignored by git; on the runner only
VOL_DIR = "/pictures-v1"
TYPES = {"brand_image": "campaign", "product_on_model": "on_model", "product_packshot": "packshot"}
ALSO = 2                                         # later captures to try when the first showing fails
SEED = 20261008
APPS = {"qwen3": "adtone-luxury-qwen3"}          # bakeoff/modal_reader.py deployed with the picture volume
CHECK_PICTURES = 10                              # Claude reads one picture in ten again
CHECK_PAIRS = 20                                 # and one comparison in twenty, in both orders


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


# ---------- what is read ----------

def found() -> dict[str, dict]:
    """The pictures fetched again, with their brand and month of first showing."""
    c = {p["sha"]: p for p in json.loads((DIR / "corpus.json").read_text(encoding="utf-8"))["pictures"]}
    return {r["sha"]: {**c[r["sha"]], "pixel": r.get("pixel")} for r in store.read_jsonl(DIR / "pictures.jsonl")
            if r.get("found") and r["sha"] in c}


def design(write: bool = True) -> dict:
    """On each axis every picture against six others, as in the bake-off: a circulant design on a random
    order of the pictures. Made once, from the pictures fetched by then, and kept."""
    path = DIR / "pairs.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    pics = [{"sha": s, "house": p["house"]} for s, p in sorted(found().items())]
    d = bakeoff.design(pics, [], bakeoff._pairs_rubric()["axes"], seed=SEED, human_per_axis=0, human_within=0)
    out = {"seed": SEED, "degree": bakeoff.DEGREE, "pictures": len(pics), "reader": d["reader"]}
    if write:
        DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, indent=0) + "\n", encoding="utf-8")
    _log("design", pictures=len(pics), comparisons={k: len(v) for k, v in d["reader"].items()})
    return out


def check_set() -> dict:
    """The standing check: one picture in ten from each brand, and one comparison in twenty on each axis."""
    rng = random.Random(SEED + 1)
    by = defaultdict(list)
    for s, p in sorted(found().items()):
        by[p["house"]].append(s)
    pictures = []
    for h in sorted(by):
        rng.shuffle(by[h])
        pictures += by[h][:max(1, round(len(by[h]) / CHECK_PICTURES))]
    d = design()
    pairs = {ax: sorted(rng.sample(es, max(1, len(es) // CHECK_PAIRS))) for ax, es in sorted(d["reader"].items())}
    return {"pictures": sorted(pictures), "pairs": pairs}


def _both_orders(pairs: dict[str, list]) -> list[tuple[str, str, str]]:
    base = [(ax, a, b) for ax, es in sorted(pairs.items()) for a, b in es]
    return base + [(ax, b, a) for ax, a, b in base]


def _done(path: Path, key) -> set:
    return {key(r) for r in store.read_jsonl(path) if not r.get("error")} if path.exists() else set()


def _tone_rows(part: list[str], texts: list[str], rub) -> list[dict]:
    from .score import ScoreError, parse
    rows = []
    for sha, text in zip(part, texts):
        try:
            rows.append({"sha": sha, "out": parse(text, rub)})
        except ScoreError as e:
            rows.append({"sha": sha, "error": str(e)[:200], "text": (text or "")[:300]})
    return rows


def _on_modal(reader: str, method: str, inputs: list, args, rows_of, out: Path, deadline: float) -> int:
    """Batches to the open reader on Modal, spread over its GPUs; each batch's rows are written as they
    come back, so a run that stops keeps what was read."""
    import modal
    rd = modal.Cls.from_name(APPS[reader], "Reader")()
    sent = []

    def gen():
        for part in inputs:
            if time.monotonic() > deadline:
                return
            sent.append(part)
            yield args(part)
    n = 0
    for k, res in enumerate(getattr(rd, method).starmap(gen(), return_exceptions=True)):
        if isinstance(res, BaseException):
            _log("batch_failed", reader=reader, method=method, error=f"{res.__class__.__name__}: {str(res)[:200]}")
            continue
        rows = rows_of(sent[k], res)
        store.append_jsonl(out, rows)
        n += len(rows)
    return n


def read(reader: str, what: str, budget_min: float = 280) -> dict:
    """tone: tone-v1 for every picture; pairs: every comparison of the design, both orders; check: Claude
    reads the check set (tone and pairs). Picks up where an earlier run stopped."""
    from .score import json_schema, load_rubric
    t0 = time.monotonic()
    deadline = t0 + budget_min * 60
    out_dir = DIR / "readings"
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = bakeoff._pairs_rubric()
    axes = {a["id"]: a for a in spec["axes"]}
    pics = found()
    result = {}
    if what in ("tone", "check"):
        rub = load_rubric()
        shas = sorted(pics) if what == "tone" else check_set()["pictures"]
        out = out_dir / f"{reader}-tone.jsonl"
        todo = [s for s in shas if s not in _done(out, lambda r: r["sha"])]
        if reader in APPS:
            schema = json_schema(rub)
            n = _on_modal(reader, "read_from", [todo[i:i + 16] for i in range(0, len(todo), 16)],
                          lambda part: (VOL_DIR, part, rub.prompt, schema),
                          lambda part, texts: _tone_rows(part, texts, rub), out, deadline)
        else:
            for i in range(0, len(todo), 100):
                if time.monotonic() > deadline:
                    break
                jpegs = bakeoff.from_volume(todo[i:i + 100], VOL_DIR)
                store.append_jsonl(out, bakeoff.read_tone(reader, jpegs))
            n = len(todo)
        result["tone"] = {"asked": len(todo), "rows": n}
    if what in ("pairs", "check"):
        pairs = design()["reader"] if what == "pairs" else check_set()["pairs"]
        out = out_dir / f"{reader}-pairs.jsonl"
        have = _done(out, lambda r: (r["axis"], r["first"], r["second"]) if r.get("answer") else None)
        todo = [j for j in _both_orders(pairs) if j not in have]
        if reader in APPS:
            n = _on_modal(reader, "compare_from", [todo[i:i + 64] for i in range(0, len(todo), 64)],
                          lambda part: (VOL_DIR, [(a, b) for _, a, b in part], spec["prompt"],
                                        [bakeoff.question(axes[ax], spec) for ax, _, _ in part], spec["answers"]),
                          lambda part, texts: [{"axis": ax, "first": a, "second": b, "answer": bakeoff.answer_of(t)}
                                               for (ax, a, b), t in zip(part, texts)], out, deadline)
        else:
            n = 0
            for i in range(0, len(todo), 400):
                if time.monotonic() > deadline:
                    break
                part = todo[i:i + 400]
                jpegs = bakeoff.from_volume(sorted({x for _, a, b in part for x in (a, b)}), VOL_DIR)
                rows = [{k: r[k] for k in ("axis", "first", "second", "answer", "error") if k in r}
                        for r in bakeoff.read_pairs(reader, jpegs, part)]
                store.append_jsonl(out, rows)
                n += len(rows)
        result["pairs"] = {"asked": len(todo), "rows": n}
    _log("read", reader=reader, what=what, seconds=round(time.monotonic() - t0), **result)
    return result


def embed(model: str) -> dict:
    return bakeoff.embed(model, shas=sorted(found()), vol_dir=VOL_DIR, out=DIR / "vectors" / f"{model}.npz", log=_log)


def positions(reader: str = "qwen3") -> dict:
    """Each picture's place on each axis (Bradley and Terry, with the first-position bias fitted
    alongside), from every comparison the reader answered."""
    rows = [r for r in store.read_jsonl(DIR / "readings" / f"{reader}-pairs.jsonl") if r.get("answer")]
    shas = sorted(found())
    idx = {s: i for i, s in enumerate(shas)}
    pos, info = {}, {}
    for ax in sorted({r["axis"] for r in rows}):
        rr = [r for r in rows if r["axis"] == ax and r["first"] in idx and r["second"] in idx]
        th, beta = bakeoff.bradley_terry(len(shas), np.array([idx[r["first"]] for r in rr]),
                                         np.array([idx[r["second"]] for r in rr]),
                                         np.array([1.0 if r["answer"] == "first" else 0.0 for r in rr]))
        n = np.bincount(np.array([idx[r["first"]] for r in rr] + [idx[r["second"]] for r in rr], dtype=int),
                        minlength=len(shas))
        pos[ax] = {s: (round(float(th[i]), 4) if n[i] else None) for s, i in idx.items()}
        info[ax] = {"judgements": len(rr), "first_bias": round(beta, 3), "pictures": int((n > 0).sum())}
    store.write_jsonl(DIR / f"positions-{reader}.jsonl", [{"sha": s, **{ax: pos[ax][s] for ax in pos}} for s in shas])
    _log("positions", reader=reader, axes=info)
    return info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m adtone.luxury")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("corpus")
    f = sub.add_parser("fetch")
    f.add_argument("--budget", type=float, default=150, help="minutes before no new page is started")
    r = sub.add_parser("read")
    r.add_argument("--reader", choices=sorted(APPS) + ["claude"], required=True)
    r.add_argument("--what", choices=["tone", "pairs", "check"], required=True)
    r.add_argument("--budget", type=float, default=280, help="minutes before no new batch is sent")
    e = sub.add_parser("embed")
    e.add_argument("--model", choices=sorted(bakeoff.EMBEDDERS), required=True)
    ps = sub.add_parser("positions")
    ps.add_argument("--reader", default="qwen3")
    a = ap.parse_args(argv)
    if a.cmd == "corpus":
        c = corpus()
        print(f"{len(c['pictures'])} pictures, {len(c['brands'])} brands, {c['pages']} pages")
    elif a.cmd == "fetch":
        corpus()
        rows = fetch(budget_min=a.budget)
        print(f"{sum(r['found'] for r in rows)} of {len(rows)} pictures found")
    elif a.cmd == "read":
        print(f"luxury read {a.reader} {a.what}: {read(a.reader, a.what, a.budget)}")
    elif a.cmd == "embed":
        print(f"luxury embed {a.model}: {embed(a.model)}")
    elif a.cmd == "positions":
        print(f"luxury positions {a.reader}: {positions(a.reader)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
