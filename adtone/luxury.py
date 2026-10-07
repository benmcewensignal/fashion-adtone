"""A second, exploratory reading of the homepage pictures, as luxury pictures, by a larger reader.

Exploratory, not in the pre-registration: tone-v1 read by Qwen2.5-VL-7B at its pinned weights stays the
registered instrument. Which reader, which questions and which image model this reading uses are
decided by the bake-off (adtone/bakeoff.py).

    python -m adtone.luxury corpus      # every homepage picture and where it was seen -> data/luxury/corpus.json
    python -m adtone.luxury fetch       # fetched again from the archive: copies for the readers to the
                                        #   private Modal volume, pixel measures      -> data/luxury/pictures.jsonl
    python -m adtone.luxury read --reader qwen3 --what tone|comparisons
                                        # every picture, and every comparison of the design on the axes the
                                        #   plan keeps, in both orders, as written answers or as the weight
                                        #   on each answer (data/luxury/plan.json)  -> data/luxury/readings/
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
import math
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
APPS = {"qwen3": "adtone-luxury-qwen3",           # bakeoff/modal_reader.py deployed with the picture volume
        "qwen25": "adtone-luxury-qwen25"}
CHECK_PICTURES = 10                              # Claude reads one picture in ten again
CHECK_PAIRS = 20                                 # and one comparison in twenty, in both orders


def _log(event: str, **kw) -> None:
    store.append_jsonl(PROV, [{"at": store.utc_now(), "event": event, **kw}])


def plan() -> dict:
    """What the bake-off decided for this reading, recorded before the reading starts: which axes are kept,
    and whether the comparisons are taken as the reader's written answers ("answers") or as the weight it
    puts on each answer ("probs"). With no plan, every axis, as written answers."""
    path = DIR / "plan.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _kept_axes(all_axes) -> list[str]:
    p = plan()
    return sorted(all_axes) if "axes" not in p else [a for a in sorted(all_axes) if a in set(p["axes"])]


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
    if not inputs:          # nothing to read: a map over no inputs can wait on Modal indefinitely
        return 0
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
    """tone: tone-v1 for every picture; pairs: every comparison of the design on the kept axes, both orders,
    as written answers; probs: the same comparisons as the weight the reader puts on each answer;
    comparisons: whichever of the two the plan names (nothing when it keeps no axis); check: Claude reads
    the check set (tone, and the kept axes' comparisons). Picks up where an earlier run stopped."""
    from .score import json_schema, load_rubric
    t0 = time.monotonic()
    deadline = t0 + budget_min * 60
    out_dir = DIR / "readings"
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = bakeoff._pairs_rubric()
    axes = {a["id"]: a for a in spec["axes"]}
    kept = _kept_axes(axes)
    if what == "comparisons":
        if not kept:
            _log("read", reader=reader, what=what, note="the plan keeps no axis")
            return {"comparisons": "the plan keeps no axis"}
        what = "probs" if plan().get("comparisons") == "probs" else "pairs"
    pics = found()
    result = {}
    if what == "probs":
        pairs = {ax: es for ax, es in design()["reader"].items() if ax in kept}
        out = out_dir / f"{reader}-probs.jsonl"
        have = _done(out, lambda r: (r["axis"], r["first"], r["second"]) if r.get("p_first") is not None else None)
        todo = [j for j in _both_orders(pairs) if j not in have]
        n = _on_modal(reader, "compare_probs_from", [todo[i:i + 64] for i in range(0, len(todo), 64)],
                      lambda part: (VOL_DIR, [(a, b) for _, a, b in part], spec["prompt"],
                                    [bakeoff.question(axes[ax], spec) for ax, _, _ in part]),
                      lambda part, probs: [{"axis": ax, "first": a, "second": b, "p_first": round(float(pf), 5)}
                                           for (ax, a, b), pf in zip(part, probs)], out, deadline)
        result["probs"] = {"asked": len(todo), "rows": n}
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
        pairs = {ax: es for ax, es in pairs.items() if ax in kept}
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
    """Each picture's place on each kept axis (Bradley and Terry, with the first-position bias fitted
    alongside), from every comparison the reader answered: its written answers, or, when the plan says so,
    the weight it put on "first" against "second"."""
    as_probs = plan().get("comparisons") == "probs"
    f = DIR / "readings" / f"{reader}-{'probs' if as_probs else 'pairs'}.jsonl"
    rows = [r for r in store.read_jsonl(f) if (r.get("p_first") is not None if as_probs else r.get("answer"))] \
        if f.exists() else []
    shas = sorted(found())
    idx = {s: i for i, s in enumerate(shas)}
    pos, info = {}, {}
    for ax in _kept_axes({r["axis"] for r in rows}):
        rr = [r for r in rows if r["axis"] == ax and r["first"] in idx and r["second"] in idx]
        y = [float(r["p_first"]) for r in rr] if as_probs else [1.0 if r["answer"] == "first" else 0.0 for r in rr]
        th, beta = bakeoff.bradley_terry(len(shas), np.array([idx[r["first"]] for r in rr]),
                                         np.array([idx[r["second"]] for r in rr]), np.array(y))
        n = np.bincount(np.array([idx[r["first"]] for r in rr] + [idx[r["second"]] for r in rr], dtype=int),
                        minlength=len(shas))
        pos[ax] = {s: (round(float(th[i]), 4) if n[i] else None) for s, i in idx.items()}
        info[ax] = {"judgements": len(rr), "first_bias": round(beta, 3), "pictures": int((n > 0).sum())}
    store.write_jsonl(DIR / f"positions-{reader}.jsonl", [{"sha": s, **{ax: pos[ax][s] for ax in pos}} for s in shas])
    _log("positions", reader=reader, axes=info)
    return info


# ---------- the readings on the new instrument ----------

def _standard(table: dict[str, dict[str, float]], keys: list[str]) -> dict[str, np.ndarray]:
    """Each measure in standard units over the pictures that have it; a picture missing one is left out."""
    shas = sorted(s for s, row in table.items() if all(row.get(k) is not None for k in keys))
    if not shas:
        return {}
    X = np.array([[float(table[s][k]) for k in keys] for s in shas])
    sd = X.std(0)
    X = (X - X.mean(0)) / np.where(sd > 0, sd, 1.0)
    return {s: X[i] for i, s in enumerate(shas)}


def vectors(kind: str, reader: str = "qwen3", axes: list[str] | None = None) -> dict[str, np.ndarray]:
    """A picture's vector: clip (today's fingerprint) or a style model's (csd, dino, fashion), each a
    direction; positions (the reader's place for the picture on each axis, or the axes given) or pixels
    (colour and light), each in standard units."""
    if kind == "clip":
        from .embed import VectorStore
        from . import homepages
        root = homepages.paths()["vectors"]
        out = {}
        for d in sorted(root.glob("*")) if root.exists() else []:
            out.update(VectorStore(d.name, root=root).vecs)
        return {s: v / (np.linalg.norm(v) or 1.0) for s, v in out.items()}
    if kind in bakeoff.EMBEDDERS:
        f = DIR / "vectors" / f"{kind}.npz"
        if not f.exists():
            return {}
        with np.load(f, allow_pickle=False) as z:
            return {str(s): v.astype(np.float64) / (np.linalg.norm(v.astype(np.float64)) or 1.0) for s, v in zip(z["shas"], z["vecs"])}
    if kind == "positions":
        rows = {r["sha"]: r for r in store.read_jsonl(DIR / f"positions-{reader}.jsonl")}
        keys = axes or sorted({k for r in rows.values() for k in r if k != "sha"})
        return _standard(rows, keys)
    if kind == "pixels":
        rows = {r["sha"]: r.get("pixel") or {} for r in store.read_jsonl(DIR / "pictures.jsonl") if r.get("found")}
        return _standard(rows, bakeoff.PIXEL_KEYS)
    raise ValueError(kind)


def load_images(reader: str = "qwen3", vec: str = "clip", axes: list[str] | None = None) -> list[dict]:
    """The homepage pictures as adtone.readings takes them: every showing of every picture fetched again,
    with the answers `reader` gave (tone-v1), the kind of picture as that reader saw it, and the vector
    chosen. Today's fingerprint stays under "dup", for telling two crops of one picture."""
    from . import homepages
    from .character import period_of
    answers = {}
    if reader == "today":        # the registered reader's answers, as the homepage pipeline recorded them
        for f in sorted(homepages.paths()["obs"].glob("*.jsonl")):
            for r in store.read_jsonl(f):
                if r.get("status") == "ok" and r.get("output"):
                    answers[r["sha"]] = r["output"]
    else:
        answers = _tone_of(reader)
    vecs = vectors(vec, "qwen3" if reader == "today" else reader, axes)
    dup = vectors("clip") if vec != "clip" else vecs
    out, seen = [], set()
    for f in sorted(homepages.paths()["captures"].glob("*.jsonl")):
        for row in store.read_jsonl(f):
            if row.get("status") != "resolved":
                continue
            for im in row.get("images") or []:
                s = im["sha"]
                key = (row["house_id"], row["month"], s)
                if key in seen or s not in answers or s not in vecs:
                    continue
                seen.add(key)
                out.append({"house": row["house_id"], "month": row["month"], "sha": s, "out": answers[s],
                            "type": TYPES.get(answers[s].get("creative_type"), "other"), "period": period_of(row["month"]),
                            "vec": vecs[s], "dup": dup.get(s)})
    return out


READINGS = ("clip", "positions", "pixels", "csd", "dino", "fashion")


def within_brands(images: list[dict], keys: list[str], min_per_brand: int = 12) -> dict:
    """Is a brand's character a point or a range? For each measure (in standard units): the share of all
    variation that lies between brands, and how the rest, inside brands, divides: by the kind of picture
    (campaign, product on a model, packshot), by half-year, by the creative director's era, and what is
    left; and how much of the inside is no more than the noise between two crops of one picture. Each
    picture counted once, where it was first shown."""
    from . import readings
    uniq = readings.once([i for i in images if i["vec"] is not None])
    by: dict[str, list] = defaultdict(list)
    for im in uniq:
        by[im["house"]].append(im)
    by = {h: ims for h, ims in by.items() if len(ims) >= min_per_brand}
    pics = [im for ims in by.values() for im in ims]
    if len(by) < 3:
        return {"brands": len(by), "note": "too few brands with enough pictures"}
    X = np.vstack([np.asarray(im["vec"], float) for im in pics])
    house = np.array([im["house"] for im in pics])
    events = defaultdict(list)
    for e in readings.designer_events():
        events[e["house"]].append(e["date"][:7])
    era = np.array([f'{im["house"]}:{sum(d <= im["month"] for d in events.get(im["house"], []))}' for im in pics])
    kind = np.array([f'{im["house"]}:{im["type"]}' for im in pics])
    half = np.array([f'{im["house"]}:{im["period"]}' for im in pics])
    crops = readings.duplicate_pairs(readings.once([i for i in images if i["vec"] is not None], lambda m: m))
    idx = {(im["house"], im["sha"]): n for n, im in enumerate(pics)}
    pairs = [(idx[(a["house"], a["sha"])], idx[(b["house"], b["sha"])]) for a, b in crops
             if (a["house"], a["sha"]) in idx and (b["house"], b["sha"]) in idx]

    n_brands = len(by)

    def explained(v: np.ndarray, groups: np.ndarray) -> float:
        """Share of v's variance (v centred within brand) explained by group means, less what as many
        groups would explain by chance (epsilon squared): small groups explain something by luck alone."""
        tot = float((v ** 2).sum())
        if not tot:
            return 0.0
        levels = sorted(set(groups))
        ssb = float(sum((groups == g).sum() * v[groups == g].mean() ** 2 for g in levels))
        df_b = len(levels) - n_brands
        df_w = len(v) - len(levels)
        if df_b <= 0 or df_w <= 0:
            return 0.0
        msw = (tot - ssb) / df_w
        return float(max(0.0, (ssb - df_b * msw) / tot))
    out = {}
    for j, k in enumerate(keys):
        v = X[:, j] - X[:, j].mean()
        tot = float((v ** 2).sum())
        brand_mean = {h: v[house == h].mean() for h in by}
        w = v - np.array([brand_mean[h] for h in house])        # inside brands
        ssw = float((w ** 2).sum())
        msw = ssw / max(1, len(v) - n_brands)
        between = max(0.0, (tot - ssw - (n_brands - 1) * msw) / tot) if tot else 0.0      # net of chance
        noise = float(np.mean([(X[a, j] - X[b, j]) ** 2 / 2 for a, b in pairs])) if len(pairs) >= 10 else None
        inside_var = float((w ** 2).mean())
        out[k] = {"between_brands": round(between, 3), "inside_brands": round(1 - between, 3),
                  "inside_by_kind": round(explained(w, kind), 3), "inside_by_half_year": round(explained(w, half), 3),
                  "inside_by_designer_era": round(explained(w, era), 3),
                  "inside_that_is_crop_noise": None if noise is None or not inside_var else round(min(1.0, noise / inside_var), 3),
                  "crop_pairs": len(pairs)}
    spread = {h: round(float(np.sqrt(np.mean([((np.asarray(im["vec"], float) - X.mean(0)) ** 2).sum() for im in ims]))), 3)
              for h, ims in by.items()}
    return {"brands": len(by), "pictures": len(pics), "measures": out,
            "spread_by_brand": dict(sorted(spread.items(), key=lambda kv: -kv[1])),
            "note": "shares of variance; inside_by_* are shares of the variation inside brands, each on its own "
                    "and net of what as many groups explain by chance (they overlap: a designer era is also a run of "
                    "half-years); crop noise is half the mean squared difference between two crops of one picture, "
                    "against the variation inside brands"}


def _tone_of(name: str) -> dict[str, dict]:
    out = {}
    for r in store.read_jsonl(DIR / "readings" / f"{name}-tone.jsonl"):
        if r.get("out"):
            out[r["sha"]] = r["out"]
    return out


def _logit(p: float) -> float:
    p = min(max(float(p), 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _winners(name: str) -> dict[tuple, str | None]:
    """For each comparison asked both ways round: the picture chosen both times, or None when the two
    orders disagree. When the plan takes the open reader's comparisons as probabilities: the picture both
    orders favour once the reader's lean towards one position on that axis is taken out, or None."""
    pf = DIR / "readings" / f"{name}-probs.jsonl"
    if name in APPS and plan().get("comparisons") == "probs" and pf.exists():
        rows = [r for r in store.read_jsonl(pf) if r.get("p_first") is not None]
        lean = {ax: float(np.mean([_logit(r["p_first"]) for r in rows if r["axis"] == ax])) for ax in {r["axis"] for r in rows}}
        byp = defaultdict(dict)
        for r in rows:
            byp[(r["axis"], frozenset((r["first"], r["second"])))][(r["first"], r["second"])] = \
                _logit(r["p_first"]) - lean[r["axis"]]
        out = {}
        for k, v in byp.items():
            if len(v) != 2:
                continue
            a, b = next(iter(v))
            za, zb = v[(a, b)], v[(b, a)]
            out[k] = (a if za > 0 else b) if (za > 0) != (zb > 0) else None
        return out
    by = defaultdict(dict)
    for r in store.read_jsonl(DIR / "readings" / f"{name}-pairs.jsonl"):
        if r.get("answer"):
            by[(r["axis"], frozenset((r["first"], r["second"])))][(r["first"], r["second"])] = \
                r["first"] if r["answer"] == "first" else r["second"]
    return {k: (next(iter(v.values())) if len(set(v.values())) == 1 else None) for k, v in by.items() if len(v) == 2}


def check_report(reader: str = "qwen3") -> dict:
    """The standing check: on the same pictures, how far the open reader and Claude agree on each
    question (Cohen's kappa), and on the same comparisons, how often they pick the same picture."""
    from .character import QUESTIONS
    if not (DIR / "readings" / "claude-tone.jsonl").exists():
        return {"note": "Claude has not read the check set"}
    cs = check_set()
    q, c = _tone_of(reader), _tone_of("claude")
    both = sorted(set(q) & set(c) & set(cs["pictures"]))
    keys = ["creative_type", *QUESTIONS, "street_couture_axis"]
    tone = {k: bakeoff._kappa2([q[s].get(k) for s in both], [c[s].get(k) for s in both]) for k in keys}
    wq, wc = _winners(reader), _winners("claude")
    pairs = {}
    for ax, es in sorted(cs["pairs"].items()):
        ks = [(ax, frozenset(e)) for e in es if (ax, frozenset(e)) in wq and (ax, frozenset(e)) in wc]
        decided = [k for k in ks if wq[k] is not None and wc[k] is not None]
        pairs[ax] = {"comparisons": len(ks), "both_decided": len(decided),
                     "agree": round(sum(wq[k] == wc[k] for k in decided) / len(decided), 3) if decided else None,
                     "open_reader_split": round(sum(wq[k] is None for k in ks) / len(ks), 3) if ks else None,
                     "claude_split": round(sum(wc[k] is None for k in ks) / len(ks), 3) if ks else None}
    return {"pictures": len(both), "tone_kappa": tone, "pairs": pairs}


def analyse(reader: str = "qwen3", which: tuple[str, ...] = READINGS) -> dict:
    """adtone.readings run on the new instrument: the reader's answers each time, beside each vector in
    turn (directions compared by cosine, measures by distance)."""
    from . import readings, registry
    from .score import load_rubric
    spec, reg = load_rubric().spec, registry.load()
    out = {"generated_at": store.utc_now(), "reader": reader,
           "status": "exploratory: the luxury reading, not in the pre-registration", "readings": {},
           "check": check_report(reader)}
    for kind in which:
        ims = load_images(reader, kind)
        if len(ims) < 50:
            out["readings"][kind] = {"note": f"only {len(ims)} pictures with answers and this vector"}
            continue
        t0 = time.monotonic()
        res = readings.run(ims, spec, reg, euclid=kind in ("positions", "pixels"))
        if kind == "pixels":
            res["within_brands"] = within_brands(ims, bakeoff.PIXEL_KEYS)
        elif kind == "positions":
            rows = store.read_jsonl(DIR / f"positions-{reader}.jsonl")
            res["within_brands"] = within_brands(ims, sorted({k for r in rows for k in r if k != "sha"}))
        res["seconds"] = round(time.monotonic() - t0)
        out["readings"][kind] = res
    path = config.RESULTS_DIR / "luxury.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    _log("analyse", reader=reader, readings={k: v.get("images") for k, v in out["readings"].items()})
    return out


def bakeoff_read(reader: str, what: str, budget_min: float = 150) -> dict:
    """The bake-off's own readings (tone on its pictures, the reader design, the person's pairs, both
    orders) taken through the deployed luxury reader, which reads the pictures from the volume by sha:
    a second route to the same answers, kept apart in data/luxury/bakeoff/ and picked up by the bake-off's
    scoring beside its own."""
    from .score import json_schema, load_rubric
    t0 = time.monotonic()
    deadline = t0 + budget_min * 60
    out_dir = DIR / "bakeoff"
    out_dir.mkdir(parents=True, exist_ok=True)
    found = sorted(r["sha"] for r in store.read_jsonl(bakeoff.DIR / "pictures.jsonl") if r.get("found"))
    out = out_dir / f"{reader}-{what}.jsonl"
    if reader == "claude":       # the closed reader, through the API, on the bake-off's copies of the pictures
        jpegs = bakeoff.from_volume(found)
        if what == "tone":
            todo = [s for s in found if s not in _done(out, lambda r: r["sha"])]
            rows = bakeoff.read_tone("claude", {s: jpegs[s] for s in todo if s in jpegs})
        else:
            d = json.loads((bakeoff.DIR / "pairs.json").read_text(encoding="utf-8"))
            base = [(h["axis"], h["left"], h["right"]) for h in d["human"]] if what == "human" else \
                [(ax, a, b) for ax, es in d["reader"].items() for a, b in es]
            have = _done(out, lambda r: (r["axis"], r["first"], r["second"]) if r.get("answer") else None)
            todo = [j for j in base + [(ax, b, a) for ax, a, b in base] if j not in have]
            rows = [{k: r[k] for k in ("axis", "first", "second", "answer", "error") if k in r}
                    for r in bakeoff.read_pairs("claude", jpegs, todo)]
        store.append_jsonl(out, rows)
        _log("bakeoff_read", reader=reader, what=what, asked=len(todo), rows=len(rows), seconds=round(time.monotonic() - t0))
        return {"asked": len(todo), "rows": len(rows)}
    if what == "tone":
        rub = load_rubric()
        schema = json_schema(rub)
        todo = [s for s in found if s not in _done(out, lambda r: r["sha"])]
        n = _on_modal(reader, "read_from", [todo[i:i + 16] for i in range(0, len(todo), 16)],
                      lambda part: (VOL_DIR, part, rub.prompt, schema),
                      lambda part, texts: _tone_rows(part, texts, rub), out, deadline)
    else:
        spec = bakeoff._pairs_rubric()
        axes = {a["id"]: a for a in spec["axes"]}
        d = json.loads((bakeoff.DIR / "pairs.json").read_text(encoding="utf-8"))
        base = [(h["axis"], h["left"], h["right"]) for h in d["human"]] if what == "human" else \
            [(ax, a, b) for ax, es in d["reader"].items() for a, b in es]
        have = _done(out, lambda r: (r["axis"], r["first"], r["second"]) if r.get("answer") else None)
        todo = [j for j in base + [(ax, b, a) for ax, a, b in base] if j not in have]
        n = _on_modal(reader, "compare_from", [todo[i:i + 64] for i in range(0, len(todo), 64)],
                      lambda part: (VOL_DIR, [(a, b) for _, a, b in part], spec["prompt"],
                                    [bakeoff.question(axes[ax], spec) for ax, _, _ in part], spec["answers"]),
                      lambda part, texts: [{"axis": ax, "first": a, "second": b, "answer": bakeoff.answer_of(t)}
                                           for (ax, a, b), t in zip(part, texts)], out, deadline)
    _log("bakeoff_read", reader=reader, what=what, asked=len(todo), rows=n, seconds=round(time.monotonic() - t0))
    return {"asked": len(todo), "rows": n}


# ---------- does the reader know the brand? ----------

BRAND_SYSTEM = ("You look at pictures from luxury fashion houses' own homepages and say which house a picture comes "
                "from, using only what is in the picture: logos, wordmarks, signature products, monograms, styling. "
                "Do not identify any person.")
BRAND_QUESTION = ("Which fashion house's homepage is this picture from? Answer with the house's name exactly as listed, "
                  "or 'cannot tell' if nothing in the picture tells you.\nHouses: {names}.")


def _houses() -> dict[str, str]:
    from . import registry
    return {h.id: h.name for h in registry.load().houses}


def _norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return "".join(ch for ch in s if ch.isalnum())


def _match_house(text: str, choices: list[str]) -> str:
    """The house named in a free answer, longest name first, "Maison " optional; else cannot tell."""
    t = _norm(text)
    for c in sorted(choices, key=len, reverse=True):
        keys = {_norm(c), _norm(c.replace("Maison ", ""))}
        if c != "cannot tell" and any(k and k in t for k in keys):
            return c
    return "cannot tell"


def recognise(reader: str, budget_min: float = 60) -> dict:
    """Asks the reader which house each bake-off picture comes from: a reader that can tell may carry its view of
    the brand into its reading of the picture."""
    t0 = time.monotonic()
    names = _houses()
    choices = sorted(names.values()) + ["cannot tell"]
    q = BRAND_QUESTION.format(names=", ".join(sorted(names.values())))
    found = sorted(r["sha"] for r in store.read_jsonl(bakeoff.DIR / "pictures.jsonl") if r.get("found"))
    out = DIR / "bakeoff" / f"{reader}-brand.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    todo = [s for s in found if s not in _done(out, lambda r: r["sha"] if r.get("answer") else None)]
    if reader in APPS:
        n = _on_modal(reader, "ask_from", [todo[i:i + 32] for i in range(0, len(todo), 32)],
                      lambda part: (VOL_DIR, part, BRAND_SYSTEM, q, choices),
                      lambda part, texts: [{"sha": s, "answer": (t or "").strip()} for s, t in zip(part, texts)],
                      out, time.monotonic() + budget_min * 60)
    else:
        import anthropic
        client = anthropic.Anthropic()
        jpegs = bakeoff.from_volume(todo)

        def one(sha):
            import base64
            content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.b64encode(jpegs[sha]).decode()}},
                       {"type": "text", "text": q}]
            for attempt in range(6):
                try:
                    r = client.messages.create(model=bakeoff.READERS["claude"]["model"], max_tokens=20, temperature=0,
                                               system=BRAND_SYSTEM, messages=[{"role": "user", "content": content}])
                    text = "".join(getattr(x, "text", "") for x in r.content).strip()
                    return {"sha": sha, "answer": _match_house(text, choices), "text": text[:40]}
                except Exception as e:
                    if getattr(e, "status_code", None) not in (408, 409, 429, 500, 502, 503, 504, 529):
                        return {"sha": sha, "error": f"{e.__class__.__name__}: {str(e)[:100]}"}
                    time.sleep(min(60, 4 * 2 ** attempt) + random.random())
            return {"sha": sha, "error": "no answer"}
        with ThreadPoolExecutor(4) as ex:
            rows = list(ex.map(one, [s for s in todo if s in jpegs]))
        store.append_jsonl(out, rows)
        n = len(rows)
    _log("recognise", reader=reader, asked=len(todo), rows=n, seconds=round(time.monotonic() - t0))
    return {"asked": len(todo), "rows": n}


def bakeoff_probs(reader: str = "qwen3", budget_min: float = 120) -> dict:
    """The bake-off's comparisons read again as probabilities: how much weight the reader puts on "first"
    against "second", so that the two orders can be averaged and its lean towards one position removed."""
    t0 = time.monotonic()
    spec = bakeoff._pairs_rubric()
    axes = {a["id"]: a for a in spec["axes"]}
    d = json.loads((bakeoff.DIR / "pairs.json").read_text(encoding="utf-8"))
    base = [(ax, a, b) for ax, es in d["reader"].items() for a, b in es]
    out = DIR / "bakeoff" / f"{reader}-probs.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    have = _done(out, lambda r: (r["axis"], r["first"], r["second"]) if r.get("p_first") is not None else None)
    todo = [j for j in base + [(ax, b, a) for ax, a, b in base] if j not in have]
    n = _on_modal(reader, "compare_probs_from", [todo[i:i + 64] for i in range(0, len(todo), 64)],
                  lambda part: (VOL_DIR, [(a, b) for _, a, b in part], spec["prompt"],
                                [bakeoff.question(axes[ax], spec) for ax, _, _ in part]),
                  lambda part, probs: [{"axis": ax, "first": a, "second": b, "p_first": round(float(pf), 5)}
                                       for (ax, a, b), pf in zip(part, probs)], out, time.monotonic() + budget_min * 60)
    _log("bakeoff_probs", reader=reader, asked=len(todo), rows=n, seconds=round(time.monotonic() - t0))
    return {"asked": len(todo), "rows": n}


def _bakeoff_frame():
    s = json.loads((bakeoff.DIR / "sample.json").read_text(encoding="utf-8"))
    found = {r["sha"] for r in store.read_jsonl(bakeoff.DIR / "pictures.jsonl") if r.get("found")}
    pics = {p["sha"]: p for p in s["pictures"] if p["sha"] in found}
    crops = [c for c in s["crop_pairs"] if c[0] in pics and c[1] in pics]
    human_file = bakeoff.DIR / "human.json"
    human = json.loads(human_file.read_text(encoding="utf-8")) if human_file.exists() else {}
    hp = {h["id"]: h for h in json.loads((bakeoff.DIR / "pairs.json").read_text(encoding="utf-8"))["human"]}
    judged = {k: v for k, v in human.items() if k in hp and v in ("left", "right")}
    return pics, crops, judged, hp


def recognition_report() -> dict:
    """How often each reader names the right house, and whether a reader that knows the house reads its
    pictures differently: brands further apart, and agreement with the person, on pictures it recognised
    against pictures it did not."""
    pics, crops, judged, hp = _bakeoff_frame()
    names = _houses()
    q3_tone = {r["sha"]: r["out"] for r in store.read_jsonl(DIR / "bakeoff" / "qwen3-tone.jsonl") if r.get("out")} \
        if (DIR / "bakeoff" / "qwen3-tone.jsonl").exists() else {}
    out = {}
    for f in sorted((DIR / "bakeoff").glob("*-brand.jsonl")):
        reader = f.stem.rsplit("-", 1)[0]
        ans = {r["sha"]: r["answer"] for r in store.read_jsonl(f) if r.get("answer") and r["sha"] in pics}
        right = {s for s, a in ans.items() if a == names.get(pics[s]["house"])}
        told = {s for s, a in ans.items() if a != "cannot tell"}
        by_text = defaultdict(list)
        for s in ans:
            text = (q3_tone.get(s) or {}).get("text_in_image")
            if text:
                by_text["no text" if text == "none" else "text or logo"].append(s in right)
        by_kind = defaultdict(list)
        for s in ans:
            by_kind[pics[s]["kind"]].append(s in right)
        by_house = defaultdict(list)
        for s in ans:
            by_house[pics[s]["house"]].append(s in right)
        out[reader] = {"pictures": len(ans), "right": round(len(right) / max(1, len(ans)), 3),
                       "wrong": round(len(told - right) / max(1, len(ans)), 3),
                       "cannot_tell": round(1 - len(told) / max(1, len(ans)), 3),
                       "right_by_text": {k: {"pictures": len(v), "right": round(sum(v) / len(v), 3)} for k, v in by_text.items()},
                       "right_by_kind": {k: {"pictures": len(v), "right": round(sum(v) / len(v), 3)} for k, v in by_kind.items()},
                       "right_by_house": {h: round(sum(v) / len(v), 2) for h, v in sorted(by_house.items())},
                       "recognised": sorted(right)}
    return out


def probs_report(reader: str = "qwen3") -> dict:
    """The comparisons read as probabilities, judged by the yardsticks fixed before they were read: agreement with
    the person, two crops together, brands apart, and, in place of plain order consistency (which averaging the two
    orders passes by construction), whether the two orders point the same way once the reader's general lean
    towards one position is taken out."""
    rows = [r for r in store.read_jsonl(DIR / "bakeoff" / f"{reader}-probs.jsonl") if r.get("p_first") is not None]
    pics, crops, judged, hp = _bakeoff_frame()
    sh = sorted(pics)
    idx = {s: i for i, s in enumerate(sh)}
    house = [pics[s]["house"] for s in sh]
    nrng = np.random.default_rng(bakeoff.SEED)
    logit = lambda p: float(np.log(np.clip(p, 1e-4, 1 - 1e-4) / (1 - np.clip(p, 1e-4, 1 - 1e-4))))
    out = {"rows": len(rows), "lean_to_first": round(float(np.mean([r["p_first"] for r in rows])) - 0.5, 3) if rows else None,
           "axes": {}}
    for ax in sorted({r["axis"] for r in rows}):
        rr = [r for r in rows if r["axis"] == ax and r["first"] in idx and r["second"] in idx]
        lean = float(np.mean([logit(r["p_first"]) for r in rr]))
        by = defaultdict(dict)
        for r in rr:
            by[frozenset((r["first"], r["second"]))][(r["first"], r["second"])] = r["p_first"]
        both = {k: v for k, v in by.items() if len(v) == 2}
        same = 0
        pref = {}
        for k, v in both.items():
            (a, b), (c, d) = list(v)
            pa, pb = v[(a, b)], v[(c, d)]          # (c, d) is (b, a)
            za, zb = logit(pa) - lean, logit(pb) - lean
            same += (za > 0) != (zb > 0)            # one order says a, the other says a too
            pref[(a, b)] = (pa + (1 - pb)) / 2      # how strongly a is placed above b
        th, beta = bakeoff.bradley_terry(len(sh), np.array([idx[r["first"]] for r in rr]),
                                         np.array([idx[r["second"]] for r in rr]), np.array([r["p_first"] for r in rr]))
        agree, agree_pos = [], []
        for hid, ans in judged.items():
            h = hp[hid]
            if h["axis"] != ax:
                continue
            ben = h["left"] if ans == "left" else h["right"]
            other = h["right"] if ans == "left" else h["left"]
            if (ben, other) in pref:
                agree.append(float(pref[(ben, other)] > 0.5))
            elif (other, ben) in pref:
                agree.append(float(pref[(other, ben)] < 0.5))
            if ben in idx and other in idx:
                agree_pos.append(float(th[idx[ben]] > th[idx[other]]))
        icc = bakeoff._icc([(th[idx[a]], th[idx[b]]) for a, b in crops])
        eta = bakeoff._eta2(th, house, nrng)
        within = 1 - eta.get("between_brands", 0.0)
        cp = [abs(th[idx[a]] - th[idx[b]]) for a, b in crops]
        rp = [abs(th[i] - th[j]) for i, j in (nrng.choice(len(sh), 2, replace=False) for _ in range(2000))]
        out["axes"][ax] = {"pairs": len(both), "lean_logit": round(lean, 3), "first_bias": round(beta, 3),
                           "orders_agree_after_lean": round(same / max(1, len(both)), 3),
                           "crop_gap_vs_random": round(float(np.mean(cp)) / (float(np.mean(rp)) or 1), 3) if cp else None,
                           "with_person": {"pairs": len(agree), "agreement": round(float(np.mean(agree)), 3) if agree else None},
                           "with_person_by_position": round(float(np.mean(agree_pos)), 3) if agree_pos else None,
                           "crop_icc": icc, **eta,
                           "within_brand_signal": None if icc is None or within <= 0 else round(max(0.0, 1 - (1 - icc) / within), 3)}
    allv = [v["with_person"] for v in out["axes"].values() if v["with_person"]["agreement"] is not None]
    if allv:
        n = sum(a["pairs"] for a in allv)
        out["with_person_all"] = {"pairs": n, "agreement": round(sum(a["pairs"] * a["agreement"] for a in allv) / n, 3)}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m adtone.luxury")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("corpus")
    f = sub.add_parser("fetch")
    f.add_argument("--budget", type=float, default=150, help="minutes before no new page is started")
    r = sub.add_parser("read")
    r.add_argument("--reader", choices=sorted(APPS) + ["claude"], required=True)
    r.add_argument("--what", choices=["tone", "pairs", "probs", "comparisons", "check"], required=True)
    r.add_argument("--budget", type=float, default=280, help="minutes before no new batch is sent")
    e = sub.add_parser("embed")
    e.add_argument("--model", choices=sorted(bakeoff.EMBEDDERS), required=True)
    ps = sub.add_parser("positions")
    ps.add_argument("--reader", default="qwen3")
    bo = sub.add_parser("bakeoff")
    bo.add_argument("--reader", choices=sorted(APPS) + ["claude"], required=True)
    bo.add_argument("--what", choices=["tone", "pairs", "human"], required=True)
    rc = sub.add_parser("recognise")
    rc.add_argument("--reader", choices=sorted(APPS) + ["claude"], required=True)
    pb = sub.add_parser("probs")
    pb.add_argument("--reader", choices=sorted(APPS), default="qwen3")
    sub.add_parser("bakeoff-report")
    an = sub.add_parser("analyse")
    an.add_argument("--reader", default="qwen3")
    an.add_argument("--which", nargs="*", default=list(READINGS))
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
    elif a.cmd == "bakeoff":
        print(f"luxury bakeoff {a.reader} {a.what}: {bakeoff_read(a.reader, a.what)}")
    elif a.cmd == "recognise":
        print(f"luxury recognise {a.reader}: {recognise(a.reader)}")
    elif a.cmd == "probs":
        print(f"luxury probs {a.reader}: {bakeoff_probs(a.reader)}")
    elif a.cmd == "bakeoff-report":
        rep = {"generated_at": store.utc_now(), "recognition": recognition_report(), "probs": probs_report("qwen3")}
        (DIR / "bakeoff_extra.json").write_text(json.dumps(rep, indent=1, default=float) + "\n", encoding="utf-8")
        print("luxury bakeoff-report: " + json.dumps({k: (v.get("right") if isinstance(v, dict) else v)
                                                       for k, v in rep["recognition"].items()}))
    elif a.cmd == "analyse":
        out = analyse(a.reader, tuple(a.which))
        print("luxury analyse: " + ", ".join(f"{k}: {v.get('images', v.get('note'))}" for k, v in out["readings"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
