"""Bake-off: which reader and which image model read luxury homepage pictures best.

Exploratory, not in the pre-registration. The registered instrument (tone-v1 read by Qwen2.5-VL-7B at
its pinned weights) is untouched; this decides what a second, exploratory instrument should be.

    python -m adtone.bakeoff sample     # choose about 300 pictures          -> data/bakeoff/sample.json
    python -m adtone.bakeoff fetch      # fetch them again from the archive -> data/bakeoff/pictures.jsonl,
                                        #   small copies to a private Modal volume, sealed thumbnails
    python -m adtone.bakeoff pairs      # the pairs to judge                -> data/bakeoff/pairs.json
    python -m adtone.bakeoff read --reader qwen3|qwen25|claude --what tone|pairs|human
    python -m adtone.bakeoff embed --model csd|dino|fashion
    python -m adtone.bakeoff score      #                                   -> data/results/bakeoff.json

Candidates:
  readers   Qwen2.5-VL-7B (today's), Qwen3-VL-32B (open, pinned), Claude (closed, the check)
  questions tone-v1 as it stands, and pairs-v1: five axes judged by comparing two luxury pictures
  images   today's fingerprint (OpenCLIP ViT-B/32), CSD style descriptors, DINO, Marqo FashionSigLIP
  pixels   colour and light measured from the picture itself

Judged by: whether answers vary at all (no defaulting); whether two crops of one picture get the same
answer; whether the reading tells brands apart; and how often it agrees with a person's judgement of
the same pairs. No picture is ever written to the repository: the repository is public. Small copies
live on a private Modal volume, and the thumbnails for the judging page leave the runner only sealed
to a key held outside the repository.

How the winner is chosen, fixed on 7 October 2026 before any reading came back:
  pairs     A reader's agreement with the person is the share of the person's pairs (those marked
            unsure left out) where the reader, asked in both orders, picks the picture the person
            picked; a reader that contradicts itself across the two orders scores a half. The open
            reader (Qwen3-VL-32B) reads the axes for the luxury reading if its agreement is no more than
            five points below Claude's; otherwise Claude reads them and the open reader is the check.
            An axis is kept when, under that reader, the two orders agree on at least three pairs in
            four, two crops of one picture sit closer on it than two random pictures (gap under one
            half), and agreement with the person on it is at least 60%. If no reader reaches 60% over
            all the pairs, the axes are rewritten before anything is read with them.
  questions Under the chosen reader, a tone-v1 question is kept when its commonest answer covers less
            than 90% of pictures and two crops of one picture get the same answer beyond chance (kappa
            at least 0.4), as adtone/readings.py already requires.
  images    Today's fingerprint stays unless another image model finds more brands nearest their own
            earlier pictures and also puts more pictures among their own brand's nearest neighbours.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import itertools
import json
import math
import os
import random
import re
import sys
import tarfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import config, store

DIR = config.DATA / "bakeoff"
RESULTS = config.RESULTS_DIR / "bakeoff.json"
PROV = config.DATA / "provenance" / "bakeoff.jsonl"
LOCAL = config.ROOT / "bakeoff_pictures"        # ignored by git; on the runner only
VOLUME = "adtone-pictures"                       # private Modal volume
VOL_DIR = "/bakeoff-v1"
SEAL_KEY = config.ROOT / "bakeoff" / "sealing_key.pub"
RELEASE = "bakeoff-v1"
SPLIT = "2025-07"                                # before and after, as in the identity check
PER_SIDE = 6
CROP_PAIRS = 30
HUMAN_PER_AXIS = 30
HUMAN_WITHIN = 10
DEGREE = 6                                       # comparisons per picture per axis for the readers
READ_EDGE = 896
THUMB_EDGE = 420
SEED = 20261007


def _pairs_rubric() -> dict:
    """pairs-v1: the prompt and the five axes, checked against its frozen hash."""
    from .score import _PROMPT, _SPEC, check_frozen
    path = config.RUBRIC_DIR / "pairs-v1.md"
    check_frozen(path, config.RUBRIC_DIR / "pairs-v1.sha256")
    text = path.read_text(encoding="utf-8")
    spec = json.loads(_SPEC.search(text).group(1))
    spec["prompt"] = _PROMPT.search(text).group(1)
    return spec


def question(axis: dict, spec: dict) -> str:
    return spec["question"].format(away=axis["away"], toward=axis["toward"], away_cap=axis["away"].capitalize(),
                                   toward_cap=axis["toward"].capitalize(), away_means=axis["away_means"],
                                   toward_means=axis["toward_means"])


def _log(event: str, **kw) -> None:
    store.append_jsonl(PROV, [{"at": store.utc_now(), "event": event, **kw}])


# ---------- the sample ----------

def choose(images: list[dict], dup_pairs: list[tuple[dict, dict]], captures: dict[tuple, dict],
           seed: int = SEED, per_side: int = PER_SIDE, n_crops: int = CROP_PAIRS) -> dict:
    """About 300 pictures: for every brand with enough pictures both before and after July 2025, six
    from each side (campaign pictures and product on a model in proportion), plus pairs of crops of one
    picture. Each picture once, at the month it was first shown."""
    rng = random.Random(seed)
    first: dict[tuple, dict] = {}
    for im in sorted(images, key=lambda i: (i["month"], i["sha"])):
        first.setdefault((im["house"], im["sha"]), im)
    by: dict[str, dict[str, list]] = defaultdict(lambda: {"early": [], "late": []})
    for im in first.values():
        by[im["house"]]["early" if im["month"] < SPLIT else "late"].append(im)
    chosen: dict[str, dict] = {}
    for h in sorted(by):
        if min(len(by[h]["early"]), len(by[h]["late"])) < per_side:
            continue
        for side in ("early", "late"):
            pool = sorted(by[h][side], key=lambda i: i["sha"])
            rng.shuffle(pool)
            camp = [i for i in pool if i["type"] == "campaign"]
            mod = [i for i in pool if i["type"] != "campaign"]
            k_c = round(per_side * len(camp) / len(pool))
            pick = camp[:k_c] + mod[:per_side - k_c]
            pick += [i for i in pool if i not in pick][:per_side - len(pick)]
            for i in pick:
                chosen[i["sha"]] = {**i, "side": side}
    crops = []
    pairs = sorted(dup_pairs, key=lambda p: (p[0]["sha"], p[1]["sha"]))
    rng.shuffle(pairs)
    for a, b in pairs:
        if len(crops) >= n_crops:
            break
        if a["sha"] == b["sha"]:
            continue
        crops.append([a["sha"], b["sha"]])
        for i in (a, b):
            chosen.setdefault(i["sha"], {**i, "side": "early" if i["month"] < SPLIT else "late"})
    out = []
    for sha, i in sorted(chosen.items(), key=lambda kv: (kv[1]["house"], kv[1]["month"], kv[0])):
        cap = captures.get((i["house"], i["month"]), {})
        out.append({"sha": sha, "house": i["house"], "month": i["month"], "kind": i["type"], "side": i["side"],
                    "capture": cap.get("capture"), "page": cap.get("page"), "url": cap.get("url")})
    return {"seed": seed, "split": SPLIT, "per_side": per_side, "pictures": out, "crop_pairs": crops,
            "brands": sorted({p["house"] for p in out})}


def sample(write: bool = True) -> dict:
    from . import character, homepages, readings
    images = [i for i in readings.load_images() if i["type"] in readings.LED and i["vec"] is not None]
    P = homepages.paths()
    caps = {}
    for p in sorted(P["captures"].glob("*.jsonl")):
        for row in store.read_jsonl(p):
            if row.get("status") == "resolved":
                caps[(row["house_id"], row["month"])] = row
    dups = readings.duplicate_pairs(readings.once(images, lambda m: m))
    s = choose(images, dups, caps)
    if write:
        DIR.mkdir(parents=True, exist_ok=True)
        (DIR / "sample.json").write_text(json.dumps(s, indent=1) + "\n", encoding="utf-8")
    _log("sample", pictures=len(s["pictures"]), brands=len(s["brands"]), crop_pairs=len(s["crop_pairs"]))
    return s


# ---------- colour and light from the pixels ----------

def _lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB in 0..1, shape (n, 3), to CIELAB (D65)."""
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = (lin @ m.T) / np.array([0.95047, 1.0, 1.08883])
    d = 6 / 29
    f = np.where(xyz > d ** 3, np.cbrt(xyz), xyz / (3 * d * d) + 4 / 29)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], axis=1)


def pixel_measures(img) -> dict:
    """Colour and light that need no judgement, on the picture scaled to at most 256 pixels a side.
    lightness and contrast: mean and spread of CIELAB L* (0 to 1); high_key and low_key: share of
    pixels above L* 80 and below 25; chroma: mean C* / 100; colourful: share of pixels with C* above 20;
    warmth: among all pixels, the share with a warm hue (reds to yellows) less the share with a cool
    hue (greens to blues), counting only pixels with some colour (C* above 8); hue_spread: circular
    spread of hue among coloured pixels (0 one hue, 1 all hues); colourfulness: Hasler and
    Suesstrunk's measure / 100."""
    from PIL import Image
    im = img.convert("RGB").copy()
    im.thumbnail((256, 256), Image.Resampling.BILINEAR)
    rgb = np.asarray(im, dtype=np.float64).reshape(-1, 3) / 255.0
    lab = _lab(rgb)
    L, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    C = np.hypot(a, b)
    hue = np.degrees(np.arctan2(b, a)) % 360
    coloured = C > 8
    warm = coloured & ((hue >= 330) | (hue < 100))
    cool = coloured & (hue >= 150) & (hue < 300)
    if coloured.any():
        ang = np.radians(hue[coloured])
        spread = 1 - float(np.hypot(np.cos(ang).mean(), np.sin(ang).mean()))
    else:
        spread = 0.0
    R, G, B = (rgb * 255).T
    rg, yb = R - G, 0.5 * (R + G) - B
    cf = math.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * math.sqrt(rg.mean() ** 2 + yb.mean() ** 2)
    r = lambda x: round(float(x), 4)
    return {"lightness": r(L.mean() / 100), "contrast": r(L.std() / 100), "high_key": r((L > 80).mean()),
            "low_key": r((L < 25).mean()), "chroma": r(C.mean() / 100), "colourful": r((C > 20).mean()),
            "warmth": r(warm.mean() - cool.mean()), "hue_spread": r(spread), "colourfulness": r(cf / 100)}


PIXEL_KEYS = ["lightness", "contrast", "high_key", "low_key", "chroma", "colourful", "warmth", "hue_spread", "colourfulness"]


# ---------- fetching the pictures again ----------

def _jpeg(img, edge: int, quality: int) -> bytes:
    from PIL import Image
    im = img.convert("RGB").copy()
    im.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _places(p: dict) -> list[tuple]:
    """Where a picture was seen: the capture of its first showing, then any later captures listed under
    "also". Older capture rows kept only the address asked for, not the page it led to."""
    first = (p.get("capture"), p.get("page") or p.get("url"))
    return [c for c in [first] + [tuple(c) for c in p.get("also", [])] if c[0] and c[1]]


def fetch(s: dict | None = None, pause: float = 0.4, workers: int = 4, budget_min: float = 110,
          session_factory=None, out: Path | None = None, local: Path | None = None, thumbs: bool = True,
          present: set | None = None, log=None, rounds: int = 3) -> list[dict]:
    """Each picture fetched again from the archived page where it was found: the page's picture
    addresses are read again, and a picture is kept when its bytes hash to the recorded sha. Kept: a
    copy for the readers (at most 896 pixels a side) and, for the judging page, a thumbnail, on the
    runner only, and the pixel measures, which go into the repository. A picture not found where it was
    first shown is sought where it was shown later, up to `rounds` places. Pictures found by an earlier
    run are not fetched again (when `present` is given, only those whose copy is known to be kept); a
    few workers share the archive politely; past the time budget no new page is started, and what was
    found is written whatever happens."""
    import threading
    import requests
    from . import homepages, media
    s = s or json.loads((DIR / "sample.json").read_text(encoding="utf-8"))
    prev_path = out or DIR / "pictures.jsonl"
    local_dir = local or LOCAL
    log = log or _log
    prev = {r["sha"]: r for r in store.read_jsonl(prev_path)} if prev_path.exists() else {}
    have = {sha for sha, r in prev.items() if r.get("found") and (present is None or sha in present)}
    (local_dir / "read").mkdir(parents=True, exist_ok=True)
    if thumbs:
        (local_dir / "thumb").mkdir(parents=True, exist_ok=True)
    found: dict[str, dict] = {}
    deadline = time.monotonic() + budget_min * 60
    local = threading.local()

    def sess():
        if not hasattr(local, "s"):
            local.s = session_factory() if session_factory else requests.Session()
            local.s.headers.setdefault("User-Agent", media.UA)
        return local.s

    def get(url):
        for attempt in range(4):
            try:
                r = sess().get(url, timeout=45)
            except requests.RequestException:
                r = None
            if r is not None and (r.status_code == 200 or r.status_code not in (429, 500, 502, 503, 504)):
                return r
            time.sleep(min(60, 5 * 2 ** attempt))
        return None

    def work(item):
        (ts, page), shas = item
        got = {}
        if time.monotonic() > deadline:
            return got
        r = get(homepages.REPLAY.format(ts=ts, url=page))
        time.sleep(pause)
        if r is None or r.status_code != 200:
            return got
        urls = homepages.page_images(r.text, homepages.final_url(getattr(r, "url", "") or "", page), ts)
        left = set(shas)
        for u in urls[:homepages.IMAGE_CANDIDATES + 4]:
            if not left or time.monotonic() > deadline + 300:
                break
            ri = get(u)
            time.sleep(pause)
            if ri is None or ri.status_code != 200 or len(ri.content) > config.MAX_IMAGE_BYTES:
                continue
            sha = hashlib.sha256(ri.content).hexdigest()
            if sha not in left:
                continue
            try:
                img = media.open_image(ri.content)
            except media.MediaError:
                continue
            (local_dir / "read" / f"{sha}.jpg").write_bytes(_jpeg(img, READ_EDGE, 90))
            if thumbs:
                (local_dir / "thumb" / f"{sha}.jpg").write_bytes(_jpeg(img, THUMB_EDGE, 80))
            got[sha] = {"sha": sha, "found": True, "capture": ts, "w": img.width, "h": img.height,
                        "pixel": pixel_measures(img)}
            left.discard(sha)
        return got

    pages = 0
    try:
        for rnd in range(rounds):
            want: dict[tuple, set] = defaultdict(set)
            for p in s["pictures"]:
                where = _places(p)
                if p["sha"] not in have and p["sha"] not in found and rnd < len(where):
                    want[where[rnd]].add(p["sha"])
            if not want or time.monotonic() > deadline:
                break
            pages += len(want)
            with ThreadPoolExecutor(max(1, workers)) as ex:
                for got in ex.map(work, sorted(want.items(), key=lambda kv: kv[0])):
                    found.update(got)
    finally:
        rows = [prev[p["sha"]] if p["sha"] in have else found.get(p["sha"], {"sha": p["sha"], "found": False})
                for p in s["pictures"]]
        store.write_jsonl(prev_path, rows)
        log("fetch", pictures=len(rows), found=sum(r["found"] for r in rows), this_run=len(found),
            pages=pages, out_of_time=time.monotonic() > deadline)
    return rows


def to_volume(local: Path | None = None, vol_dir: str = VOL_DIR, files: list[Path] | None = None) -> int:
    """The readers' copies to the private Modal volume."""
    import modal
    vol = modal.Volume.from_name(VOLUME, create_if_missing=True)
    files = sorted(((local or LOCAL) / "read").glob("*.jpg")) if files is None else files
    if not files:
        return 0
    with vol.batch_upload(force=True) as batch:
        for f in files:
            batch.put_file(str(f), f"{vol_dir}/{f.name}")
    return len(files)


def on_volume(vol_dir: str = VOL_DIR) -> set[str]:
    """The pictures whose readers' copy is on the volume."""
    import modal
    vol = modal.Volume.from_name(VOLUME, create_if_missing=True)
    try:
        entries = vol.listdir(vol_dir)
    except Exception:   # nothing there yet
        return set()
    return {Path(e.path).stem for e in entries if str(e.path).endswith(".jpg")}


def from_volume(shas: list[str], vol_dir: str = VOL_DIR) -> dict[str, bytes]:
    import modal
    vol = modal.Volume.from_name(VOLUME)
    out = {}
    for sha in shas:
        try:
            out[sha] = b"".join(vol.read_file(f"{vol_dir}/{sha}.jpg"))
        except Exception:   # a picture the fetch did not find
            continue
    return out


def seal_thumbnails() -> Path:
    """The judging page's thumbnails as one tar, sealed to the public key in bakeoff/sealing_key.pub:
    only the holder of the private key, which is not in the repository, can open it."""
    from nacl.public import PublicKey, SealedBox
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for f in sorted((LOCAL / "thumb").glob("*.jpg")):
            tar.add(str(f), arcname=f.name)
    key = PublicKey(base64.b64decode(SEAL_KEY.read_text().strip()))
    out = LOCAL / "thumbs.tar.sealed"
    out.write_bytes(SealedBox(key).encrypt(buf.getvalue()))
    return out


def seal_from_volume() -> Path:
    """Thumbnails made again from the readers' copies on the volume, sealed, for when the fetch's own
    thumbnails are gone with its runner."""
    from PIL import Image
    found = [r["sha"] for r in store.read_jsonl(DIR / "pictures.jsonl") if r.get("found")]
    (LOCAL / "thumb").mkdir(parents=True, exist_ok=True)
    for sha, data in from_volume(found).items():
        (LOCAL / "thumb" / f"{sha}.jpg").write_bytes(_jpeg(Image.open(io.BytesIO(data)), THUMB_EDGE, 80))
    return seal_thumbnails()


# ---------- the pairs ----------

def design(pictures: list[dict], crops: list[list[str]], axes: list[dict], seed: int = SEED,
           human_per_axis: int = HUMAN_PER_AXIS, human_within: int = HUMAN_WITHIN, degree: int = DEGREE) -> dict:
    """For a person: on each axis, 30 pairs, 10 of them within one brand, no picture more than twice,
    and never two crops of one picture; the side each picture is shown on is drawn at random. For the
    readers: on each axis every picture against `degree` others (a circulant design on a random order of
    the pictures, so each picture has exactly that many comparisons), plus the person's pairs."""
    rng = random.Random(seed)
    shas = sorted(p["sha"] for p in pictures)
    house = {p["sha"]: p["house"] for p in pictures}
    crop = {frozenset(c) for c in crops}
    human, reader = [], {}
    for ax in axes:
        used: Counter = Counter()
        got: set = set()
        within = across = 0
        tries = 0
        while within + across < human_per_axis and tries < 100000:
            tries += 1
            a, b = rng.sample(shas, 2)
            key = frozenset((a, b))
            if key in got or key in crop or used[a] >= 2 or used[b] >= 2:
                continue
            same = house[a] == house[b]
            if same and within >= human_within or not same and across >= human_per_axis - human_within:
                continue
            got.add(key)
            used[a] += 1
            used[b] += 1
            within += same
            across += not same
            left, right = (a, b) if rng.random() < 0.5 else (b, a)
            human.append({"id": f"{ax['id']}-{len([h for h in human if h['axis'] == ax['id']]) + 1:02d}",
                          "axis": ax["id"], "left": left, "right": right, "same_brand": same})
        order = shas[:]
        rng.shuffle(order)
        n = len(order)
        offsets = rng.sample(range(1, n // 2), degree // 2)
        edges = set()
        for d in offsets:
            for i in range(n):
                e = frozenset((order[i], order[(i + d) % n]))
                if len(e) == 2 and e not in crop:
                    edges.add(e)
        edges |= {frozenset((h["left"], h["right"])) for h in human if h["axis"] == ax["id"]}
        reader[ax["id"]] = sorted(sorted(e) for e in edges)
    return {"seed": seed, "human": human, "reader": reader}


def pairs(write: bool = True, redesign: bool = False) -> dict:
    """The pairs, designed once: a person may already be judging them, so a later fetch keeps them."""
    if (DIR / "pairs.json").exists() and not redesign:
        return json.loads((DIR / "pairs.json").read_text(encoding="utf-8"))
    s = json.loads((DIR / "sample.json").read_text(encoding="utf-8"))
    found = {r["sha"] for r in store.read_jsonl(DIR / "pictures.jsonl") if r.get("found")}
    pics = [p for p in s["pictures"] if p["sha"] in found]
    crops = [c for c in s["crop_pairs"] if c[0] in found and c[1] in found]
    d = design(pics, crops, _pairs_rubric()["axes"])
    if write:
        (DIR / "pairs.json").write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
    _log("pairs", human=len(d["human"]), reader={k: len(v) for k, v in d["reader"].items()})
    return d


# ---------- Bradley and Terry ----------

def bradley_terry(n: int, first: np.ndarray, second: np.ndarray, y: np.ndarray, ridge: float = 0.1,
                  iters: int = 500) -> tuple[np.ndarray, float]:
    """Positions theta (mean zero) and a first-position bias beta from judgements where y is 1 when the
    picture shown first was chosen, 0 when the second was: P(first chosen) = logistic(theta_first -
    theta_second + beta). A small ridge keeps a picture that always wins finite. Newton steps."""
    theta, beta = np.zeros(n), 0.0
    for _ in range(iters):
        z = theta[first] - theta[second] + beta
        p = 1 / (1 + np.exp(-z))
        r = y - p
        w = p * (1 - p)
        g = np.bincount(first, r, n) - np.bincount(second, r, n) - ridge * theta
        gb = r.sum()
        hd = np.bincount(first, w, n) + np.bincount(second, w, n) + ridge
        theta_new = theta + g / hd
        beta_new = beta + gb / max(w.sum(), 1e-9)
        if np.max(np.abs(theta_new - theta)) < 1e-7 and abs(beta_new - beta) < 1e-7:
            theta, beta = theta_new, beta_new
            break
        theta, beta = theta_new, beta_new
    return theta - theta.mean(), float(beta)


# ---------- the readers ----------

READERS = {
    "qwen3": {"model": "Qwen/Qwen3-VL-32B-Instruct-FP8", "app": "adtone-bakeoff-qwen3", "gpu": "H100"},
    "qwen25": {"model": "Qwen/Qwen2.5-VL-7B-Instruct", "app": "adtone-bakeoff-qwen25", "gpu": "L4"},
    "claude": {"model": config.CLAUDE_MODEL},
}


def _modal_reader(name: str):
    import modal
    return modal.Cls.from_name(READERS[name]["app"], "Reader")()


def read_tone(name: str, jpegs: dict[str, bytes], batch: int = 16) -> list[dict]:
    """tone-v1, the registered questions, read by another reader on the same pictures."""
    from .score import ClaudeScorer, ScoreError, gbnf, json_schema, load_rubric, parse
    rub = load_rubric()
    shas = sorted(jpegs)
    out = []
    if name == "claude":
        sc = ClaudeScorer(rub)

        def one(sha):
            try:
                return {"sha": sha, "out": sc.score(jpegs[sha])}
            except ScoreError as e:
                return {"sha": sha, "error": str(e)[:200]}
        with ThreadPoolExecutor(4) as ex:
            out = list(ex.map(one, shas))
        return out
    rd = _modal_reader(name)
    schema, grammar = json_schema(rub), gbnf(rub)
    for i in range(0, len(shas), batch):
        part = shas[i:i + batch]
        texts = rd.read.remote([jpegs[s] for s in part], rub.prompt, schema, grammar)
        for sha, t in zip(part, texts):
            try:
                out.append({"sha": sha, "out": parse(t, rub)})
            except ScoreError as e:
                out.append({"sha": sha, "error": str(e)[:200], "text": t[:300]})
    return out


def _claude_pair(client, model: str, system: str, a: bytes, b: bytes, q: str) -> str:
    content = [{"type": "text", "text": "First picture:"},
               {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(a).decode()}},
               {"type": "text", "text": "Second picture:"},
               {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(b).decode()}},
               {"type": "text", "text": q}]
    for attempt in range(6):
        try:
            r = client.messages.create(model=model, max_tokens=5, temperature=0, system=system,
                                       messages=[{"role": "user", "content": content}])
            return "".join(getattr(x, "text", "") for x in r.content)
        except Exception as e:  # retry on overload and rate limits only
            status = getattr(e, "status_code", None)
            if status not in (408, 409, 429, 500, 502, 503, 504, 529) and e.__class__.__name__ not in (
                    "APIConnectionError", "APITimeoutError"):
                raise
            time.sleep(min(90, 4 * 2 ** attempt) + random.random())
    raise RuntimeError("Claude did not answer")


def answer_of(text: str) -> str | None:
    m = re.search(r"\b(first|second)\b", (text or "").lower())
    return m.group(1) if m else None


def read_pairs(name: str, jpegs: dict[str, bytes], jobs: list[tuple[str, str, str]], batch: int = 24) -> list[dict]:
    """Each job (axis, first, second) is asked as it stands; callers pass both orders."""
    spec = _pairs_rubric()
    axes = {a["id"]: a for a in spec["axes"]}
    jobs = [j for j in jobs if j[1] in jpegs and j[2] in jpegs]
    out = []
    if name == "claude":
        import anthropic
        client = anthropic.Anthropic()

        def one(j):
            ax, a, b = j
            try:
                t = _claude_pair(client, READERS["claude"]["model"], spec["prompt"], jpegs[a], jpegs[b], question(axes[ax], spec))
                return {"axis": ax, "first": a, "second": b, "answer": answer_of(t), "text": t[:20]}
            except Exception as e:
                return {"axis": ax, "first": a, "second": b, "answer": None, "error": f"{e.__class__.__name__}: {str(e)[:120]}"}
        with ThreadPoolExecutor(4) as ex:
            return list(ex.map(one, jobs))
    rd = _modal_reader(name)
    for i in range(0, len(jobs), batch):
        part = jobs[i:i + batch]
        texts = rd.compare.remote([(jpegs[a], jpegs[b]) for _, a, b in part], spec["prompt"],
                                  [question(axes[ax], spec) for ax, _, _ in part], spec["answers"])
        for (ax, a, b), t in zip(part, texts):
            out.append({"axis": ax, "first": a, "second": b, "answer": answer_of(t), "text": (t or "")[:20]})
    return out


def read(name: str, what: str, chunk: int = 480) -> dict:
    """Reads what is not yet read: answers already in hand are kept, and new ones are written chunk by
    chunk, so a run that stops keeps what came back and a second run costs nothing once all is read."""
    t0 = time.monotonic()
    s = json.loads((DIR / "sample.json").read_text(encoding="utf-8"))
    found = [r["sha"] for r in store.read_jsonl(DIR / "pictures.jsonl") if r.get("found")]
    out_dir = DIR / "readings"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{name}-{what}.jsonl"
    prev = store.read_jsonl(out) if out.exists() else []
    if what == "tone":
        rows = [r for r in prev if r.get("out")]
        done = {r["sha"] for r in rows}
        todo = [sh for sh in sorted(found) if sh not in done]
        for i in range(0, len(todo), chunk // 8):
            jpegs = from_volume(todo[i:i + chunk // 8])
            rows += read_tone(name, jpegs)
            store.write_jsonl(out, rows)
    else:
        d = json.loads((DIR / "pairs.json").read_text(encoding="utf-8"))
        if what == "human":
            base = [(h["axis"], h["left"], h["right"]) for h in d["human"]]
        else:
            base = [(ax, a, b) for ax, es in d["reader"].items() for a, b in es]
        jobs = base + [(ax, b, a) for ax, a, b in base]
        rows = [r for r in prev if r.get("answer")]
        done = {(r["axis"], r["first"], r["second"]) for r in rows}
        todo = [j for j in jobs if j not in done]
        jpegs = from_volume(sorted({x for _, a, b in todo for x in (a, b)})) if todo else {}
        for i in range(0, len(todo), chunk):
            rows += read_pairs(name, jpegs, todo[i:i + chunk])
            store.write_jsonl(out, rows)
    if not out.exists():
        store.write_jsonl(out, rows)
    bad = sum(1 for r in rows if r.get("error") or ("answer" in r and r["answer"] is None))
    _log("read", reader=name, what=what, rows=len(rows), failed=bad, asked=len(todo), sample=len(s["pictures"]),
         seconds=round(time.monotonic() - t0))
    return {"rows": len(rows), "failed": bad, "asked": len(todo)}


# ---------- image models ----------

EMBEDDERS = {
    "csd": "CSD style descriptors (ViT-L, tomg-group-umd/CSD-ViT-L): style apart from content",
    "dino": "DINOv3 ViT-B/16 if its weights can be had without a licence click, else DINOv2 ViT-B/14",
    "fashion": "Marqo FashionSigLIP: fashion product embeddings",
}


def _csd():
    import torch
    from huggingface_hub import hf_hub_download, list_repo_files
    sys.path.insert(0, str(config.ROOT / "bakeoff"))
    from csd_model import CSD_CLIP
    from torchvision import transforms
    from torchvision.transforms import InterpolationMode
    files = list_repo_files("tomg-group-umd/CSD-ViT-L")
    model = CSD_CLIP("vit_large", "default")
    if "model.safetensors" in files:
        from safetensors.torch import load_file
        sd = load_file(hf_hub_download("tomg-group-umd/CSD-ViT-L", "model.safetensors"))
    else:
        name = next(f for f in files if f.endswith((".pth", ".pt", ".bin")))
        ck = torch.load(hf_hub_download("tomg-group-umd/CSD-ViT-L", name), map_location="cpu")
        sd = ck.get("model_state_dict", ck)
    sd = {k.replace("module.", "", 1): v for k, v in sd.items()}
    msg = model.load_state_dict(sd, strict=False)
    if any(k.startswith("last_layer_style") for k in msg.missing_keys):
        raise RuntimeError(f"CSD weights did not load: {msg.missing_keys[:5]}")
    model.eval()
    tf = transforms.Compose([transforms.Resize(224, interpolation=InterpolationMode.BICUBIC), transforms.CenterCrop(224),
                             transforms.ToTensor(),
                             transforms.Normalize((0.48145466, 0.4578275, 0.40821073), (0.26862954, 0.26130258, 0.27577711))])

    def f(img):
        with torch.no_grad():
            _, _, style = model(tf(img.convert("RGB")).unsqueeze(0))
        return style[0].numpy()
    return f, {"weights": "tomg-group-umd/CSD-ViT-L", "missing": len(msg.missing_keys), "unexpected": len(msg.unexpected_keys)}


def _dino():
    import torch
    import timm
    errors = []
    for name in ("vit_base_patch16_dinov3.lvd1689m", "vit_base_patch14_dinov2.lvd142m"):
        try:
            m = timm.create_model(name, pretrained=True, num_classes=0)
            m.eval()
            cfg = timm.data.resolve_data_config({}, model=m)
            tf = timm.data.create_transform(**cfg)

            def f(img, m=m, tf=tf):
                with torch.no_grad():
                    return m(tf(img.convert("RGB")).unsqueeze(0))[0].numpy()
            return f, {"weights": name, "tried": errors}
        except Exception as e:
            errors.append(f"{name}: {e.__class__.__name__}: {str(e)[:120]}")
    raise RuntimeError("no DINO weights: " + " | ".join(errors))


def _fashion():
    import open_clip
    import torch
    model, _, tf = open_clip.create_model_and_transforms("hf-hub:Marqo/marqo-fashionSigLIP")
    model.eval()

    def f(img):
        with torch.no_grad():
            return model.encode_image(tf(img.convert("RGB")).unsqueeze(0))[0].float().numpy()
    return f, {"weights": "Marqo/marqo-fashionSigLIP"}


TEXT_MODELS = {"clip": (config.EMBED_MODEL, config.EMBED_PRETRAINED), "fashion": ("hf-hub:Marqo/marqo-fashionSigLIP", None)}


def axis_prompts() -> list[tuple[str, str]]:
    """Both ends of each pairs-v1 axis as words, in the rubric's own descriptions."""
    out = []
    for ax in _pairs_rubric()["axes"]:
        for end in ("away", "toward"):
            out.append((f"{ax['id']}:{end}", f"a luxury fashion photograph that is {ax[end]}: {ax[end + '_means']}"))
    return out


def text(name: str) -> dict:
    """The axis ends embedded by an image model's own text tower (clip: today's fingerprint model; fashion:
    Marqo FashionSigLIP), so the model can place a picture on each axis by itself."""
    import open_clip
    import torch
    model_name, pretrained = TEXT_MODELS[name]
    model = open_clip.create_model_and_transforms(model_name, pretrained=pretrained)[0] if pretrained else \
        open_clip.create_model_and_transforms(model_name)[0]
    model.eval()
    tok = open_clip.get_tokenizer(model_name)
    keys, texts = zip(*axis_prompts())
    with torch.no_grad():
        T = model.encode_text(tok(list(texts))).float().numpy()
    T = T / np.linalg.norm(T, axis=1, keepdims=True)
    out = DIR / "vectors" / f"{name}-text.npz"
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, keys=np.array(keys), vecs=T.astype(np.float32), texts=np.array(texts))
    _log("text", model=name, prompts=len(texts))
    return {"prompts": len(texts)}


def embed(name: str, shas: list[str] | None = None, vol_dir: str = VOL_DIR, out: Path | None = None, log=None,
          again: bool = False) -> dict:
    """One image model's vectors for the pictures; kept, not made again, once every picture has one."""
    from PIL import Image
    t0 = time.monotonic()
    if shas is None:
        shas = [r["sha"] for r in store.read_jsonl(DIR / "pictures.jsonl") if r.get("found")]
    out = out or DIR / "vectors" / f"{name}.npz"
    if out.exists() and not again:
        with np.load(out, allow_pickle=False) as z:
            if set(shas) <= {str(x) for x in z["shas"]}:
                return {"pictures": len(z["shas"]), "kept": True}
    f, info = {"csd": _csd, "dino": _dino, "fashion": _fashion}[name]()
    done, vecs = [], []
    for i in range(0, len(shas), 200):           # a few hundred pictures in memory at a time
        jpegs = from_volume(sorted(shas)[i:i + 200], vol_dir)
        for sha in sorted(jpegs):
            v = f(Image.open(io.BytesIO(jpegs[sha])))
            done.append(sha)
            vecs.append(v / (np.linalg.norm(v) or 1.0))
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, shas=np.array(done), vecs=np.array(vecs, dtype=np.float16))
    (log or _log)("embed", model=name, pictures=len(done), dim=len(vecs[0]) if vecs else 0,
                  seconds=round(time.monotonic() - t0), **info)
    return {"pictures": len(done), **info}


# ---------- scoring ----------

def _kappa(pairs: list[tuple]) -> float | None:
    if len(pairs) < 10:
        return None
    po = sum(a == b for a, b in pairs) / len(pairs)
    marg = Counter([a for a, _ in pairs] + [b for _, b in pairs])
    n = sum(marg.values())
    pe = sum((c / n) ** 2 for c in marg.values())
    return None if pe >= 1 else round((po - pe) / (1 - pe), 3)


def _kappa2(x: list, y: list) -> float | None:
    """Cohen's kappa between two readers on the same pictures."""
    if len(x) < 10:
        return None
    po = sum(a == b for a, b in zip(x, y)) / len(x)
    cx, cy = Counter(x), Counter(y)
    pe = sum(cx[k] * cy[k] for k in set(cx) | set(cy)) / len(x) ** 2
    return None if pe >= 1 else round((po - pe) / (1 - pe), 3)


def _answers_of(rows: list[dict]) -> dict[str, dict]:
    return {r["sha"]: r["out"] for r in rows if r.get("out")}


def _identity(vecs: dict[str, np.ndarray], pics: list[dict]) -> dict:
    """Each brand's later pictures against every brand's earlier ones: how many brands are nearest
    their own (chance: about one)."""
    early, late = defaultdict(list), defaultdict(list)
    for p in pics:
        if p["sha"] in vecs:
            (early if p["side"] == "early" else late)[p["house"]].append(vecs[p["sha"]])
    hs = sorted(h for h in early if len(early[h]) >= 3 and len(late.get(h, [])) >= 3)
    if len(hs) < 3:
        return {"brands": len(hs)}
    E = np.vstack([np.mean(early[h], axis=0) for h in hs])
    L = np.vstack([np.mean(late[h], axis=0) for h in hs])
    D = np.linalg.norm(L[:, None, :] - E[None, :, :], axis=2)
    ranks = [int((D[i] < D[i, i]).sum()) + 1 for i in range(len(hs))]
    return {"brands": len(hs), "hits": sum(r == 1 for r in ranks), "mean_rank": round(float(np.mean(ranks)), 2)}


def _knn_brand(vecs: dict[str, np.ndarray], pics: list[dict], crops: list[list[str]], k: int = 5) -> float | None:
    """Share of pictures whose five nearest other pictures (never a crop of the same picture) are mostly
    of their own brand."""
    shas = [p["sha"] for p in pics if p["sha"] in vecs]
    if len(shas) < 20:
        return None
    house = {p["sha"]: p["house"] for p in pics}
    partner = defaultdict(set)
    for a, b in crops:
        partner[a].add(b)
        partner[b].add(a)
    X = np.vstack([vecs[s] for s in shas])
    X = X / np.linalg.norm(X, axis=1, keepdims=True)
    S = X @ X.T
    hits = 0
    for i, s in enumerate(shas):
        order = [j for j in np.argsort(-S[i]) if j != i and shas[j] not in partner[s]][:k]
        votes = Counter(house[shas[j]] for j in order)
        hits += votes.most_common(1)[0][0] == house[s]
    return round(hits / len(shas), 3)


def _crop_sep(vecs: dict[str, np.ndarray], crops: list[list[str]], rng) -> dict | None:
    pairs = [(a, b) for a, b in crops if a in vecs and b in vecs]
    if len(pairs) < 5:
        return None
    shas = sorted(vecs)
    cs = [float(vecs[a] @ vecs[b]) for a, b in pairs]
    rand = []
    for _ in range(2000):
        a, b = rng.sample(shas, 2)
        rand.append(float(vecs[a] @ vecs[b]))
    return {"crop_cos": round(float(np.mean(cs)), 3), "random_cos": round(float(np.mean(rand)), 3),
            "separation": round((float(np.mean(cs)) - float(np.mean(rand))) / (float(np.std(rand)) or 1), 2)}


def _eta2(values: np.ndarray, groups: list[str], rng, n_perm: int = 999) -> dict:
    """Share of variance between brands, corrected for chance (epsilon squared), with a permutation p."""
    g = np.array(groups)
    levels = sorted(set(groups))
    if len(levels) < 3:
        return {}

    def eps(v, gg):
        grand = v.mean()
        ssb = sum(((v[gg == l].mean() - grand) ** 2) * (gg == l).sum() for l in levels)
        sst = ((v - grand) ** 2).sum()
        k, n = len(levels), len(v)
        msw = (sst - ssb) / max(n - k, 1)
        return (ssb - (k - 1) * msw) / sst if sst else 0.0
    obs = eps(values, g)
    null = [eps(values, rng.permutation(g)) for _ in range(n_perm)]
    return {"between_brands": round(float(obs), 3), "p": round((1 + sum(n >= obs for n in null)) / (1 + n_perm), 4)}


def score() -> dict:
    from . import readings
    from .score import load_rubric
    rng = random.Random(SEED)
    nrng = np.random.default_rng(SEED)
    s = json.loads((DIR / "sample.json").read_text(encoding="utf-8"))
    pics_rows = {r["sha"]: r for r in store.read_jsonl(DIR / "pictures.jsonl")}
    pics = [p for p in s["pictures"] if pics_rows.get(p["sha"], {}).get("found")]
    shas = {p["sha"] for p in pics}
    crops = [c for c in s["crop_pairs"] if c[0] in shas and c[1] in shas]
    house = {p["sha"]: p["house"] for p in pics}
    out: dict = {"generated_at": store.utc_now(), "status": "exploratory bake-off, not in the pre-registration",
                 "sample": {"chosen": len(s["pictures"]), "fetched": len(pics), "brands": len(set(house.values())),
                            "crop_pairs": len(crops)}}
    spec = load_rubric().spec
    questions = [q for q in spec["enums"] if q not in ("creative_type", "category")]
    # today's reader: its answers as recorded by the homepage pipeline
    from . import homepages
    current = {}
    for p in sorted(homepages.paths()["obs"].glob("*.jsonl")):
        for r in store.read_jsonl(p):
            if r.get("sha") in shas and r.get("status") == "ok" and r.get("output"):
                current[r["sha"]] = r["output"]
    tone = {"qwen25 (today)": current}
    for name in ("qwen3", "claude"):
        rows = _best_reading(f"{name}-tone.jsonl", lambda r: bool(r.get("out")))
        if rows:
            tone[name] = _answers_of(rows)
    out["tone"] = {}
    for name, ans in tone.items():
        rows = {}
        for q in questions + ["mood", "street_couture_axis"]:
            get = (lambda o, q=q: tuple(sorted(o.get(q) or []))) if q == "mood" else (lambda o, q=q: o.get(q))
            vals = [get(ans[sh]) for sh in sorted(ans)]
            if not vals:
                continue
            c = Counter(vals)
            top = c.most_common(1)[0][1] / len(vals)
            kap = _kappa([(get(ans[a]), get(ans[b])) for a, b in crops if a in ans and b in ans])
            v = readings._cramers_v([(house[sh], str(get(ans[sh]))) for sh in sorted(ans)])
            rows[q] = {"top_share": round(top, 3), "values_used": sum(1 for n in c.values() if n / len(vals) >= 0.02),
                       "crop_kappa": kap, "brand_v": None if v is None else round(v, 3),
                       "trackable": top < readings.MAX_TOP and (kap is None or kap >= readings.MIN_KAPPA)}
        moods = Counter(len(ans[sh].get("mood") or []) for sh in ans)
        out["tone"][name] = {"pictures": len(ans), "questions": rows,
                             "trackable": sum(1 for r in rows.values() if r["trackable"]),
                             "three_moods_share": round(moods.get(3, 0) / max(1, len(ans)), 3),
                             "confidence_values": len({ans[sh].get("confidence") for sh in ans})}
        enc = readings.Answers(spec, {q: {"keep": True} for q in questions} | {f"mood:{m}": {"keep": True} for m in spec["lists"]["mood"]["options"]})
        vec = {sh: enc.row(ans[sh]) for sh in ans}
        out["tone"][name]["identity"] = _identity(vec, pics)
    names = list(tone)
    agree = {}
    for x, y in itertools.combinations(names, 2):
        both = sorted(set(tone[x]) & set(tone[y]))
        agree[f"{x} | {y}"] = {q: _kappa2([tone[x][sh].get(q) for sh in both], [tone[y][sh].get(q) for sh in both])
                              for q in questions}
    out["tone_agreement"] = agree
    # pixels
    px = {sh: pics_rows[sh]["pixel"] for sh in shas}
    out["pixels"] = {}
    for k in PIXEL_KEYS:
        v = np.array([px[sh][k] for sh in sorted(px)])
        cp = [(px[a][k], px[b][k]) for a, b in crops]
        r = float(np.corrcoef(*zip(*cp))[0, 1]) if len(cp) >= 5 else None
        out["pixels"][k] = {"mean": round(float(v.mean()), 3), "sd": round(float(v.std()), 3),
                            "crop_r": None if r is None else round(r, 3),
                            **_eta2(v, [house[sh] for sh in sorted(px)], nrng)}
    # image models
    vecs = {"clip": {}}
    from .embed import VectorStore
    for d in sorted(homepages.paths()["vectors"].glob("*")):
        for sh, v in VectorStore(d.name, root=homepages.paths()["vectors"]).vecs.items():
            if sh in shas:
                vecs["clip"][sh] = v / np.linalg.norm(v)
    for f in sorted((DIR / "vectors").glob("*.npz")) if (DIR / "vectors").exists() else []:
        if f.stem.endswith("-text"):
            continue
        with np.load(f, allow_pickle=False) as z:
            vecs[f.stem] = {str(a): b.astype(np.float32) / (np.linalg.norm(b.astype(np.float32)) or 1) for a, b in zip(z["shas"], z["vecs"])}
    out["images"] = {n: {"pictures": len(v), "identity": _identity(v, pics), "knn_brand": _knn_brand(v, pics, crops),
                         "crops": _crop_sep(v, crops, rng)} for n, v in vecs.items()}
    spec_p = _pairs_rubric()
    axes = [a["id"] for a in spec_p["axes"]]
    human_file = DIR / "human.json"
    human = json.loads(human_file.read_text(encoding="utf-8")) if human_file.exists() else {}
    hp = {h["id"]: h for h in json.loads((DIR / "pairs.json").read_text(encoding="utf-8"))["human"]} if (DIR / "pairs.json").exists() else {}
    judged = {hid: ans for hid, ans in human.items() if hid in hp and ans in ("left", "right")}
    sh = sorted(shas)
    idx = {x: i for i, x in enumerate(sh)}

    def quality(th: np.ndarray, ax: str) -> dict:
        """One axis's positions for every picture, judged: do two crops of one picture land together, how
        much of the spread lies between brands, how much inside brands is more than noise, and how often
        the higher-placed picture of each of the person's pairs is the one the person picked."""
        cp = [abs(th[idx[a]] - th[idx[b2]]) for a, b2 in crops]
        rp = [abs(th[i] - th[j]) for i, j in (nrng.choice(len(sh), 2, replace=False) for _ in range(2000))]
        icc = _icc([(th[idx[a]], th[idx[b2]]) for a, b2 in crops])
        eta = _eta2(th, [house[x] for x in sh], nrng)
        within = 1 - eta.get("between_brands", 0.0)
        within_signal = None if icc is None or within <= 0 else round(max(0.0, 1 - (1 - icc) / within), 3)
        pos_agree = [1.0 if (th[idx[h["left"]]] > th[idx[h["right"]]]) == (ans == "left") else 0.0
                     for hid, ans in judged.items() for h in [hp[hid]]
                     if h["axis"] == ax and h["left"] in idx and h["right"] in idx]
        return {"crop_gap_vs_random": round(float(np.mean(cp)) / (float(np.mean(rp)) or 1), 3) if cp else None,
                "crop_icc": icc, **eta, "within_brand_signal": within_signal,
                "human_agreement_by_position": {"pairs": len(pos_agree), "agreement": round(float(np.mean(pos_agree)), 3)}
                if pos_agree else None,
                "pixels": {k: round(float(_spearman(th, np.array([px[x][k] for x in sh]))), 3) for k in PIXEL_KEYS}}

    # zero-shot: an image model's own text tower places each picture toward the end of the axis whose words it
    # lies closer to, with no reader at all
    out["zero_shot"] = {}
    for name in ("clip", "fashion"):
        tf = DIR / "vectors" / f"{name}-text.npz"
        if not tf.exists() or not all(x in vecs.get(name, {}) for x in sh):
            continue
        with np.load(tf, allow_pickle=False) as z:
            T = {str(k): v.astype(np.float64) for k, v in zip(z["keys"], z["vecs"])}
        scores = {}
        for ax in axes:
            if f"{ax}:toward" in T and f"{ax}:away" in T:
                scores[ax] = np.array([float(vecs[name][x] @ (T[f"{ax}:toward"] - T[f"{ax}:away"])) for x in sh])
        out["zero_shot"][name] = {"axes": {ax: quality(th, ax) for ax, th in scores.items()},
                                  "axis_correlations": {f"{a}|{b2}": round(float(_spearman(scores[a], scores[b2])), 3)
                                                        for a, b2 in itertools.combinations(sorted(scores), 2)}}
        allv = [x for ax in scores for x in ([] if not out["zero_shot"][name]["axes"][ax]["human_agreement_by_position"]
                                              else [out["zero_shot"][name]["axes"][ax]["human_agreement_by_position"]])]
        if allv:
            n = sum(a["pairs"] for a in allv)
            out["zero_shot"][name]["human_agreement_all"] = {"pairs": n, "agreement": round(sum(a["pairs"] * a["agreement"] for a in allv) / n, 3)}
    # pairs
    out["pairs"] = {}
    names = sorted({f.name for d in READING_DIRS() if d.exists() for f in d.glob("*.jsonl")
                    if f.name.endswith(("-human.jsonl", "-pairs.jsonl"))}, key=lambda n: (n.endswith("-pairs.jsonl"), n))
    for fname in names:
        name, what = fname[:-len(".jsonl")].rsplit("-", 1)
        rows = [r for r in _best_reading(fname, lambda r: bool(r.get("answer")))]
        res = out["pairs"].setdefault(name, {})
        by_pair = defaultdict(list)
        for r in rows:
            by_pair[(r["axis"], frozenset((r["first"], r["second"])))].append(r)
        both = [v for v in by_pair.values() if len(v) == 2]
        consistent = [v for v in both if (v[0]["answer"] == "first") != (v[1]["answer"] == "first")]
        res[f"{what}_order_consistency"] = round(len(consistent) / max(1, len(both)), 3)
        res[f"{what}_first_share"] = round(sum(r["answer"] == "first" for r in rows) / max(1, len(rows)), 3)
        res[f"{what}_judgements"] = len(rows)
        if human:
            per_axis = defaultdict(list)
            for hid, ans in judged.items():
                h = hp.get(hid)
                if not h or ans not in ("left", "right"):
                    continue
                v = by_pair.get((h["axis"], frozenset((h["left"], h["right"]))), [])
                if len(v) != 2:
                    continue
                pick_left = sum((r["answer"] == "first") == (r["first"] == h["left"]) for r in v)
                reader_side = "left" if pick_left == 2 else "right" if pick_left == 0 else "tie"
                per_axis[h["axis"]].append(1.0 if reader_side == ans else 0.5 if reader_side == "tie" else 0.0)
            if per_axis:
                res["human_agreement"] = {ax: {"pairs": len(v), "agreement": round(float(np.mean(v)), 3)} for ax, v in per_axis.items()}
                allv = [x for v in per_axis.values() for x in v]
                res["human_agreement_all"] = {"pairs": len(allv), "agreement": round(float(np.mean(allv)), 3)}
        if what == "pairs":
            scores = {}
            for ax in axes:
                rr = [r for r in rows if r["axis"] == ax and r["first"] in idx and r["second"] in idx]
                if len(rr) < 50:
                    continue
                th, b = bradley_terry(len(sh), np.array([idx[r["first"]] for r in rr]),
                                      np.array([idx[r["second"]] for r in rr]),
                                      np.array([1.0 if r["answer"] == "first" else 0.0 for r in rr]))
                scores[ax] = th
                res.setdefault("axes", {})[ax] = {"judgements": len(rr), "first_bias": round(b, 3), **quality(th, ax)}
            if len(scores) >= 2:
                res["axis_correlations"] = {f"{a}|{b2}": round(float(_spearman(scores[a], scores[b2])), 3)
                                            for a, b2 in itertools.combinations(sorted(scores), 2)}
                (DIR / "positions").mkdir(parents=True, exist_ok=True)
                store.write_jsonl(DIR / "positions" / f"{name}.jsonl",
                                  [{"sha": x, **{ax: round(float(scores[ax][i]), 4) for ax in scores}} for i, x in enumerate(sh)])
    out["person"] = {"answered": len(human), "left_or_right": sum(a in ("left", "right") for a in human.values()),
                     "unsure": sum(a == "unsure" for a in human.values()), "pairs": len(hp),
                     "pixel_baseline": _pixel_baseline(human, hp, px)}
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(out, indent=1, default=float) + "\n", encoding="utf-8")
    _log("score", readers=sorted(out["tone"]), images=sorted(out["images"]), pairs=sorted(out["pairs"]))
    return out


def READING_DIRS() -> list[Path]:
    """Where readings are kept: the bake-off's own, and those taken through the luxury reader."""
    return [DIR / "readings", config.DATA / "luxury" / "bakeoff"]


def _best_reading(fname: str, good) -> list[dict]:
    """Of the copies of one reading, the one with the most usable rows (only answered rows kept)."""
    best = []
    for d in READING_DIRS():
        f = d / fname
        if f.exists():
            rows = [r for r in store.read_jsonl(f) if good(r)]
            if len(rows) > len(best):
                best = rows
    return best


def _pixel_baseline(human: dict, hp: dict, px: dict) -> dict:
    """How often a single colour-and-light measure picks the picture the person picked, on each axis: the
    measure and its direction chosen on the other pairs and tried on the one left out, so the choice is
    not flattered by being made on the pairs it is scored on."""
    by = defaultdict(list)
    for hid, ans in human.items():
        h = hp.get(hid)
        if h and ans in ("left", "right") and h["left"] in px and h["right"] in px:
            by[h["axis"]].append((h["left"], h["right"]) if ans == "left" else (h["right"], h["left"]))
    out = {}
    for ax, rows in sorted(by.items()):
        if len(rows) < 10:
            continue
        hits = 0.0
        for i, (w, l) in enumerate(rows):
            rest = [r for j, r in enumerate(rows) if j != i]
            share = {k: float(np.mean([px[a][k] > px[b][k] for a, b in rest])) for k in PIXEL_KEYS}
            k = max(PIXEL_KEYS, key=lambda m: abs(share[m] - 0.5))
            d = (px[w][k] - px[l][k]) * (1 if share[k] >= 0.5 else -1)
            hits += 1.0 if d > 0 else 0.5 if d == 0 else 0.0
        full = {k: float(np.mean([px[a][k] > px[b][k] for a, b in rows])) for k in PIXEL_KEYS}
        best = max(PIXEL_KEYS, key=lambda m: abs(full[m] - 0.5))
        out[ax] = {"pairs": len(rows), "agreement_left_out": round(hits / len(rows), 3), "best_measure": best,
                   "best_measure_share": round(full[best], 3)}
    return out


def _icc(pairs: list[tuple[float, float]]) -> float | None:
    """Intraclass correlation of two measurements of one thing (one-way, single measure): here, two crops
    of one picture. 1 when crops always land together, 0 when they are no closer than any two pictures."""
    if len(pairs) < 5:
        return None
    x = np.array(pairs, dtype=float)
    m = x.mean(axis=1)
    n = len(x)
    msb = 2 * ((m - x.mean()) ** 2).sum() / (n - 1)
    msw = ((x[:, 0] - x[:, 1]) ** 2 / 2).sum() / n
    return round(float((msb - msw) / (msb + msw)), 3) if msb + msw else None


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    rx, ry = np.argsort(np.argsort(x)), np.argsort(np.argsort(y))
    return float(np.corrcoef(rx, ry)[0, 1]) if np.std(rx) and np.std(ry) else 0.0


# ---------- the command line ----------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.bakeoff")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sample")
    f = sub.add_parser("fetch")
    f.add_argument("--no-upload", action="store_true")
    pp = sub.add_parser("pairs")
    pp.add_argument("--redesign", action="store_true")
    sub.add_parser("seal")
    r = sub.add_parser("read")
    r.add_argument("--reader", choices=sorted(READERS), required=True)
    r.add_argument("--what", choices=["tone", "pairs", "human"], required=True)
    e = sub.add_parser("embed")
    e.add_argument("--model", choices=sorted(EMBEDDERS), required=True)
    tx = sub.add_parser("text")
    tx.add_argument("--model", choices=sorted(TEXT_MODELS), required=True)
    sub.add_parser("score")
    a = ap.parse_args(argv)
    if a.cmd == "sample":
        s = sample()
        print(f"bakeoff sample: {len(s['pictures'])} pictures, {len(s['brands'])} brands, {len(s['crop_pairs'])} crop pairs")
    elif a.cmd == "fetch":
        rows = fetch()
        print(f"bakeoff fetch: {sum(r['found'] for r in rows)} of {len(rows)} pictures found again")
        if not a.no_upload:
            # thumbnails are made from the volume, so a run that found nothing new still seals them all
            print(f"to the Modal volume: {to_volume()}; sealed thumbnails: {seal_from_volume()}")
    elif a.cmd == "seal":
        print(f"bakeoff seal: {seal_from_volume()}")
    elif a.cmd == "pairs":
        d = pairs(redesign=a.redesign)
        print(f"bakeoff pairs: {len(d['human'])} for a person; " + ", ".join(f"{k} {len(v)}" for k, v in d["reader"].items()))
    elif a.cmd == "read":
        print(f"bakeoff read {a.reader} {a.what}: {read(a.reader, a.what)}")
    elif a.cmd == "embed":
        print(f"bakeoff embed {a.model}: {embed(a.model)}")
    elif a.cmd == "text":
        print(f"bakeoff text {a.model}: {text(a.model)}")
    elif a.cmd == "score":
        out = score()
        print("bakeoff score: " + json.dumps({k: out[k] for k in ("sample",)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
