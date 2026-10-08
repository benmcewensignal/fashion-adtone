"""The clothes rubric's test set: runway looks and advertising pictures, for the bake-off that decides which
questions of rubric/clothes-v1.md go forward.

    python -m adtone.clothes looks     # on the runner: runway looks from the collection pages the archive
                                       #   keeps (data/thread/looks_probe.json) -> data/clothes/looks.jsonl;
                                       #   copies for the reader to the private Modal volume, thumbnails sealed
    python -m adtone.clothes sample    # the test set: the looks, 150 advertising pictures from the earlier
                                       #   bake-off, the pictures to crop and the 100 to label
                                       #   -> data/clothes/sample.json
    python -m adtone.clothes crops     # on the runner: a crop of each chosen picture to its central 85%,
                                       #   to the private volume -> data/clothes/crops.jsonl

The test set is described in the rubric. No picture is written to the repository, which is public: the
reader's copies go to the private Modal volume, and the thumbnails for the labelling page leave the
runner only sealed to a key held outside the repository. What the repository keeps is each picture's
hash, where it was found, its size and its perceptual hash. Reading and scoring wait until the rubric
is frozen.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import random
import sys
import tarfile
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, store

DIR = config.DATA / "clothes"
PROV = config.PROV_DIR / "clothes.jsonl"
LOCAL = config.ROOT / "clothes_pictures"      # ignored by git; on the runner only
VOL_DIR = "/clothes-v1"
ADS_VOL_DIR = "/bakeoff-v1"                    # where the earlier bake-off's copies are
PER_HOUSE = 14
CANDIDATES = 60          # looks gathered per house before the spread is chosen
PORTRAIT = 1.2           # height over width for a picture to count as a look
MIN_HEIGHT = 500
DUP_BITS = 6             # perceptual hashes this close are one picture at two sizes
N_ADS = 150
ADS_PERSON = 100         # of the advertising pictures, those showing a person
N_LABEL_EACH = 50
LABEL_PERSON = 33        # of the 50 advertising pictures to label, those showing a person
N_CROP_EACH = 30
CROP_KEEP = 0.85
SEED = 20261008
SKIP_PAGE = ("pre-order", "holiday", "bag", "subscribe")    # product and sign-up pages, not the show


def _log(event: str, **kw) -> None:
    store.append_jsonl(PROV, [{"at": store.utc_now(), "event": event, **kw}])


# ---------- the looks ----------

def pages_for(probe: dict) -> dict[str, list[tuple[str, str]]]:
    """For each house the probe found looks for, its pages in the order to try: the likeliest show or
    collection page first, then by the number of pictures."""
    from .looks import score
    out = {}
    for house, h in sorted(probe.get("houses", {}).items()):
        if h.get("verdict") != "looks found":
            continue
        ok = [p for p in h.get("pages", []) if p.get("status") == "ok" and (p.get("kept") or 0) >= 2
              and (p.get("pictures") or 0) >= 15 and not any(w in p["url"].lower() for w in SKIP_PAGE)]
        ok.sort(key=lambda p: (-score(p["url"]), -p["pictures"], p["url"]))
        if ok:
            out[house] = [(p["ts"], p["url"]) for p in ok]
    return out


def spread(items: list, k: int) -> list:
    """k items evenly spaced through a list in its order, so a show is sampled from start to finish."""
    if len(items) <= k:
        return list(items)
    return [items[round(i * (len(items) - 1) / (k - 1))] for i in range(k)] if k > 1 else items[:1]


def house_looks(get, house: str, pages: list[tuple[str, str]], keep, shrink, per_house: int = PER_HOUSE,
                candidates: int = CANDIDATES, deadline: float | None = None) -> list[dict]:
    """One house: its pages opened in turn, the pictures on each fetched in page order, the portrait ones
    of a usable size kept once each (the same look at two sizes counts once), until enough are gathered;
    then an even spread of them. `get` fetches a URL; `shrink` makes a picture's small copies at once, so
    no full-size picture is held; `keep` stores the copies of the chosen ones."""
    from . import homepages, media
    found, seen = [], []
    for ts, url in pages:
        if len(found) >= candidates or (deadline and time.monotonic() > deadline):
            break
        r = get(homepages.REPLAY.format(ts=ts, url=url))
        if r is None or r.status_code != 200:
            continue
        page = homepages.final_url(getattr(r, "url", "") or "", url)
        for u in homepages.page_images(r.text, page, ts, cap=300):
            if len(found) >= candidates or (deadline and time.monotonic() > deadline):
                break
            ri = get(u)
            if ri is None or ri.status_code != 200 or len(ri.content) > config.MAX_IMAGE_BYTES:
                continue
            try:
                img = media.open_image(ri.content)
            except media.MediaError:
                continue
            if img.height < MIN_HEIGHT or img.height < PORTRAIT * img.width:
                continue
            ph = media.phash(img)
            if any(media.hamming(ph, s) <= DUP_BITS for s in seen):
                continue
            seen.append(ph)
            found.append({"sha": hashlib.sha256(ri.content).hexdigest(), "house": house, "page": url, "ts": ts,
                          "order": len(found), "w": img.width, "h": img.height, "phash": ph, "_copies": shrink(img)})
            del img
    chosen = spread(found, per_house)
    for row in chosen:
        keep(row["sha"], *row.pop("_copies"))
    for row in found:
        row.pop("_copies", None)
    return chosen


def fetch_looks(probe: dict | None = None, per_house: int = PER_HOUSE, workers: int = 4, pause: float = 0.4,
                budget_min: float = 40, session_factory=None, local: Path | None = None) -> list[dict]:
    """Every house the probe found looks for, a few at a time, each with its own session."""
    import requests
    from . import media
    from .bakeoff import READ_EDGE, THUMB_EDGE, _jpeg
    probe = probe if probe is not None else json.loads((config.DATA / "thread" / "looks_probe.json").read_text())
    local = local or LOCAL
    (local / "read").mkdir(parents=True, exist_ok=True)
    (local / "thumb").mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + budget_min * 60
    tl = threading.local()

    def get(url):
        if not hasattr(tl, "s"):
            tl.s = session_factory() if session_factory else requests.Session()
            tl.s.headers.setdefault("User-Agent", media.UA)
        for attempt in range(3):
            try:
                r = tl.s.get(url, timeout=45)
            except requests.RequestException:
                r = None
            if pause:
                time.sleep(pause)
            if r is not None and r.status_code not in (429, 500, 502, 503, 504):
                return r
            time.sleep(min(30, 5 * 2 ** attempt) if pause else 0)
        return None

    def shrink(img):
        return _jpeg(img, READ_EDGE, 90), _jpeg(img, THUMB_EDGE, 80)

    def keep(sha, read_copy, thumb):
        (local / "read" / f"{sha}.jpg").write_bytes(read_copy)
        (local / "thumb" / f"{sha}.jpg").write_bytes(thumb)

    todo = sorted(pages_for(probe).items())
    with ThreadPoolExecutor(max(1, workers)) as ex:
        got = list(ex.map(lambda kv: house_looks(get, kv[0], kv[1], keep, shrink, per_house, deadline=deadline), todo))
    rows = [r for rs in got for r in rs]
    DIR.mkdir(parents=True, exist_ok=True)
    store.write_jsonl(DIR / "looks.jsonl", rows)
    _log("looks", houses=len(todo), looks=len(rows), per_house={h: len(rs) for (h, _), rs in zip(todo, got)},
         out_of_time=time.monotonic() > deadline)
    return rows


# ---------- the test set ----------

def _within_house(items: list[dict], n: int, rng: random.Random) -> list[dict]:
    """n items spread across houses as evenly as they allow: each house's items shuffled, then taken in
    turn, house by house."""
    by = defaultdict(list)
    for it in sorted(items, key=lambda i: i["sha"]):
        by[it["house"]].append(it)
    for h in by:
        rng.shuffle(by[h])
    out, houses = [], sorted(by)
    rng.shuffle(houses)
    while len(out) < n and any(by[h] for h in houses):
        for h in houses:
            if by[h] and len(out) < n:
                out.append(by[h].pop())
    return out


def adverts(seed: int = SEED, n: int = N_ADS, n_person: int = ADS_PERSON) -> list[dict]:
    """Advertising pictures from the earlier bake-off (homepage pictures, their copies already on the private
    volume): two in three showing a person, one in three a product alone, by their tone-v2 reading."""
    s = json.loads((config.DATA / "bakeoff" / "sample.json").read_text(encoding="utf-8"))
    found = {r["sha"] for r in store.read_jsonl(config.DATA / "bakeoff" / "pictures.jsonl") if r.get("found")}
    tone = {r["sha"]: (r.get("out") or {}) for r in store.read_jsonl(config.DATA / "luxury" / "bakeoff" / "qwen3-tone-v2.jsonl")}
    pics = [{"sha": p["sha"], "house": p["house"], "person": tone[p["sha"]].get("people") not in (None, "none")}
            for p in s["pictures"] if p["sha"] in found and tone.get(p["sha"], {}).get("people")]
    rng = random.Random(seed)
    return (_within_house([p for p in pics if p["person"]], n_person, rng)
            + _within_house([p for p in pics if not p["person"]], n - n_person, rng))


def sample(seed: int = SEED, write: bool = True) -> dict:
    """The looks, the advertising pictures, the pictures to crop and the 100 to label, mixed in one order."""
    looks = store.read_jsonl(DIR / "looks.jsonl")
    ads = adverts(seed)
    rng = random.Random(seed + 1)
    label = _within_house(looks, N_LABEL_EACH, rng)
    label += _within_house([a for a in ads if a["person"]], LABEL_PERSON, rng)
    label += _within_house([a for a in ads if not a["person"]], N_LABEL_EACH - LABEL_PERSON, rng)
    order = [x["sha"] for x in label]
    rng.shuffle(order)
    crop = [x["sha"] for x in rng.sample(looks, min(N_CROP_EACH, len(looks)))]
    crop += [x["sha"] for x in rng.sample(ads, min(N_CROP_EACH, len(ads)))]
    out = {"seed": seed, "made_at": store.utc_now(),
           "looks": [{"sha": r["sha"], "house": r["house"]} for r in looks],
           "adverts": ads, "label": order, "crop": crop,
           "counts": {"looks": len(looks), "adverts": len(ads), "adverts_with_person": sum(a["person"] for a in ads),
                      "label": len(order), "crop": len(crop)}}
    if write:
        DIR.mkdir(parents=True, exist_ok=True)
        (DIR / "sample.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
        _log("sample", **out["counts"])
    return out


# ---------- crops, the volume and the seal ----------

def crop_central(data: bytes, keep: float = CROP_KEEP) -> bytes:
    """The picture cut to its central share on each side, as a copy for the reader."""
    from . import media
    from .bakeoff import READ_EDGE, _jpeg
    img = media.open_image(data)
    w, h = img.size
    dw, dh = round(w * (1 - keep) / 2), round(h * (1 - keep) / 2)
    return _jpeg(img.crop((dw, dh, w - dw, h - dh)), READ_EDGE, 90)


def make_crops(s: dict | None = None, read=None, put=None) -> list[dict]:
    """A crop of each chosen picture, to the volume beside the looks. `read` gets a picture's copy for the
    reader (from the runner or the volume) and `put` stores bytes under a name."""
    s = s if s is not None else json.loads((DIR / "sample.json").read_text(encoding="utf-8"))
    looks = {x["sha"] for x in s["looks"]}
    read = read or _read_copy
    put = put or _put
    rows = []
    for sha in s["crop"]:
        data = read(sha, VOL_DIR if sha in looks else ADS_VOL_DIR)
        if not data:
            rows.append({"orig": sha, "crop": None})
            continue
        c = crop_central(data)
        csha = hashlib.sha256(c).hexdigest()
        put(f"{csha}.jpg", c)
        rows.append({"orig": sha, "crop": csha, "kind": "look" if sha in looks else "advert"})
    DIR.mkdir(parents=True, exist_ok=True)
    store.write_jsonl(DIR / "crops.jsonl", rows)
    _log("crops", made=sum(1 for r in rows if r["crop"]), missing=sum(1 for r in rows if not r["crop"]))
    return rows


def _read_copy(sha: str, vol_dir: str) -> bytes | None:
    f = LOCAL / "read" / f"{sha}.jpg"
    if f.exists():
        return f.read_bytes()
    from .bakeoff import from_volume
    return from_volume([sha], vol_dir).get(sha)


def _put(name: str, data: bytes) -> None:
    (LOCAL / "read").mkdir(parents=True, exist_ok=True)
    (LOCAL / "read" / name).write_bytes(data)


def to_volume() -> int:
    from .bakeoff import to_volume as upload
    return upload(local=LOCAL, vol_dir=VOL_DIR)


def seal_thumbnails(local: Path | None = None) -> Path:
    """The labelling page's thumbnails of the looks as one tar, sealed to the public key in
    bakeoff/sealing_key.pub: only the holder of the private key, outside the repository, can open it."""
    from nacl.public import PublicKey, SealedBox
    from .bakeoff import SEAL_KEY
    local = local or LOCAL
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for f in sorted((local / "thumb").glob("*.jpg")):
            tar.add(str(f), arcname=f.name)
    key = PublicKey(base64.b64decode(SEAL_KEY.read_text().strip()))
    out = local / "thumbs.tar.sealed"
    out.write_bytes(SealedBox(key).encrypt(buf.getvalue()))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m adtone.clothes")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lk = sub.add_parser("looks")
    lk.add_argument("--budget-min", type=float, default=40)
    lk.add_argument("--workers", type=int, default=4)
    sub.add_parser("sample")
    sub.add_parser("crops")
    a = ap.parse_args(argv)
    if a.cmd == "looks":
        rows = fetch_looks(budget_min=a.budget_min, workers=a.workers)
        n = to_volume()
        sealed = seal_thumbnails()
        print(f"clothes looks: {len(rows)} looks, {n} copies on the volume, thumbnails sealed to {sealed.name}")
    elif a.cmd == "sample":
        print(f"clothes sample: {sample()['counts']}")
    elif a.cmd == "crops":
        rows = make_crops()
        n = to_volume()
        print(f"clothes crops: {sum(1 for r in rows if r['crop'])} made, {n} copies on the volume")
    return 0


if __name__ == "__main__":
    sys.exit(main())
