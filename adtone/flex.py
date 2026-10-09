"""The site's illustration of the clothes reader: one invented runway look, changed a step at a time in two
directions, each picture read by the clothes reader exactly as it reads a runway photograph.

    python -m adtone.flex start    # on the runner: the start picture drawn once for each seed of the plan,
                                   #   each read -> data/flex/start.json; the pictures to the private volume
                                   #   and, sealed, to a branch of their own
    python -m adtone.flex steps    # on the runner: each step of the plan drawn a few times from the picture
                                   #   before, held to its mask, and each version read; the version whose
                                   #   reading changes as the step intends, and least else, is kept and the
                                   #   next step drawn from it -> data/flex/frames.json (resumable)
    python -m adtone.flex shares   # each answer's share of two houses' runway looks -> data/flex/shares.json

The plan (data/flex/plan.json) holds the prompts, the seeds, the masks and what each step is meant to change.
The pictures are made with open image models licensed Apache 2.0 (bakeoff/modal_paint.py). None reaches the
repository, which is public: the copies go to the private Modal volume, the copies to look at leave the
runner only sealed to a key held outside the repository, and the repository keeps each picture's hash and
the reader's answers. The person in the pictures is invented by the image model and resembles no one on
purpose; the reader is told, as always, not to identify anyone.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
import tarfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, store

DIR = config.DATA / "flex"
PLAN = DIR / "plan.json"
MODELS = DIR / "models.json"
START = DIR / "start.json"
FRAMES = DIR / "frames.json"
SHARES = DIR / "shares.json"
PROV = config.PROV_DIR / "flex.jsonl"
LOCAL = config.ROOT / "flex_pictures"         # ignored by git; on the runner only
VOL_DIR = "/flex-v1"
APP = "adtone-paint"
SIZE = (832, 1248)                             # the edit model's own size for a 2:3 picture (about a megapixel)
REVIEW_EDGE = 1248
RUBRIC = "clothes-v1"
NOISY = {"street_couture_axis": 0.5}           # a change in these counts for less: the reader's own wobble


def _log(event: str, **kw) -> None:
    store.append_jsonl(PROV, [{"at": store.utc_now(), "event": event, **kw}])


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------- pictures ----------

def mask_png(shapes: list[dict], size: tuple[int, int] = SIZE) -> bytes:
    """The mask of a step: white where the picture may change. Shapes are boxes or ellipses in fractions of
    the picture's width and height, [left, top, right, bottom]; the mask is their union."""
    from PIL import Image, ImageDraw
    w, h = size
    img = Image.new("L", size, 0)
    d = ImageDraw.Draw(img)
    for s in shapes:
        (kind, box), = s.items()
        x0, y0, x1, y1 = box
        xy = [round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)]
        if kind == "box":
            d.rectangle(xy, fill=255)
        elif kind == "ellipse":
            d.ellipse(xy, fill=255)
        else:
            raise ValueError(f"unknown shape {kind!r}")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def to_size(png: bytes, size: tuple[int, int] = SIZE) -> bytes:
    from PIL import Image
    img = Image.open(io.BytesIO(png)).convert("RGB")
    if img.size != size:
        img = img.resize(size, Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=6)
    return buf.getvalue()


def read_copy(png: bytes) -> bytes:
    """The reader's copy, made as every runway look's is: the long side 896 pixels, JPEG quality 90."""
    from .bakeoff import READ_EDGE, _jpeg
    from PIL import Image
    return _jpeg(Image.open(io.BytesIO(png)), READ_EDGE, 90)


def review_copy(png: bytes) -> bytes:
    from .bakeoff import _jpeg
    from PIL import Image
    return _jpeg(Image.open(io.BytesIO(png)), REVIEW_EDGE, 92)


def sha_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------- what changed ----------

def standing() -> tuple[list[str], dict[str, list[str]]]:
    """The clothes questions in use, and for list questions the options in use (data/clothes/judge.json)."""
    from .clothes import JUDGE
    j = load(JUDGE)
    use = [q for q in j.get("use", []) if j.get("questions", {}).get(q, {}).get("status") != "dropped"]
    return use, j.get("options", {})


def answers(a: dict | None, use: list[str], options: dict[str, list[str]]) -> dict:
    """The answers to the questions in use; a list question as the set of its options in use."""
    out = {}
    for q in use:
        if q == "confidence" or a is None or q not in a:
            continue
        v = a[q]
        out[q] = frozenset(x for x in v if x in options.get(q, v)) if isinstance(v, list) else v
    return out


def changes(before: dict | None, after: dict | None, use: list[str], options: dict[str, list[str]]) -> list[str]:
    """Each answer that differs: 'q+option' and 'q-option' for list questions, 'q=new' for the rest."""
    b, a = answers(before, use, options), answers(after, use, options)
    out = []
    for q in use:
        if q not in a or q not in b:
            continue
        if isinstance(a[q], frozenset):
            out += [f"{q}+{o}" for o in sorted(a[q] - b[q])] + [f"{q}-{o}" for o in sorted(b[q] - a[q])]
        elif a[q] != b[q]:
            out.append(f"{q}={a[q]}")
    return out


def judge_step(before: dict | None, after: dict | None, expect: list[str], use: list[str],
               options: dict[str, list[str]]) -> dict:
    """How a version's reading answers what its step was meant to change: the changes meant and seen, those
    meant and not seen, and the changes not meant. 'q=v' is met when the answer is v, even if it was v
    before."""
    if after is None:
        return {"met": [], "missed": list(expect), "unmeant": [], "score": -99.0}
    seen = changes(before, after, use, options)
    a = answers(after, use, options)
    met, missed = [], []
    for e in expect:
        if "=" in e:
            q, v = e.split("=", 1)
            ok = a.get(q) == (int(v) if v.isdigit() else v)
        else:
            ok = e in seen
        (met if ok else missed).append(e)
    unmeant = [c for c in seen if c not in expect]
    weight = sum(NOISY.get(question_of(c), 1.0) for c in unmeant)
    return {"met": met, "missed": missed, "unmeant": unmeant, "score": round(3 * len(met) - 2 * len(missed) - weight, 2)}


def question_of(change: str) -> str:
    import re
    return re.split(r"[+=-]", change, maxsplit=1)[0]


def pick(cands: list[dict], keep: str | None = None) -> int:
    """The version kept: the one named in the plan, else the highest score, the fewest changes not meant,
    then the first drawn."""
    if keep:
        for i, c in enumerate(cands):
            if c["name"] == keep:
                return i
    return max(range(len(cands)), key=lambda i: (cands[i]["score"], -len(cands[i]["unmeant"]), -i))


# ---------- Modal ----------

def _volume():
    import modal
    from .bakeoff import VOLUME
    return modal.Volume.from_name(VOLUME, create_if_missing=True)


def put_files(files: dict[str, bytes]) -> None:
    vol = _volume()
    with vol.batch_upload(force=True) as batch:
        for path, data in files.items():
            batch.put_file(io.BytesIO(data), path)


def get_file(path: str) -> bytes:
    return b"".join(_volume().read_file(path))


def _painter(name: str):
    import modal
    return modal.Cls.from_name(APP, name)()


def _reader():
    """The clothes reader, Qwen3-VL-32B at the weights pinned in data/luxury/readers.json (the workflow
    deploys it to wait a quarter of an hour between the steps' readings rather than start again)."""
    import modal
    from .luxury import APPS
    return modal.Cls.from_name(APPS["qwen3"], "Reader")()


def read_all(reader, jpegs: list[bytes]) -> list[dict | None]:
    """Each picture read with the frozen clothes rubric, as the runway looks were."""
    from .clothes import _parse
    from .score import json_schema, load_rubric
    rub = load_rubric(RUBRIC)
    texts = reader.read.remote(jpegs, rub.prompt, json_schema(rub))
    out = []
    for t in texts:
        try:
            out.append(_parse(t, rub)[0])
        except Exception as e:
            _log("unread", error=f"{type(e).__name__}: {str(e)[:200]}", text=(t or "")[:200])
            out.append(None)
    return out


def seal(review: dict[str, bytes], local: Path | None = None) -> Path:
    """The pictures to look at, as one tar sealed to the key outside the repository."""
    from nacl.public import PublicKey, SealedBox
    from .bakeoff import SEAL_KEY
    local = local or LOCAL
    local.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in sorted(review.items()):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    key = PublicKey(base64.b64decode(SEAL_KEY.read_text().strip()))
    out = local / "flex.tar.sealed"
    out.write_bytes(SealedBox(key).encrypt(buf.getvalue()))
    return out


# ---------- the start picture ----------

def start(plan: dict | None = None, starter=None, reader=None, put=None) -> dict:
    """The start picture drawn once for each seed, brought to the working size, each read."""
    plan = plan or load(PLAN)
    st = plan["start"]
    starter = starter or _painter("Starter")
    reader = reader or _reader()
    put = put or put_files
    t0 = time.monotonic()
    pngs = starter.make.remote(st["prompt"], st["negative"], st["seeds"], st["draw_size"][0], st["draw_size"][1],
                               st["steps"], st["cfg"])
    files, review, rows = {}, {}, []
    for seed, png in zip(st["seeds"], pngs):
        png = to_size(png)
        jpg = read_copy(png)
        name = f"start-{seed}"
        files[f"{VOL_DIR}/png/{name}.png"] = png
        files[f"{VOL_DIR}/{sha_of(jpg)}.jpg"] = jpg
        review[f"{name}.jpg"] = review_copy(png)
        rows.append({"name": name, "seed": seed, "sha": sha_of(jpg), "png_sha": sha_of(png)})
    put(files)
    seal(review)
    out = {"version": plan.get("version"), "at": store.utc_now(), "models": load(MODELS),
           "prompt": st["prompt"], "negative": st["negative"], "draw_size": st["draw_size"], "size": list(SIZE),
           "steps": st["steps"], "cfg": st["cfg"], "candidates": rows}
    save(START, out)
    for row, rd in zip(rows, read_all(reader, [files[f"{VOL_DIR}/{r['sha']}.jpg"] for r in rows])):
        row["reading"] = rd
    save(START, out)
    _log("start", drawn=len(rows), read=sum(1 for r in rows if r.get("reading")), seconds=round(time.monotonic() - t0))
    return out


# ---------- the steps ----------

def _step_key(prev_png: bytes, st: dict, plan: dict) -> str:
    """What a step's pictures depend on: the picture before and everything the plan says about drawing it."""
    parts = {k: st.get(k) for k in ("prompt", "mask", "seeds")}
    parts.update(prev=sha_of(prev_png), negative=plan.get("negative"), steps=plan.get("edit_steps"),
                 cfg=plan.get("cfg"), feather=plan.get("feather"), draws=plan.get("draws"), seed=plan.get("draw_seed"),
                 models=load(MODELS).get("edit"))
    return sha_of(json.dumps(parts, sort_keys=True).encode())[:16]


def steps(plan: dict | None = None, editor=None, reader=None, put=None, get=None, budget_min: float = 120) -> dict:
    """Both directions of the plan, each step drawn from the version kept at the step before. A step already
    drawn from the same picture with the same plan is not drawn again; one whose plan changed is drawn
    again, and so is every step after it in that direction."""
    plan = plan or load(PLAN)
    editor = editor or _painter("Editor")
    reader = reader or _reader()
    put, get = put or put_files, get or get_file
    use, options = standing()
    first = plan["start"]["chosen"]
    st0 = next(c for c in load(START)["candidates"] if c["name"] == first)
    frames = load(FRAMES)
    if frames.get("start", {}).get("name") != first:
        frames = {"version": plan.get("version"), "arms": {}}
    frames.update(start={"name": first, "sha": st0["sha"], "reading": st0["reading"]}, models=load(MODELS),
                  size=list(SIZE))
    base = get(f"{VOL_DIR}/png/{first}.png")
    deadline = time.monotonic() + budget_min * 60
    lock = threading.Lock()
    review: dict[str, bytes] = {}
    seeds_default = [plan.get("draw_seed", 1) + k for k in range(plan.get("draws", 3))]

    def arm(name: str, todo: list[dict]):
        prev_png, prev_name, prev_read = base, first, st0["reading"]
        kept = frames["arms"].setdefault(name, {})
        for st in todo:
            key = _step_key(prev_png, st, plan)
            rec = kept.get(st["id"])
            if rec and rec.get("key") == key:      # drawn already: judged again, in case the plan's aims changed
                for c in rec["candidates"]:
                    c.update(judge_step(prev_read, c["reading"], st["expect"], use, options))
                i = pick(rec["candidates"], st.get("keep"))
                c = rec["candidates"][i]
                with lock:
                    rec.update(chosen=c["name"], expect=st["expect"])
                prev_png, prev_name, prev_read = get(f"{VOL_DIR}/png/{c['name']}.png"), c["name"], c["reading"]
                continue
            if time.monotonic() > deadline:
                _log("steps_stopped", arm=name, at=st["id"], why="budget")
                return
            seeds = st.get("seeds") or seeds_default
            pngs = editor.edit.remote(prev_png, mask_png(st["mask"]), st["prompt"], plan["negative"], seeds,
                                      plan["edit_steps"], plan["cfg"], plan["feather"])
            jpgs = [read_copy(p) for p in pngs]
            reads = read_all(reader, jpgs)
            files, cands = {}, []
            for seed, png, jpg, rd in zip(seeds, pngs, jpgs, reads):
                cname = f"{st['id']}-{seed}"
                files[f"{VOL_DIR}/png/{cname}.png"] = png
                files[f"{VOL_DIR}/{sha_of(jpg)}.jpg"] = jpg
                cands.append({"name": cname, "seed": seed, "sha": sha_of(jpg), "png_sha": sha_of(png), "reading": rd,
                              **judge_step(prev_read, rd, st["expect"], use, options)})
                with lock:
                    review[f"{cname}.jpg"] = review_copy(png)
            put(files)
            i = pick(cands, st.get("keep"))
            rec = {"id": st["id"], "from": prev_name, "key": key, "prompt": st["prompt"], "mask": st["mask"],
                   "expect": st["expect"], "candidates": cands, "chosen": cands[i]["name"], "at": store.utc_now()}
            with lock:
                kept[st["id"]] = rec
                for later in [s["id"] for s in todo[todo.index(st) + 1:]]:
                    kept.pop(later, None)
                save(FRAMES, frames)
            _log("step", arm=name, id=st["id"], chosen=cands[i]["name"], scores=[c["score"] for c in cands])
            prev_png, prev_name, prev_read = pngs[i], cands[i]["name"], cands[i]["reading"]

    errors = []
    try:
        with ThreadPoolExecutor(max_workers=len(plan["arms"])) as ex:
            futs = {name: ex.submit(arm, name, todo) for name, todo in plan["arms"].items()}
            for name, f in futs.items():
                try:
                    f.result()
                except Exception as e:     # one direction failing leaves the other's pictures standing
                    errors.append(f"{name}: {type(e).__name__}: {str(e)[:400]}")
                    _log("arm_failed", arm=name, error=errors[-1])
    finally:
        frames["at"] = store.utc_now()
        save(FRAMES, frames)
        chosen = [r["chosen"] for a in frames["arms"].values() for r in a.values()]
        for name in [first] + chosen:      # the kept versions at full quality, to make the site's copies from
            try:
                review[f"kept/{name}.png"] = get(f"{VOL_DIR}/png/{name}.png")
            except Exception as e:
                _log("missing", name=name, error=f"{type(e).__name__}")
        seal(review)
    if errors:
        raise RuntimeError("; ".join(errors))
    return frames


# ---------- the houses' shares ----------

def house_answers(spec: dict) -> list[dict]:
    """The answers for the runway looks of one house's line between two dates (designer's tenure), each look
    showing an outfit, from the pages of the show's own line."""
    from . import clothes as C
    rows = store.read_jsonl(C.DIR / "runway.jsonl") if (C.DIR / "runway.jsonl").exists() else []
    read = C.readings()
    out = []
    for r in rows:
        if (r.get("house") == spec["house"] and r.get("category") == spec["category"]
                and spec["from"] <= r.get("date", "") <= spec["to"] and C.page_fits(r.get("page", ""), r.get("category", ""))):
            a = read.get(r["sha"])
            if a and C.shows_outfit(a):
                out.append({"date": r["date"], "season": r.get("season"), **a})
    return out


def shares(plan: dict | None = None, write: bool = True) -> dict:
    """For every answer in use, the share of each house's looks giving it (for a list question, showing the
    option), with the looks and shows it rests on."""
    plan = plan or load(PLAN)
    use, options = standing()
    from .score import load_rubric
    spec = load_rubric(RUBRIC).spec
    out = {"version": plan.get("version"), "at": store.utc_now(), "houses": {}}
    for h in plan["houses"]:
        looks = house_answers(h)
        n = len(looks)
        sh = {}
        for q in use:
            if q in ("confidence", "subject"):
                continue
            if q in spec["lists"]:
                for o in options.get(q, spec["lists"][q]["options"]):
                    k = sum(1 for a in looks if o in (a.get(q) or []))
                    sh[f"{q}+{o}"] = [k, n]
            else:
                vals = spec["enums"].get(q) or list(range(spec["integers"][q]["min"], spec["integers"][q]["max"] + 1))
                for v in vals:
                    k = sum(1 for a in looks if a.get(q) == v)
                    sh[f"{q}={v}"] = [k, n]
        out["houses"][h["house"]] = {**h, "looks": n, "shows": len({a["date"] for a in looks}),
                                     "seasons": sorted({a["season"] for a in looks if a.get("season")}), "shares": sh}
    if write:
        save(SHARES, out)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m adtone.flex")
    ap.add_argument("what", choices=["start", "steps", "shares"])
    ap.add_argument("--budget-min", type=float, default=120)
    a = ap.parse_args(argv)
    try:
        if a.what == "start":
            r = start()
            print(json.dumps({"drawn": len(r["candidates"])}))
        elif a.what == "steps":
            r = steps(budget_min=a.budget_min)
            print(json.dumps({arm: list(v) for arm, v in r["arms"].items()}))
        else:
            r = shares()
            print(json.dumps({h: v["looks"] for h, v in r["houses"].items()}))
    except Exception as e:
        _log("failed", what=a.what, error=f"{type(e).__name__}: {str(e)[:600]}")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
