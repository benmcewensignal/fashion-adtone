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
    python -m adtone.clothes seal      # on the runner: the 100 pictures to label, from the volume, sealed
    python -m adtone.clothes collect   # on the runner: about 20 looks of every show the coverage map found
                                       #   pages for -> data/clothes/runway.jsonl; copies to the private volume
    python -m adtone.clothes recheck   # runway looks taken from another line's pages (a men's page for a
                                       #   women's show) dropped, and their shows cleared to be taken again
    python -m adtone.clothes read      # every picture on the volumes read with the frozen rubric by the
                                       #   chosen reader: the test set, the runway looks, the homepage
                                       #   pictures -> data/clothes/readings.jsonl (resumable)
    python -m adtone.clothes judge     # which questions stand: rules 1 and 2 from the reader now, rules 3
                                       #   and 4 from the labels once given -> data/clothes/judge.json
    python -m adtone.clothes labels <folder>
                                       # the labelling page's documents, exported one JSON file each, as
                                       #   data/clothes/labels.json (labellers by role, never by name)

The test set is described in the rubric. No picture is written to the repository, which is public: the
reader's copies go to the private Modal volume, and the thumbnails for the labelling page leave the
runner only sealed to a key held outside the repository. What the repository keeps is each picture's
hash, where it was found, its size and its perceptual hash, and the reader's answers.

The rubric was frozen on 9 October 2026 and every picture is read with it. Which questions the measures
use is decided after the reading: the questions that fail rules 1 or 2 on the test set are dropped at
once, the rest are used and marked provisional until the labels decide rules 3 and 4 (the rubric's "The
qualified eye, afterwards"). No picture needs reading again when that happens.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import random
import re
import sys
import tarfile
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

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


SRCSET_ATTR = re.compile(r"""(?:data-)?srcset\s*=\s*["']([^"']+)["']""", re.I)
LAZY_ATTR = re.compile(r"""data-(?:original|bg|image|zoom-src|full|large|src-large|hires|desktop-src|src-desktop)\s*=\s*["']([^"']+)["']""", re.I)
SIZE_QUERY = re.compile(r"[?&](?:wid|w|width|imwidth|sw|size|im_w)=(\d{2,5})", re.I)
SIZE_PATH = re.compile(r"/(?:w_|width_|wid_)?(\d{3,5})(?:x\d{2,5})?(?:/|_|\.)")


def _width_hint(url: str, descriptor: str | None = None) -> int:
    """How wide a picture address says its picture is: the srcset descriptor, a width in the query (wid=, w=,
    imwidth=) or a size in the path (/w_1600/, /1600x2000/); 0 when it says nothing."""
    if descriptor and descriptor.endswith("w") and descriptor[:-1].isdigit():
        return int(descriptor[:-1])
    m = SIZE_QUERY.search(url)
    if m:
        return int(m.group(1))
    m = SIZE_PATH.search(url.split("?")[0])
    return int(m.group(1)) if m else 0


def picture_variants(html: str, page_url: str, ts: str, cap: int = 300) -> list[list[str]]:
    """The pictures of a show page, in page order, each as the addresses to try in turn: the widest size the
    page names first (a srcset, data-srcset or lazy-loading variant, or a width in the address itself), then
    the address the page's own `src` gives when that differs. Of the variants that share one path (a CDN
    that sizes by its query or a folder) only those two are kept. The looks of a show are mostly held at a
    phone size in `src` and at full size in a variant; the archive, which saved what a browser loaded, more
    often holds the `src` address, so the collector falls back to it when the widest is not there."""
    from urllib.parse import urljoin, urlparse
    from . import homepages
    from .backcat import IMG_EXT, IMG_SKIP
    raw = homepages.unrewrite(html).replace("\\/", "/")
    base = homepages.page_images(raw, page_url, ts, cap=cap)
    key = lambda u: (lambda p: p.netloc + p.path)(urlparse(u))
    best: dict[str, tuple[int, str]] = {}
    src: dict[str, str] = {}
    order: list[str] = []
    for u in base:
        o = homepages._original(u)
        k = key(o)
        if k not in best:
            order.append(k)
            src[k] = o
        if k not in best or _width_hint(o) > best[k][0]:
            best[k] = (_width_hint(o), o)
    variants: list[tuple[str, str | None]] = []
    for m in SRCSET_ATTR.finditer(raw):
        for part in m.group(1).split(","):
            bits = part.strip().split()
            if bits:
                variants.append((bits[0], bits[1] if len(bits) > 1 else None))
    for m in LAZY_ATTR.finditer(raw):
        variants.append((m.group(1).strip(), None))
    for u, desc in variants:
        if not u or u.startswith("data:"):
            continue
        absu = urljoin(page_url, u)
        p = urlparse(absu)
        low = p.path.lower()
        if p.scheme not in ("http", "https") or any(x in low for x in IMG_SKIP):
            continue
        if not low.endswith(IMG_EXT) and "image" not in absu.lower():
            continue
        k = p.netloc + p.path
        w = _width_hint(absu, desc)
        if k not in best:
            if len(order) >= cap:
                continue
            order.append(k)
            best[k] = (w, absu)
        elif w > best[k][0]:
            best[k] = (w, absu)
    out = []
    for k in order:
        vs = [best[k][1]]
        if k in src and src[k] != vs[0]:
            vs.append(src[k])
        out.append(vs)
    return out


def show_pictures(html: str, page_url: str, ts: str, cap: int = 300) -> list[str]:
    """The pictures of a show page, in page order, each at the largest size the page names, as the archive's
    addresses (see picture_variants)."""
    from .backcat import WAYBACK_IMG
    return [WAYBACK_IMG.format(ts=ts, url=vs[0]) for vs in picture_variants(html, page_url, ts, cap=cap)]


def _picture(ri) -> tuple[bytes, object] | None:
    """A response's picture as (bytes, image), or None when there was no response, it was refused, too large
    or not a picture."""
    from . import media
    if ri is None or ri.status_code != 200 or len(ri.content) > config.MAX_IMAGE_BYTES:
        return None
    try:
        return ri.content, media.open_image(ri.content)
    except media.MediaError:
        return None


def house_looks(get, house: str, pages: list[tuple[str, str]], keep, shrink, per_house: int = PER_HOUSE,
                candidates: int = CANDIDATES, deadline: float | None = None, tries: int | None = None,
                captures=None, diag: dict | None = None, enough: int = 5, live=None) -> list[dict]:
    """One house: its pages opened in turn, the pictures on each fetched in page order, the portrait ones
    of a usable size kept once each (the same look at two sizes counts once), until enough are gathered;
    then an even spread of them. `get` fetches a URL; `shrink` makes a picture's small copies at once, so
    no full-size picture is held; `keep` stores the copies of the chosen ones. With `tries`, only that many
    of a page's pictures are fetched, spread through the page, so a long show is sampled from end to end.
    With `captures`, a function giving a page's other captures (latest first), each page is opened at its
    latest capture first and at an earlier one when that gave fewer than `enough` looks: a show page
    captured the day after the show may hold a teaser, the gallery coming later. A picture is asked of the
    archive at the widest address the page names, then at the page's own `src` address; with `live`, a
    function fetching an address from the house's own site (robots.txt honoured, None when declined), a
    picture the archive does not hold, or holds only below the size a look needs, is asked of the site at
    its widest address. `diag`, when given, is filled with what happened: pages opened or failed, pictures
    found, fetched, refused, too small, duplicates, how many came from the `src` address or the live site,
    and whether the deadline cut the search short."""
    from . import homepages, media
    from .backcat import WAYBACK_IMG
    found, seen = [], []
    d = diag if diag is not None else {}
    d.update({"pages": len(pages), "opened": 0, "failed": 0, "pictures": 0, "fetched": 0, "refused": 0, "small": 0,
              "duplicates": 0, "cut": False, "captures_tried": 0, "from_src": 0, "from_live": 0})

    def fetch(u):
        return _picture(get(u))

    for ts, url in pages:
        if len(found) >= candidates or (deadline and time.monotonic() > deadline):
            d["cut"] = d["cut"] or len(found) < candidates
            break
        stamps = [ts]
        if captures is not None:
            try:
                later = [t for t in (captures(url, ts) or []) if t != ts]
            except Exception:
                later = []
            stamps = (sorted(later, reverse=True) + [ts])[:3]
        for stamp in stamps:
            before = len(found)
            d["captures_tried"] += 1
            r = get(homepages.REPLAY.format(ts=stamp, url=url))
            if r is None or r.status_code != 200:
                d["failed"] += 1
                continue
            d["opened"] += 1
            page = homepages.final_url(getattr(r, "url", "") or "", url)
            groups = picture_variants(r.text, page, stamp, cap=300)
            d["pictures"] += len(groups)
            for vs in (spread(groups, tries) if tries else groups):
                if len(found) >= candidates or (deadline and time.monotonic() > deadline):
                    d["cut"] = d["cut"] or len(found) < candidates
                    break
                d["fetched"] += 1
                got = fetch(WAYBACK_IMG.format(ts=stamp, url=vs[0]))
                if got is None and len(vs) > 1:
                    got = fetch(WAYBACK_IMG.format(ts=stamp, url=vs[1]))
                    if got is not None:
                        d["from_src"] += 1
                if got is not None and live is not None and got[1].height < MIN_HEIGHT and _width_hint(vs[0]) > got[1].width:
                    wider = _picture(live(vs[0]))
                    if wider is not None:
                        got = wider
                        d["from_live"] += 1
                if got is None and live is not None:
                    got = _picture(live(vs[0]))
                    if got is not None:
                        d["from_live"] += 1
                if got is None:
                    d["refused"] += 1
                    continue
                content, img = got
                if img.height < MIN_HEIGHT or img.height < PORTRAIT * img.width:
                    d["small"] += 1
                    continue
                ph = media.phash(img)
                if any(media.hamming(ph, s) <= DUP_BITS for s in seen):
                    d["duplicates"] += 1
                    continue
                seen.append(ph)
                found.append({"sha": hashlib.sha256(content).hexdigest(), "house": house, "page": url, "ts": stamp,
                              "order": len(found), "w": img.width, "h": img.height, "phash": ph, "_copies": shrink(img)})
                del img
            if len(found) - before >= enough or len(found) >= candidates or d["cut"]:
                break
    chosen = spread(found, per_house)
    for row in chosen:
        keep(row["sha"], *row.pop("_copies"))
    for row in found:
        row.pop("_copies", None)
    d["kept"] = len(chosen)
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
    looks = {r["sha"] for r in store.read_jsonl(DIR / "looks.jsonl")}     # a runway look a homepage also showed stays a look
    pics = [{"sha": p["sha"], "house": p["house"], "person": tone[p["sha"]].get("people") not in (None, "none")}
            for p in s["pictures"] if p["sha"] in found and p["sha"] not in looks and tone.get(p["sha"], {}).get("people")]
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


def seal_thumbnails(local: Path | None = None, folder: str = "thumb") -> Path:
    """A folder of thumbnails as one tar, sealed to the public key in bakeoff/sealing_key.pub: only the
    holder of the private key, outside the repository, can open it."""
    from nacl.public import PublicKey, SealedBox
    from .bakeoff import SEAL_KEY
    local = local or LOCAL
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for f in sorted((local / folder).glob("*.jpg")):
            tar.add(str(f), arcname=f.name)
    key = PublicKey(base64.b64decode(SEAL_KEY.read_text().strip()))
    out = local / "thumbs.tar.sealed"
    out.write_bytes(SealedBox(key).encrypt(buf.getvalue()))
    return out


# ---------- the runway: every show the archive holds pages for ----------

RUNWAY_VOL_DIR = "/runway-v1"
RUNWAY_LOCAL = config.ROOT / "clothes_pictures" / "runway"
PER_SHOW = 20            # looks kept per show, spread from its start to its finish
TRIES_PER_PAGE = 40      # pictures fetched from a show page, spread through it
RUNWAY_FINAL = ("collected", "no looks", "no fitting page")
CAPTURE_AFTER_DAYS = 120  # a show page's later captures, up to four months after the first: the gallery may come later
LIVE_TIMEOUT_S = 15       # a house's site gets this long to serve a picture the archive lacks
LIVE_DECLINES = 3         # after this many failures or stalls in a row, a site is not asked again in the run


def _candidate_signature(sh: dict) -> list[str]:
    return sorted(f"{c.get('ts')} {c.get('url')}" for c in sh.get("candidates", []))


def _todo(sh: dict, st: dict | None) -> bool:
    """Whether a show is read (again): never read; read without a final status; read to no looks by the
    collector before it recorded why (no `diag`), or with candidates since changed; found to have no fitting
    page, with candidates since changed (a show whose only pages are another line's stays as it is until
    the coverage map finds it another page)."""
    if st is None or st.get("status") not in RUNWAY_FINAL:
        return True
    if st["status"] == "no looks" and ("diag" not in st or st.get("candidates") != _candidate_signature(sh)):
        return True
    if st["status"] == "no fitting page" and st.get("candidates") != _candidate_signature(sh):
        return True
    return False


def show_key(show: dict) -> str:
    return f"{show['house']}:{show['date']}:{show['category']}"


_SEP = r"(?:^|[/_\-.?=&#%])"
_END = r"(?:$|[/_\-.?=&#%])"
_MEN = re.compile(_SEP + r"(?:men|mens|man|menswear|homme|hommes|uomo|herren|hombre|for-him|fur-ihn|f%c3%bcr-ihn|für-ihn|m_section)" + _END, re.I)
_WOMEN = re.compile(_SEP + r"(?:women|womens|woman|womenswear|femme|femmes|donna|damen|mujer|for-her|fur-sie|f%c3%bcr-sie|für-sie)" + _END, re.I)
_COUTURE = re.compile(r"couture", re.I)


def page_line(url: str) -> str | None:
    """Which line a house's page belongs to, as its address says: men's, women's or couture; None when it
    does not say."""
    path = (url or "").split("://", 1)[-1].split("/", 1)[-1].lower()
    men, women = bool(_MEN.search(path)), bool(_WOMEN.search(path))
    if _COUTURE.search(path):
        return "couture"
    if men and not women:
        return "men"
    if women and not men:
        return "women"
    return None


def page_fits(url: str, category: str) -> bool:
    """Whether a page can hold a show's looks: a women's ready-to-wear show is not read from a men's page or a
    couture page, a men's show not from a women's page or a couture page, and couture not from a men's page.
    The archive keeps a house's other lines beside the show it is asked for (Dior's women's show of March
    2022 came back as its men's show of January)."""
    line = page_line(url)
    if category == "couture":            # a couture show is read from a page that says so, never from the ready-to-wear
        return line == "couture"
    bad = {"rtw": ("men", "couture"), "men": ("women", "couture")}.get(category, ())
    return line not in bad


def recheck_lines(write: bool = True) -> dict:
    """The runway looks taken from another line's pages, gone: every show that kept any is cleared, its looks
    and its status, so the next collection takes it again from the pages that fit."""
    rows_path, status_path = DIR / "runway.jsonl", DIR / "runway_shows.json"
    rows = store.read_jsonl(rows_path) if rows_path.exists() else []
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    misfit = {show_key(r) for r in rows if not page_fits(r.get("page", ""), r["category"])}
    keep = [r for r in rows if show_key(r) not in misfit]
    out = {"shows_cleared": len(misfit), "looks_dropped": len(rows) - len(keep), "looks_kept": len(keep)}
    if write and misfit:
        store.write_jsonl(rows_path, keep)
        for k in misfit:
            status.pop(k, None)
        status_path.write_text(json.dumps(status, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        _log("recheck_lines", **out, shows=sorted(misfit))
    return out


def collect_runway(coverage: dict | None = None, per_show: int = PER_SHOW, workers: int = 4, pause: float = 0.4,
                   budget_min: float = 40, session_factory=None, local: Path | None = None, clock=time.monotonic,
                   shows: list[str] | None = None, live_site: bool = True) -> dict:
    """For every show the coverage map found pages for, newest first within each house: its likeliest pages
    opened in turn, a spread of their pictures fetched, the portrait looks kept once each, and an even spread
    of `per_show` of them kept: copies for the reader on the runner (then the private volume), hashes and
    places in data/clothes/runway.jsonl. Resumable: a show collected, or found to hold no looks, is left.
    With `shows` (keys as show_key gives them), those shows alone are read, again if need be, their looks
    on file replaced. With `live_site`, a picture the archive lacks is asked of the house's own site
    (robots.txt honoured), the page itself always of the archive."""
    import requests
    from . import media
    from .bakeoff import READ_EDGE, _jpeg
    coverage = coverage if coverage is not None else json.loads((DIR / "looks_coverage.json").read_text(encoding="utf-8"))
    local = local or RUNWAY_LOCAL
    (local / "read").mkdir(parents=True, exist_ok=True)
    status_path = DIR / "runway_shows.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    rows_path = DIR / "runway.jsonl"
    if shows:
        wanted = set(shows)
        for k in wanted:
            status.pop(k, None)
        rows = store.read_jsonl(rows_path) if rows_path.exists() else []
        store.write_jsonl(rows_path, [r for r in rows if show_key(r) not in wanted])
    n_rows = [len(store.read_jsonl(rows_path))]
    deadline = clock() + budget_min * 60
    todo: dict[str, list[dict]] = defaultdict(list)
    for key, sh in coverage.get("shows", {}).items():
        if shows and key not in shows:
            continue
        if sh.get("status") == "found" and _todo(sh, status.get(key)):
            todo[sh["house"]].append(sh)
    tl = threading.local()
    lock = threading.Lock()
    robots: dict[str, object] = {}
    declined: dict[str, int] = {}      # host -> live requests failed or stalled in a row

    def live(url):
        """A picture from the house's own site, asked for as the identified research crawler with robots.txt
        honoured; None when robots say no or the site declines. Pages are never asked of the site, only
        pictures the archive lacks."""
        from urllib import robotparser
        from urllib.parse import urlparse
        from .backcat import UA as CRAWLER_UA
        if not live_site:
            return None
        if not hasattr(tl, "live"):
            tl.live = session_factory() if session_factory else requests.Session()
            tl.live.headers["User-Agent"] = CRAWLER_UA
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        with lock:
            rp = robots.get(base)
        if rp is None:
            rp = robotparser.RobotFileParser()
            try:
                r = tl.live.get(f"{base}/robots.txt", timeout=30)
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except requests.RequestException:
                rp.parse([])
            with lock:
                robots[base] = rp
        if not rp.can_fetch(CRAWLER_UA, url):
            return None
        with lock:
            if declined.get(base, 0) >= LIVE_DECLINES:     # a site that failed or stalled this often is not asked again this run
                return None
        try:
            r = tl.live.get(url, timeout=LIVE_TIMEOUT_S)
        except requests.RequestException:
            r = None
        if pause:
            time.sleep(pause)
        with lock:
            if r is None or r.status_code != 200:
                declined[base] = declined.get(base, 0) + 1
            else:
                declined[base] = 0
        return r

    def captures(url, ts):
        """The page's later captures in the archive's index, latest first, up to four months after `ts`."""
        from datetime import date as _date, timedelta as _td
        from .backcat import CDX
        d0 = _date(int(ts[:4]), int(ts[4:6]), int(ts[6:8]))
        q = (f"{CDX}?url={url}&output=json&fl=timestamp&filter=statuscode:200&filter=mimetype:text/html&limit=12"
             f"&from={d0.strftime('%Y%m%d')}&to={(d0 + _td(days=CAPTURE_AFTER_DAYS)).strftime('%Y%m%d')}")
        r = get(q)
        if r is None or r.status_code != 200:
            return []
        try:
            rows = json.loads(r.text)
        except ValueError:
            return []
        return sorted({row[0] for row in rows[1:] if isinstance(row, list) and row and str(row[0]).isdigit()}, reverse=True)

    def get(url):
        """A page is asked for three times when the archive is busy; a picture twice, since a show has dozens
        and a busy archive would otherwise cost the run its budget on one show."""
        if not hasattr(tl, "s"):
            tl.s = session_factory() if session_factory else requests.Session()
            tl.s.headers.setdefault("User-Agent", media.UA)
        picture = "im_/" in url
        for attempt in range(2 if picture else 3):
            try:
                r = tl.s.get(url, timeout=20 if picture else 45)
            except requests.RequestException:
                r = None
            if pause:
                time.sleep(pause)
            if r is not None and r.status_code not in (429, 500, 502, 503, 504):
                return r
            time.sleep(min(30, 5 * 2 ** attempt) if pause else 0)
        return None

    def shrink(img):
        return (_jpeg(img, READ_EDGE, 90),)

    def keep(sha, read_copy):
        (local / "read" / f"{sha}.jpg").write_bytes(read_copy)

    def one(sh):
        """One show. The shows run newest first across the houses, so a run cut short has read the shows the
        advertising on file can meet (the libraries keep a year) before the older ones."""
        house = sh["house"]
        if clock() > deadline:
            return
        pages = [(c["ts"], c["url"]) for c in sh.get("candidates", []) if page_fits(c["url"], sh["category"])]
        sig = _candidate_signature(sh)
        if not pages:                 # a page of another line only (a men's page for a women's show): not read
            with lock:
                status[show_key(sh)] = {"status": "no fitting page", "looks": 0, "candidates": sig}
                status_path.write_text(json.dumps(status, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            return
        diag: dict = {}
        t_show = time.monotonic()
        got = house_looks(get, house, pages, keep, shrink, per_house=per_show, candidates=per_show * 2,
                          deadline=deadline, tries=TRIES_PER_PAGE, captures=captures, diag=diag, live=live)
        diag["seconds"] = round(time.monotonic() - t_show)
        for g in got:
            g.update(date=sh["date"], season=sh["season"], category=sh["category"])
        with lock:
            if got:
                store.append_jsonl(rows_path, got)
            n_rows[0] += len(got)
            if got:
                st = {"status": "collected", "looks": len(got)}
            elif diag.get("cut") or not diag.get("opened"):
                st = {"status": "unread", "looks": 0}      # the deadline, or no page opened: asked again next run
            else:
                st = {"status": "no looks", "looks": 0}
            status[show_key(sh)] = {**st, "candidates": sig, "diag": {k: v for k, v in diag.items() if k != "kept"}}
            status_path.write_text(json.dumps(status, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    order = sorted((sh for shs in todo.values() for sh in shs), key=lambda s: (s["date"], s["house"]), reverse=True)
    first = set(config.priority_houses()) & set(todo)        # the priority houses' shows first, then the rest newest first
    order = [sh for sh in order if sh["house"] in first] + [sh for sh in order if sh["house"] not in first]
    with ThreadPoolExecutor(max(1, workers)) as ex:
        list(ex.map(one, order))
    summary = {"shows_collected": sum(1 for v in status.values() if v["status"] == "collected"),
               "shows_without_looks": sum(1 for v in status.values() if v["status"] == "no looks"),
               "shows_unread": sum(1 for v in status.values() if v["status"] == "unread"),
               "shows_without_fitting_page": sum(1 for v in status.values() if v["status"] == "no fitting page"),
               "looks": n_rows[0],
               "left": sum(1 for h in todo for sh in todo[h] if _todo(sh, status.get(show_key(sh)))),
               "out_of_time": clock() > deadline}
    _log("runway", **summary)
    return summary


# ---------- the looks from the houses' own sites ----------
# The archive's index times out on the largest sites (Dior's, Gucci's) and its captures of some show pages hold
# no picture addresses (pages drawn by script); for the shows of the last LIVE_DAYS the house's own site has the
# pages up, so they are read from it: the show sections found by hand (reference/show_pages.csv) and by the
# coverage probe, their show pages found by the season the address names, the looks taken as from the archive.
# A look taken this way is the same picture the archive would hold, from the same page; the row says `via: live`.

LIVE_DAYS = 400
LIVE_INDEXES_PER_HOUSE = 6
LIVE_SEASON_AFTER_YEAR = re.compile(r"\b(20\d{2})\b\W{0,3}\b(fw|aw|ss|fall|winter|autumn|spring|summer)\b", re.I)


def slug_season(url: str) -> str | None:
    """The show a page address names, as a season key the calendar uses ('2027 SS rtw', '2026 AW men', '2026 AW
    couture'): the season word and year in either order, the line by the address (men's, women's, couture);
    None when it names none, or a pre-collection, which is not collected."""
    from .thread import title_season
    path = url.split("://", 1)[-1].split("/", 1)[-1]
    text = re.sub(r"[/_\-.%+]+", " ", path.split("?")[0])
    key = title_season(text)
    if key is None:
        m = LIVE_SEASON_AFTER_YEAR.search(text)
        if not m:
            return None
        key = f"{m.group(1)} {'AW' if m.group(2)[0].lower() in 'fwa' else 'SS'} rtw"
    y, half, cat = key.split()
    if cat in ("resort", "prefall", "prespring"):
        return None
    line = page_line(url)
    if line == "couture" or cat == "couture":
        cat = "couture"
    elif line == "men":
        cat = "men"
    else:
        cat = "rtw"
    return f"{y} {half} {cat}"


def live_show_pages(get, prefixes: list[str], wanted: dict[str, dict], cap: int = LIVE_INDEXES_PER_HOUSE) -> tuple[dict[str, list[str]], dict]:
    """The show pages on a house's own site for the shows `wanted` (season key -> show): each section prefix
    opened as an index page, its links on the same site whose address names a wanted season kept, and the
    prefix itself when its own address does. Returns {season key: [urls]} and what happened."""
    from urllib.parse import urljoin, urlparse
    found: dict[str, list[str]] = defaultdict(list)
    diag = {"indexes": 0, "opened": 0, "links": 0}
    for pre in list(dict.fromkeys(prefixes))[:cap]:
        url = pre if pre.startswith("http") else "https://" + pre
        k = slug_season(url)
        if k in wanted and url not in found[k]:
            found[k].append(url)
        diag["indexes"] += 1
        r = get(url)
        if r is None or r.status_code != 200 or not getattr(r, "text", ""):
            continue
        diag["opened"] += 1
        host = urlparse(url).netloc
        for href in re.findall(r"""href\s*=\s*["']([^"'#]+)["']""", r.text):
            absu = urljoin(url, href.strip())
            p = urlparse(absu)
            if p.scheme not in ("http", "https") or p.netloc != host or p.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".pdf")):
                continue
            k = slug_season(absu)
            if k in wanted:
                clean = absu.split("?")[0]
                if clean not in found[k]:
                    found[k].append(clean)
                    diag["links"] += 1
    return dict(found), diag


LIVE_PAGES_PER_SHOW = 3   # show pages asked of a house's own site for one show, those its index pages link first


def _site_address(url: str) -> str | None:
    """An address as the house's own site serves it: https, no port, no query."""
    from urllib.parse import urlparse
    u = url.strip()
    if not u.startswith("http"):
        u = "https://" + u
    p = urlparse(u)
    if not p.netloc:
        return None
    return f"https://{p.netloc.split(':')[0]}{p.path}"


def archive_addresses(shows: dict[str, dict], prefixes: list[str], coverage: dict, status: dict,
                      index=None) -> dict[str, list[str]]:
    """The addresses the archive knows for a house's wanted shows (season key -> show), to ask of the house's own
    site: the coverage probe's candidate pages and those the collector tried, and the pages the archive's index
    holds under the house's show sections whose address names a wanted season and fits its line, whenever they were
    first captured. The probe keeps only section pages first captured within four months of the show, and the
    archive often reaches a show page later, or keeps a copy of it with no picture addresses, while the house's
    site still serves it. `index(prefix)` lists a section's pages as (timestamp, address) rows."""
    out: dict[str, list[str]] = defaultdict(list)

    def add(key, url):
        u = _site_address(url)
        if u and u not in out[key]:
            out[key].append(u)
    for key, sh in sorted(shows.items()):
        k = show_key(sh)
        for c in ((coverage.get("shows") or {}).get(k) or {}).get("candidates") or []:
            add(key, c["url"] if isinstance(c, dict) else str(c).split()[-1])
        for c in (status.get(k) or {}).get("candidates") or []:
            add(key, str(c).split()[-1])
    if index is not None:
        for pre in dict.fromkeys(prefixes):
            for row in index(pre) or []:
                url = row[1] if len(row) > 1 else row[0]
                key = slug_season(url)
                if key in shows and page_fits(url, shows[key]["category"]):
                    add(key, url)
    return dict(out)


def page_looks_live(get, house: str, url: str, keep, shrink, per_show: int = PER_SHOW, candidates: int = CANDIDATES,
                    tries: int | None = None, deadline: float | None = None, diag: dict | None = None) -> list[dict]:
    """One show page on the house's own site: opened, its pictures fetched in page order at the widest address
    the page names, the portrait ones of a usable size kept once each, an even spread kept (as house_looks does
    from the archive). `get` fetches an address live, robots honoured, None when declined."""
    from . import media
    d = diag if diag is not None else {}
    d.update({"opened": 0, "pictures": 0, "fetched": 0, "refused": 0, "small": 0, "duplicates": 0, "cut": False})
    ts = time.strftime("%Y%m%d%H%M%S", time.gmtime())
    r = get(url)
    if r is None or r.status_code != 200 or not getattr(r, "text", ""):
        return []
    d["opened"] = 1
    page = getattr(r, "url", "") or url
    groups = picture_variants(r.text, page, ts, cap=300)
    d["pictures"] = len(groups)
    found, seen = [], []
    for vs in (spread(groups, tries) if tries else groups):
        if len(found) >= candidates or (deadline and time.monotonic() > deadline):
            d["cut"] = d["cut"] or len(found) < candidates
            break
        d["fetched"] += 1
        got = _picture(get(vs[0]))
        if got is None and len(vs) > 1:
            got = _picture(get(vs[1]))
        if got is None:
            d["refused"] += 1
            continue
        content, img = got
        if img.height < MIN_HEIGHT or img.height < PORTRAIT * img.width:
            d["small"] += 1
            continue
        ph = media.phash(img)
        if any(media.hamming(ph, s) <= DUP_BITS for s in seen):
            d["duplicates"] += 1
            continue
        seen.append(ph)
        found.append({"sha": hashlib.sha256(content).hexdigest(), "house": house, "page": url, "ts": ts, "via": "live",
                      "order": len(found), "w": img.width, "h": img.height, "phash": ph, "_copies": shrink(img)})
        del img
    chosen = spread(found, per_show)
    for row in chosen:
        keep(row["sha"], *row.pop("_copies"))
    for row in found:
        row.pop("_copies", None)
    d["kept"] = len(chosen)
    return chosen


def archive_index():
    """The archive's index of a show section, as adtone/looks.py reads it: (timestamp, address) rows, each section
    read once, by one polite crawler per thread."""
    from .backcat import Crawler
    from .looks import section_index
    tl, cache, lock = threading.local(), {}, threading.Lock()

    def index(prefix):
        with lock:
            if prefix in cache:
                return cache[prefix]
        if not hasattr(tl, "c"):
            tl.c = Crawler(pause=2.0)
        rows = section_index(tl.c, prefix)
        with lock:
            cache[prefix] = rows
        return rows
    return index


def collect_live(coverage: dict | None = None, per_show: int = PER_SHOW, workers: int = 3, pause: float = 1.0,
                 budget_min: float = 30, session_factory=None, local: Path | None = None, clock=time.monotonic,
                 today=None, days: int = LIVE_DAYS, houses: list[str] | None = None, index=None) -> dict:
    """The looks of the shows of the last `days` that have none on file, from the houses' own sites: for each
    house, its show sections (reference/show_pages.csv and the coverage probe's) opened live as index pages, the
    show pages found by the season their address names, then the addresses the archive knows for those shows
    (archive_addresses), and the looks taken from them. Pages and pictures are asked for as the identified
    research crawler with robots.txt honoured; a site that declines is left. `index(prefix)` reads the archive's
    index of a section (archive_index); without it only the addresses the coverage probe and the collector kept
    are added. Rows go to data/clothes/runway.jsonl with `via: live`; the show's status says the same."""
    import requests
    from datetime import date as _date, timedelta
    from urllib import robotparser
    from urllib.parse import urlparse
    from . import media
    from .backcat import UA as CRAWLER_UA
    from .bakeoff import READ_EDGE, _jpeg
    from .looks import seeded_sections
    coverage = coverage if coverage is not None else json.loads((DIR / "looks_coverage.json").read_text(encoding="utf-8"))
    local = local or RUNWAY_LOCAL
    (local / "read").mkdir(parents=True, exist_ok=True)
    status_path = DIR / "runway_shows.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    rows_path = DIR / "runway.jsonl"
    n_rows = [len(store.read_jsonl(rows_path))]
    today = today or _date.today()
    since = (today - timedelta(days=days)).isoformat()
    deadline = clock() + budget_min * 60
    wanted: dict[str, dict[str, dict]] = defaultdict(dict)      # house -> season key -> show
    for key, sh in coverage.get("shows", {}).items():
        st = status.get(key)
        if sh["date"] < since or (st and st.get("status") == "collected") or (houses and sh["house"] not in houses):
            continue
        wanted[sh["house"]][sh["season"]] = sh
    sections = {h: list(v) for h, v in seeded_sections().items()}
    for h, sec in (coverage.get("sections") or {}).items():
        for pre in sec.get("prefixes") or []:
            if pre not in sections.setdefault(h, []):
                sections[h].append(pre)
    tl = threading.local()
    lock = threading.Lock()
    robots: dict[str, object] = {}
    declined: dict[str, int] = {}
    sites: dict[str, Counter] = defaultdict(Counter)       # host -> how its answers went: ok, robots, left, a status, stalled
    summary = {"houses": 0, "shows_wanted": sum(len(v) for v in wanted.values()), "shows_collected": 0, "shows_without_looks": 0,
               "shows_without_page": 0, "houses_without_sections": 0, "looks": 0, "out_of_time": False}

    def get(url):
        if not hasattr(tl, "s"):
            tl.s = session_factory() if session_factory else requests.Session()
            tl.s.headers["User-Agent"] = CRAWLER_UA
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        with lock:
            rp = robots.get(base)
            if declined.get(base, 0) >= LIVE_DECLINES:
                sites[p.netloc]["left"] += 1
                return None
        if rp is None:
            rp = robotparser.RobotFileParser()
            try:
                r = tl.s.get(f"{base}/robots.txt", timeout=30)
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except requests.RequestException:
                rp.parse([])
            with lock:
                robots[base] = rp
        if not rp.can_fetch(CRAWLER_UA, url):
            with lock:
                sites[p.netloc]["robots"] += 1
            return None
        try:
            r = tl.s.get(url, timeout=LIVE_TIMEOUT_S)
        except requests.RequestException as e:
            r = None
            why = "stalled" if "timeout" in type(e).__name__.lower() or "Timeout" in str(e) else "failed"
        if pause:
            time.sleep(pause)
        with lock:
            if r is None or r.status_code != 200:
                declined[base] = declined.get(base, 0) + 1
                sites[p.netloc][why if r is None else str(r.status_code)] += 1
            else:
                declined[base] = 0
                sites[p.netloc]["ok"] += 1
        return r

    def shrink(img):
        return (_jpeg(img, READ_EDGE, 90),)

    def keep(sha, read_copy):
        (local / "read" / f"{sha}.jpg").write_bytes(read_copy)

    def section_rows(prefix):
        """The archive's index of one show section (none read without `index`, or once the time is spent)."""
        if index is None or clock() > deadline:
            return []
        return index(prefix)

    def one(house):
        shows = wanted[house]
        if not shows or clock() > deadline:
            return
        with lock:
            summary["houses"] += 1
        prefixes = sections.get(house) or []
        if not prefixes:
            with lock:
                summary["houses_without_sections"] += 1
            return
        pages, idiag = live_show_pages(get, prefixes, shows)
        known = archive_addresses(shows, prefixes, coverage, status, index=section_rows)
        idiag = {**idiag, "from_archive": 0}
        for key, urls in known.items():
            have = pages.setdefault(key, [])
            for u in urls:
                if u not in have:
                    have.append(u)
                    idiag["from_archive"] += 1
        for key, sh in sorted(shows.items(), key=lambda kv: kv[1]["date"], reverse=True):
            if clock() > deadline:
                with lock:
                    summary["out_of_time"] = True
                return
            urls = pages.get(key) or []
            k = show_key(sh)
            if not urls:
                with lock:
                    summary["shows_without_page"] += 1
                    prev = status.get(k) or {}
                    if prev.get("status") != "collected":
                        status[k] = {**prev, "status": prev.get("status") or "no page", "live": {"page": None, "indexes": idiag}}
                        status_path.write_text(json.dumps(status, indent=1, sort_keys=True) + "\n", encoding="utf-8")
                continue
            got, diag = [], {}
            tried = []
            for url in urls[:LIVE_PAGES_PER_SHOW]:
                tried.append(url)
                got = page_looks_live(get, house, url, keep, shrink, per_show=per_show, candidates=per_show * 2,
                                      tries=TRIES_PER_PAGE, deadline=deadline, diag=diag)
                if got:
                    break
            for g in got:
                g.update(date=sh["date"], season=sh["season"], category=sh["category"])
            with lock:
                if got:
                    store.append_jsonl(rows_path, got)
                    n_rows[0] += len(got)
                    summary["shows_collected"] += 1
                    status[k] = {"status": "collected", "looks": len(got), "via": "live", "candidates": _candidate_signature(sh),
                                 "live": {"page": tried[-1], "tried": len(tried), "indexes": idiag,
                                          "diag": {x: v for x, v in diag.items() if x != "kept"}}}
                else:
                    summary["shows_without_looks"] += 1
                    prev = status.get(k) or {}
                    status[k] = {**prev, "status": prev.get("status") or "no looks",
                                 "live": {"page": urls[0], "tried": len(tried), "indexes": idiag,
                                          "diag": {x: v for x, v in diag.items() if x != "kept"}}}
                status_path.write_text(json.dumps(status, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    order = sorted(wanted, key=lambda h: (h not in set(config.priority_houses()), h))
    with ThreadPoolExecutor(max(1, workers)) as ex:
        list(ex.map(one, order))
    summary["looks"] = n_rows[0]
    summary["sites"] = {h: dict(c) for h, c in sorted(sites.items())}
    _log("runway_live", **summary)
    return summary


def runway_to_volume(local: Path | None = None) -> int:
    from .bakeoff import to_volume as upload
    return upload(local=local or RUNWAY_LOCAL, vol_dir=RUNWAY_VOL_DIR)


# ---------- the reading (rubric/clothes-v1.md, "What is read") ----------

VERSION = "clothes-v1"
READER = "qwen3"                 # Qwen3-VL-32B at the weights pinned in data/luxury/readers.json
READINGS = DIR / "readings.jsonl"
HOME_VOL_DIR = "/pictures-v1"    # the homepage pictures, where the luxury reading keeps them
BATCH = 16
FOLDERS = (VOL_DIR, ADS_VOL_DIR, RUNWAY_VOL_DIR, HOME_VOL_DIR)


def reading_plan(present: dict[str, set[str]] | None = None) -> list[tuple[str, str, str]]:
    """Every picture to read, once, as (set, folder, sha) in the order they are read: the test set and its
    crops first (rules 1 and 2 are judged on them), then the runway looks, then every homepage picture on
    the volume. A picture is read from its own folder, or from another that holds the same picture; one
    on no volume yet is left for a later run."""
    if present is None:
        from .bakeoff import on_volume
        present = {d: on_volume(d) for d in FOLDERS}
    s = json.loads((DIR / "sample.json").read_text(encoding="utf-8")) if (DIR / "sample.json").exists() else {}
    crops = [r["crop"] for r in store.read_jsonl(DIR / "crops.jsonl") if r.get("crop")] if (DIR / "crops.jsonl").exists() else []
    runway = store.read_jsonl(DIR / "runway.jsonl") if (DIR / "runway.jsonl").exists() else []
    want = ([("testset", VOL_DIR, x["sha"]) for x in s.get("looks", [])]
            + [("testset", ADS_VOL_DIR, x["sha"]) for x in s.get("adverts", [])]
            + [("testset", VOL_DIR, c) for c in crops]
            + [("runway", RUNWAY_VOL_DIR, r["sha"]) for r in runway]
            + [("homepages", HOME_VOL_DIR, sha) for sha in sorted(present.get(HOME_VOL_DIR, ()))])
    out, seen = [], set()
    for st, folder, sha in want:
        if sha in seen:
            continue
        where = next((d for d in (folder, *FOLDERS) if sha in present.get(d, ())), None)
        if where:
            out.append((st, where, sha))
            seen.add(sha)
    return out


def _parse(text: str, rub) -> tuple[dict, bool]:
    """The reader's answer checked against the rubric. The engine cannot hold a list to distinct items, so
    an answer that names an item twice is taken with each item once, which changes no option chosen, and
    flagged."""
    import re
    from .score import ScoreError, parse, validate
    try:
        return parse(text, rub), False
    except ScoreError as e:
        if "repeats a value" not in str(e):
            raise
    obj = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    for k in rub.spec.get("lists", {}):
        if isinstance(obj.get(k), list):
            obj[k] = list(dict.fromkeys(obj[k]))
    return validate(obj, rub), True


def _reading_rows(part: list[tuple[str, str, str]], texts: list[str], rub) -> list[dict]:
    from .score import ScoreError
    rows = []
    for (st, folder, sha), text in zip(part, texts):
        row = {"sha": sha, "set": st, "folder": folder, "version": rub.version, "reader": READER}
        try:
            row["out"], dedup = _parse(text, rub)
            if dedup:
                row["deduplicated"] = True
        except (ScoreError, AttributeError, ValueError) as e:
            row.update(error=f"{e.__class__.__name__}: {str(e)[:200]}", text=(text or "")[:300])
        rows.append(row)
    return rows


def _read_already(path: Path | None = None) -> set[str]:
    path = path or READINGS
    return {r["sha"] for r in store.read_jsonl(path) if r.get("out") and r.get("version") == VERSION} if path.exists() else set()


def read(budget_min: float = 70, min_waiting: int = 0, present: dict | None = None, on_modal=None) -> dict:
    """Every picture of the plan not yet read, in batches to the reader on Modal, written as they come back
    to data/clothes/readings.jsonl. Resumable: a picture read is never read again, one whose answer did
    not fit the rubric is tried again next run. With `min_waiting`, nothing is sent until that many wait,
    so a run every few hours does not start the GPUs for a handful of new looks."""
    from .score import json_schema, load_rubric
    if on_modal is None:
        from .luxury import _on_modal as on_modal
    rub = load_rubric(VERSION)          # refuses a rubric changed since it was frozen
    t0 = time.monotonic()
    plan = reading_plan(present)
    done = _read_already()
    todo = [p for p in plan if p[2] not in done]
    result = {"on_volumes": len(plan), "read_before": len(plan) - len(todo), "waiting": len(todo),
              "by_set": dict(Counter(st for st, _, _ in todo))}
    if not todo or len(todo) < min_waiting:
        _log("read", **result, sent=0)
        return {**result, "sent": 0}
    parts, cur = [], []
    for item in todo:                   # batches of one folder each, in the plan's order
        if cur and (len(cur) == BATCH or cur[-1][1] != item[1]):
            parts.append(cur)
            cur = []
        cur.append(item)
    parts.append(cur)
    schema = json_schema(rub)
    n = on_modal(READER, "read_from", parts, lambda part: (part[0][1], [x[2] for x in part], rub.prompt, schema),
                 lambda part, texts: _reading_rows(part, texts, rub), READINGS, t0 + budget_min * 60, log=_log)
    good = _read_already()
    result.update(sent=sum(len(p) for p in parts), rows=n, read_now=len(good) - len(done),
                  seconds=round(time.monotonic() - t0))
    _log("read", **result)
    return result


VECTORS = DIR / "vectors"


def fingerprint(budget_min: float = 40, batch: int = 64, embedder=None, read_copy=None) -> dict:
    """The runway looks' fingerprints, by the same pinned image model as every homepage and ad picture's,
    so an ad can be set against the looks of the show it follows. Pictures come from the private volume
    a few at a time and are never written here; the vectors go to data/clothes/vectors/<tag>.npz, resumed
    run by run."""
    from .bakeoff import from_volume
    from .embed import OpenClipEmbedder
    from PIL import Image
    t0 = time.monotonic()
    emb = embedder or OpenClipEmbedder()
    out = VECTORS / f"{emb.tag}.npz"
    shas, vecs = [], []
    if out.exists():
        with np.load(out, allow_pickle=False) as z:
            shas, vecs = [str(x) for x in z["shas"]], [v for v in z["vecs"]]
    have = set(shas)
    want = [r["sha"] for r in store.read_jsonl(DIR / "runway.jsonl") if r["sha"] not in have]
    want = list(dict.fromkeys(want))
    done, missing = 0, 0
    for i in range(0, len(want), batch):
        if time.monotonic() - t0 > budget_min * 60:
            break
        part = want[i:i + batch]
        jpegs = (read_copy or from_volume)(part, RUNWAY_VOL_DIR)
        missing += len(part) - len(jpegs)
        imgs, keys = [], []
        for sha in part:
            if sha in jpegs:
                try:
                    imgs.append(Image.open(io.BytesIO(jpegs[sha])))
                    keys.append(sha)
                except Exception:
                    missing += 1
        if imgs:
            got = emb.embed_many(imgs)
            for sha, v in zip(keys, got):
                shas.append(sha)
                vecs.append(np.asarray(v, dtype=np.float16))
                done += 1
            for im in imgs:
                im.close()
    VECTORS.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, shas=np.array(shas), vecs=np.array(vecs, dtype=np.float16) if vecs else np.zeros((0, 512), np.float16))
    result = {"looks": len(have) + len(want), "had": len(have), "done": done, "missing_on_volume": missing,
              "left": len(want) - done - missing, "seconds": round(time.monotonic() - t0), "tag": emb.tag}
    _log("fingerprint", **result)
    return result


def readings(path: Path | None = None) -> dict[str, dict]:
    """The reader's answers by picture, the last good reading of each."""
    path = path or READINGS
    out = {}
    for r in store.read_jsonl(path) if path.exists() else []:
        if r.get("out") and r.get("version") == VERSION:
            out[r["sha"]] = r["out"]
    return out


# ---------- which questions stand (rubric/clothes-v1.md, "Which questions stand") ----------
#
# Fixed with the rubric, before any picture is read with it. Pure functions over answers already read:
# the reader's on every picture and on the crops, and each labeller's on the labelled pictures.

WORN = ("skin_shown", "hemline", "layers", "silhouette")   # questions about a worn outfit
VARY_MAX = 0.90          # the commonest answer's share must stay below this
OPTION_SHARE = (0.05, 0.95)
KAPPA_MIN = 0.4
CROP_PAIRS_MIN = 10
LABELLED_MIN = 20        # labelled pictures of one kind the question applies to


def applies(key: str, subject) -> bool:
    """Whether a question applies to a picture, given what it shows: the questions about a worn outfit where a
    person wears one, the rest wherever any clothing shows."""
    if subject is None:
        return False
    if key == "subject":
        return True
    if key in WORN:
        return subject in ("worn_full", "worn_part")
    return subject != "no_clothing"


def _weights(values: list, ordinal: bool):
    cats = sorted(set(values))
    if not ordinal:
        return cats, None
    lo, hi = min(cats), max(cats)
    span = (hi - lo) or 1
    return cats, lambda a, b: 1 - ((a - b) / span) ** 2


def cohen(x: list, y: list, ordinal: bool = False) -> float | None:
    """Cohen's kappa between two raters on the same pictures; quadratic weighted for an ordinal scale."""
    if not x or len(x) != len(y):
        return None
    cats, w = _weights(list(x) + list(y), ordinal)
    n = len(x)
    agree = (lambda a, b: w(a, b)) if w else (lambda a, b: 1.0 if a == b else 0.0)
    po = sum(agree(a, b) for a, b in zip(x, y)) / n
    px = {c: sum(v == c for v in x) / n for c in cats}
    py = {c: sum(v == c for v in y) / n for c in cats}
    pe = sum(px[a] * py[b] * agree(a, b) for a in cats for b in cats)
    return None if pe >= 1 - 1e-12 else round((po - pe) / (1 - pe), 3)


def pooled(pairs: list[tuple], ordinal: bool = False) -> float | None:
    """Kappa for two readings of one picture (a picture and its crop), with one pooled set of marginals,
    as the earlier bake-off computed it."""
    if not pairs:
        return None
    vals = [a for a, _ in pairs] + [b for _, b in pairs]
    cats, w = _weights(vals, ordinal)
    agree = (lambda a, b: w(a, b)) if w else (lambda a, b: 1.0 if a == b else 0.0)
    po = sum(agree(a, b) for a, b in pairs) / len(pairs)
    p = {c: vals.count(c) / len(vals) for c in cats}
    pe = sum(p[a] * p[b] * agree(a, b) for a in cats for b in cats)
    return None if pe >= 1 - 1e-12 else round((po - pe) / (1 - pe), 3)


def _units(key: str, spec: dict) -> list:
    if key in spec.get("lists", {}):
        return [o for o in spec["lists"][key]["options"]]
    return [None]


def _value(ans: dict | None, key: str, option):
    if not ans or key not in ans:
        return None
    v = ans[key]
    if option is not None:
        return isinstance(v, list) and option in v
    return v


def judge(spec: dict, reader: dict[str, dict], crops: list[tuple[str, str]], kinds: dict[str, str],
          labels: dict[str, dict[str, dict]]) -> dict:
    """Each question, and each option of a list, against the four rules. `reader` maps a picture (or a
    crop) to the reader's answers; `labels` maps a labeller ('ben', 'trained-...') to their answers by
    picture; `kinds` says whether a picture is a 'look' or an 'advert'. Applicability for rules 1 and 2
    follows the reader's own subject answer; for rules 3 and 4 it follows Ben's subject label."""
    eye = {k: g for g, ks in spec["eye"].items() for k in ks}
    ben = labels.get("ben", {})
    trained = sorted(k for k in labels if k.startswith("trained-"))
    out, forward, options = {}, [], {}
    for key in sorted(eye):
        ordinal = key in spec.get("integers", {})
        results = {}
        for opt in _units(key, spec):
            name = key if opt is None else f"{key}.{opt}"
            r: dict = {}
            vals = [_value(a, key, opt) for sha, a in reader.items() if sha in kinds and applies(key, a.get("subject"))]
            vals = [v for v in vals if v is not None]
            if opt is None:
                top = max((vals.count(v) for v in set(vals)), default=0)
                r["commonest_share"] = round(top / len(vals), 3) if vals else None
                r["varies"] = bool(vals) and top / len(vals) < VARY_MAX
            else:
                share = sum(vals) / len(vals) if vals else None
                r["share"] = None if share is None else round(share, 3)
                r["varies"] = share is not None and OPTION_SHARE[0] <= share <= OPTION_SHARE[1]
            cp = [(_value(reader.get(o), key, opt), _value(reader.get(c), key, opt)) for o, c in crops
                  if reader.get(o) and reader.get(c) and applies(key, reader[o].get("subject"))]
            cp = [(a, b) for a, b in cp if a is not None and b is not None]
            r["crop_pairs"] = len(cp)
            r["crop_kappa"] = pooled(cp, ordinal) if len(cp) >= CROP_PAIRS_MIN else None
            r["stable"] = r["crop_kappa"] is not None and r["crop_kappa"] >= KAPPA_MIN
            refs = ["ben"] if eye[key] == "anyone" else trained
            r["agree"] = {}
            ok = bool(refs)
            for ref in refs:
                for kind in ("look", "advert"):
                    xs, ys = [], []
                    for sha, lab in labels.get(ref, {}).items():
                        if kinds.get(sha) != kind or not applies(key, (ben.get(sha) or lab).get("subject")):
                            continue
                        a, b = _value(reader.get(sha), key, opt), _value(lab, key, opt)
                        if a is not None and b is not None:
                            xs.append(a)
                            ys.append(b)
                    k = cohen(xs, ys, ordinal) if len(xs) >= LABELLED_MIN else None
                    r["agree"][f"{ref}:{kind}"] = {"n": len(xs), "kappa": k}
                    ok = ok and k is not None and k >= KAPPA_MIN
            r["agrees"] = ok
            r["experts_agree"] = None
            if eye[key] == "trained" and len(trained) >= 2:
                r["experts"] = {}
                both = True
                for i, t1 in enumerate(trained):
                    for t2 in trained[i + 1:]:
                        for kind in ("look", "advert"):
                            xs, ys = [], []
                            for sha, l1 in labels[t1].items():
                                l2 = labels[t2].get(sha)
                                if l2 is None or kinds.get(sha) != kind or not applies(key, (ben.get(sha) or l1).get("subject")):
                                    continue
                                a, b = _value(l1, key, opt), _value(l2, key, opt)
                                if a is not None and b is not None:
                                    xs.append(a)
                                    ys.append(b)
                            k = cohen(xs, ys, ordinal) if len(xs) >= LABELLED_MIN else None
                            r["experts"][f"{t1}~{t2}:{kind}"] = {"n": len(xs), "kappa": k}
                            both = both and k is not None and k >= KAPPA_MIN
                r["experts_agree"] = both
            if eye[key] == "trained" and "ben" in labels:
                r["untrained"] = {}
                for kind in ("look", "advert"):
                    xs, ys = [], []
                    for sha, lab in ben.items():
                        if kinds.get(sha) != kind or not applies(key, lab.get("subject")):
                            continue
                        a, b = _value(reader.get(sha), key, opt), _value(lab, key, opt)
                        if a is not None and b is not None:
                            xs.append(a)
                            ys.append(b)
                    r["untrained"][f"reader~ben:{kind}"] = {"n": len(xs), "kappa": cohen(xs, ys, ordinal) if len(xs) >= LABELLED_MIN else None}
            r["passes"] = bool(r["varies"] and r["stable"] and r["agrees"] and r["experts_agree"] is not False)
            results[name] = r
        out.update(results)
        if key in spec.get("lists", {}):
            passing = [o for o in spec["lists"][key]["options"] if results[f"{key}.{o}"]["passes"]]
            if len(passing) >= 2:
                forward.append(key)
                options[key] = passing
        elif results[key]["passes"]:
            forward.append(key)
    return {"rules": {"vary_max": VARY_MAX, "option_share": list(OPTION_SHARE), "kappa_min": KAPPA_MIN,
                      "crop_pairs_min": CROP_PAIRS_MIN, "labelled_min": LABELLED_MIN},
            "trained_labellers": len(trained), "questions": out, "forward": forward, "options": options}


# ---------- the standing of each question, now and when the labels arrive ----------
#
# Rules 1 and 2 need only the reader, and are applied as soon as the test set is read. Rules 3 and 4 need
# the labels, and are applied to a labeller's labels once they cover the set to label. Until then a
# question that has passed rules 1 and 2 is used and marked provisional.

LABELS = DIR / "labels.json"
JUDGE = DIR / "judge.json"
COMPLETE = 0.95          # labels, or readings of the test set, are taken as given once they cover this share


def kinds_of_test_set(s: dict | None = None) -> dict[str, str]:
    s = s if s is not None else json.loads((DIR / "sample.json").read_text(encoding="utf-8"))
    return {**{x["sha"]: "look" for x in s.get("looks", [])}, **{x["sha"]: "advert" for x in s.get("adverts", [])}}


def crop_pairs() -> list[tuple[str, str]]:
    path = DIR / "crops.jsonl"
    return [(r["orig"], r["crop"]) for r in store.read_jsonl(path) if r.get("crop")] if path.exists() else []


def load_labels(path: Path | None = None) -> dict:
    path = path or LABELS
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"labellers": {}}


def _scope_keys(spec: dict, scope: str) -> list[str]:
    eye = spec["eye"]
    return list(eye["trained"]) if scope == "trained" else list(eye["anyone"]) + list(eye["trained"])


def import_labels(folder: Path, s: dict | None = None, spec: dict | None = None, write: bool = True) -> dict:
    """The labels given on the private labelling page, exported from its database as one JSON file per
    document, as data/clothes/labels.json. Only clothes labels under this rubric, for the pictures to label,
    are kept, the latest answer for each picture. Each labeller is recorded as `ben`, or as `trained-1`,
    `trained-2` in the order they began, never by name. A labeller's labels are complete once every
    question in their scope is answered for 95 of the 100 pictures."""
    from .score import load_rubric
    s = s if s is not None else json.loads((DIR / "sample.json").read_text(encoding="utf-8"))
    spec = spec or load_rubric(VERSION).spec
    want = set(s["label"])
    got: dict[str, dict[str, tuple]] = defaultdict(dict)
    began, scope = {}, {}
    for f in sorted(Path(folder).rglob("*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        d = doc.get("data") if isinstance(doc, dict) and isinstance(doc.get("data"), dict) else doc
        if not isinstance(d, dict) or d.get("task") != "clothes" or d.get("rubric") != VERSION or d.get("sha") not in want:
            continue
        key = str(d.get("labeller") or "")
        if key != "ben" and not key.startswith("trained-"):
            continue
        at = float(d.get("at") or 0)
        if d["sha"] not in got[key] or at >= got[key][d["sha"]][0]:
            got[key][d["sha"]] = (at, d.get("answers") or {})
        began[key] = min(began.get(key, at), at)
        scope[key] = "all" if key == "ben" else (d.get("scope") or "trained")
    names = {"ben": "ben"}
    for i, key in enumerate(sorted((k for k in got if k != "ben"), key=lambda k: (began[k], k)), 1):
        names[key] = f"trained-{i}"
    out = {"imported_at": store.utc_now(), "rubric": VERSION, "label_set": len(want), "labellers": {}}
    for key, rows in sorted(got.items(), key=lambda kv: names[kv[0]]):
        answers = {sha: a for sha, (_, a) in sorted(rows.items())}
        keys = _scope_keys(spec, scope[key])
        full = sum(1 for a in answers.values() if all(a.get(k) not in (None, []) for k in keys))
        out["labellers"][names[key]] = {"scope": scope[key], "pictures": len(answers), "answered_in_full": full,
                                        "complete": full >= COMPLETE * len(want), "answers": answers}
    if write:
        DIR.mkdir(parents=True, exist_ok=True)
        LABELS.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        _log("labels", **{k: {x: v[x] for x in ("pictures", "answered_in_full", "complete")} for k, v in out["labellers"].items()})
    return out


def _unit_status(r: dict, reference_given: bool) -> tuple[str, list[str]]:
    failed = [rule for rule, ok in (("rule 1: varies", r["varies"]), ("rule 2: stable on a crop", r["stable"])) if not ok]
    if failed:
        return "dropped", failed
    if not reference_given:
        return "provisional", []
    failed = [rule for rule, ok in (("rule 3: agrees with the reference on both kinds", r["agrees"]),
                                    ("rule 4: the trained labellers agree", r["experts_agree"] is not False)) if not ok]
    return ("dropped", failed) if failed else ("checked", [])


def standing(spec: dict, verdict: dict, labellers: dict[str, str]) -> dict:
    """Each question's standing from the four rules' verdict: dropped (it failed a rule), provisional (it has
    passed rules 1 and 2 and its reference labels have not been given), or checked (it has passed all four).
    A list question stands with its options that are not dropped, if at least two are; it is checked when
    all of those are. `labellers` maps those whose labels were complete, and went into the verdict, to
    their scope. Where a question applies is judged by Ben's subject label, so a trained labeller who
    answered only the trained questions is taken as given once Ben's labels are too."""
    eye = {k: g for g, ks in spec["eye"].items() for k in ks}
    trained = [k for k in labellers if k.startswith("trained-")]
    given = {"anyone": "ben" in labellers,
             "trained": bool(trained) and ("ben" in labellers or all(labellers[k] == "all" for k in trained))}
    out, use, options = {}, [], {}
    for key in sorted(eye):
        g = eye[key]
        if key in spec.get("lists", {}):
            per = {o: _unit_status(verdict["questions"][f"{key}.{o}"], given[g]) for o in spec["lists"][key]["options"]}
            kept = [o for o, (st, _) in per.items() if st != "dropped"]
            if len(kept) < 2:
                status, why = "dropped", ["fewer than two options stand"]
            else:
                status, why = ("checked" if all(per[o][0] == "checked" for o in kept) else "provisional"), []
            out[key] = {"group": g, "status": status, "why": why, "options": kept,
                        "dropped_options": {o: w for o, (st, w) in per.items() if st == "dropped"}}
            if status != "dropped":
                use.append(key)
                options[key] = kept
        else:
            status, why = _unit_status(verdict["questions"][key], given[g])
            out[key] = {"group": g, "status": status, "why": why}
            if status != "dropped":
                use.append(key)
    kinds = Counter(q["status"] for k, q in out.items() if k in use)
    return {"questions": out, "use": use, "options": options,
            "status": "checked" if use and kinds.get("provisional", 0) == 0 else "provisional" if use else "none stand",
            "counts": {"checked": kinds.get("checked", 0), "provisional": kinds.get("provisional", 0),
                       "dropped": len(out) - len(use)}}


def judge_questions(write: bool = True) -> dict:
    """The rules applied to what is on file: the reader's answers on the test set and its crops, and the
    labels that are complete. -> data/clothes/judge.json, which every clothes measure reads to know which
    answers to use and how to mark what it shows."""
    from .score import load_rubric
    spec = load_rubric(VERSION).spec
    s = json.loads((DIR / "sample.json").read_text(encoding="utf-8"))
    kinds = kinds_of_test_set(s)
    reader = readings()
    crops = crop_pairs()
    labs = load_labels().get("labellers", {})
    decided = {k: v["answers"] for k, v in labs.items() if v.get("complete")}
    test = set(kinds) | {c for _, c in crops}
    got = sum(1 for x in test if x in reader)
    out = {"generated_at": store.utc_now(), "version": VERSION, "reader": READER,
           "test_set": {"pictures": len(test), "read": got, "looks": sum(v == "look" for v in kinds.values()),
                        "adverts": sum(v == "advert" for v in kinds.values()), "crop_pairs": len(crops)},
           "labellers": {k: {x: v.get(x) for x in ("scope", "pictures", "answered_in_full", "complete")} for k, v in labs.items()}}
    if not test or got < COMPLETE * len(test):
        out.update(status="waiting on the reading of the test set", use=[], options={}, questions={})
    else:
        verdict = judge(spec, {k: v for k, v in reader.items() if k in test}, crops, kinds, decided)
        out.update(standing(spec, verdict, {k: labs[k].get("scope") or "all" for k in decided}))
        out["rules"] = verdict["rules"]
        out["detail"] = verdict["questions"]
    before = json.loads(JUDGE.read_text(encoding="utf-8")) if JUDGE.exists() else {}
    same = {k: v for k, v in before.items() if k != "generated_at"} == {k: v for k, v in out.items() if k != "generated_at"}
    if write and not same:              # a run that changes nothing leaves the file, and the history, alone
        DIR.mkdir(parents=True, exist_ok=True)
        JUDGE.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        _log("judge", status=out["status"], use=len(out["use"]), **out.get("counts", {}))
    return out


# ---------- comparing the clothes of two sets of pictures ----------
#
# A set's clothes are the shares of each answer to each standing question, over the pictures the question
# applies to and the answers that describe the clothes (not "not visible", "not applicable" or "cannot be
# told"); a list question counts each option chosen. Two sets are compared question by question by the
# total variation distance between their shares, averaged over the questions both answer. Sets of a few
# dozen pictures differ by chance alone, and the fewer the pictures the more, so each comparison is set
# against the same comparison between random splits of the two sets pooled. Likeness is the share the
# two sets have in common as a fraction of what random splits of the same pictures have in common: about
# one when they cannot be told apart, nought when they share nothing. How often a random split lies as
# far apart as the two sets do says whether they can be told apart at all at these numbers.

NOT_JUDGED = {"not_visible", "not_applicable", "not_distinguishable"}
NOT_CLOTHES = ("subject", "confidence")    # what the picture shows of clothing, and the reader's certainty
WORN_SUBJECTS = ("worn_full", "worn_part")


def shows_outfit(a: dict | None) -> bool:
    """A picture of an outfit worn: a person wearing clothes, with at least one garment the reader can name
    (a crowd too small to see what anyone wears is read as worn but names none)."""
    return bool(a) and a.get("subject") in WORN_SUBJECTS and a.get("garments") not in (None, [], ["none"])
MIN_ANSWERS = 3          # pictures answering a question, on each side, before it enters a comparison
DRAWS = 200


class Wardrobe:
    """Every picture's answers as counts, one matrix per standing question, so a comparison and its random
    splits are sums over rows."""

    def __init__(self, answers: dict[str, dict], spec: dict, use: list[str], options: dict[str, list[str]] | None = None):
        import numpy as np
        options = options or {}
        self.index = {sha: i for i, sha in enumerate(sorted(answers))}
        self.cats, self.mats = {}, {}
        for q in use:
            if q in NOT_CLOTHES:
                continue
            if q in spec.get("lists", {}):
                cats = [o for o in options.get(q, spec["lists"][q]["options"]) if o not in NOT_JUDGED]
            elif q in spec.get("integers", {}):
                cats = list(range(spec["integers"][q]["min"], spec["integers"][q]["max"] + 1))
            elif q in spec.get("enums", {}):
                cats = [v for v in spec["enums"][q] if v not in NOT_JUDGED]
            else:
                continue
            col = {c: j for j, c in enumerate(cats)}
            m = np.zeros((len(self.index), len(cats)))
            for sha, i in self.index.items():
                a = answers[sha]
                if not applies(q, a.get("subject")):
                    continue
                v = a.get(q)
                for x in (v if isinstance(v, list) else [v]):
                    if x in col:
                        m[i, col[x]] += 1
            self.cats[q], self.mats[q] = cats, m

    def rows(self, shas):
        import numpy as np
        return np.array(sorted({self.index[s] for s in shas if s in self.index}), int)

    def profile(self, shas, top: int | None = None) -> dict[str, dict]:
        """Each question's answers as the share of the pictures answering it that give each one (for a list,
        that show the option), largest first."""
        ix = self.rows(shas)
        out = {}
        for q, m in self.mats.items():
            sub = m[ix]
            n = int((sub.sum(1) > 0).sum())
            if n:
                shares = sorted(((str(c), round(float((sub[:, j] > 0).sum() / n), 3)) for j, c in enumerate(self.cats[q])
                                 if sub[:, j].any()), key=lambda kv: -kv[1])
                out[q] = {"n": n, "shares": dict(shares[:top] if top else shares)}
        return out

    def compare(self, a, b, key: str = "", draws: int = DRAWS) -> dict | None:
        """Distance, distance by chance and likeness between two sets of pictures (see above). `key` seeds the
        random splits, so a comparison comes out the same every time it is made."""
        import zlib
        import numpy as np
        ia, ib = self.rows(a), self.rows(b)
        if not len(ia) or not len(ib):
            return None
        answered = lambda m, ix: int((m[ix].sum(1) > 0).sum())
        qs = [q for q, m in self.mats.items() if answered(m, ia) >= MIN_ANSWERS and answered(m, ib) >= MIN_ANSWERS]
        if not qs:
            return None

        def tvd(x, y):
            return 0.5 * float(np.abs(x / x.sum() - y / y.sum()).sum())
        d_obs = float(np.mean([tvd(self.mats[q][ia].sum(0), self.mats[q][ib].sum(0)) for q in qs]))
        pool = np.concatenate([ia, ib])
        rng = np.random.default_rng(zlib.crc32(key.encode()))
        split = np.zeros((draws, len(pool)))
        for r in range(draws):
            split[r, rng.permutation(len(pool))[:len(ia)]] = 1.0
        total, counted = np.zeros(draws), np.zeros(draws)
        for q in qs:
            m = self.mats[q][pool]
            sa = split @ m
            sb = m.sum(0) - sa
            na, nb = sa.sum(1), sb.sum(1)
            ok = (na > 0) & (nb > 0)
            t = np.zeros(draws)
            t[ok] = 0.5 * np.abs(sa[ok] / na[ok, None] - sb[ok] / nb[ok, None]).sum(1)
            total += t
            counted += ok
        splits = total[counted > 0] / counted[counted > 0]
        d_null = float(np.mean(splits))
        return {"distance": round(d_obs, 4), "by_chance": round(d_null, 4),
                "likeness": round((1 - d_obs) / (1 - d_null), 4) if d_null < 1 else None,
                "as_far_by_chance": round(float(np.mean(splits >= d_obs - 1e-12)), 3),
                "questions": len(qs), "n": [int(len(ia)), int(len(ib))]}


LABEL_EDGE = 720        # thumbnails for the labelling page: enough to see cloth and finish on a phone


def seal_labelled(s: dict | None = None, edge: int = LABEL_EDGE, local: Path | None = None) -> Path:
    """The labelling page's pictures: the 100 to label, from the reader's copies on the volume, at a size a
    person can judge cloth and finish from, sealed as one tar to the key outside the repository."""
    from . import media
    from .bakeoff import _jpeg, from_volume
    s = s if s is not None else json.loads((DIR / "sample.json").read_text(encoding="utf-8"))
    local = local or LOCAL
    looks = {x["sha"] for x in s["looks"]}
    want = s["label"]
    got = from_volume([x for x in want if x in looks], VOL_DIR)
    got.update(from_volume([x for x in want if x not in looks], ADS_VOL_DIR))
    out = local / "label"
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.jpg"):
        f.unlink()
    for sha, data in got.items():
        (out / f"{sha}.jpg").write_bytes(_jpeg(media.open_image(data), edge, 75))
    _log("seal", wanted=len(want), sealed=len(got), edge=edge)
    return seal_thumbnails(local, folder="label")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m adtone.clothes")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lk = sub.add_parser("looks")
    lk.add_argument("--budget-min", type=float, default=40)
    lk.add_argument("--workers", type=int, default=4)
    sub.add_parser("sample")
    sub.add_parser("crops")
    sub.add_parser("seal")
    co = sub.add_parser("collect")
    co.add_argument("--budget-min", type=float, default=40)
    co.add_argument("--workers", type=int, default=4)
    co.add_argument("--shows", default="", help="show keys (house:date:category), comma-separated: those alone, read again")
    co.add_argument("--no-live", action="store_true", help="never ask the houses' own sites for a picture the archive lacks")
    lv = sub.add_parser("live", help="the looks of the last year's shows that have none, from the houses' own sites")
    lv.add_argument("--budget-min", type=float, default=30)
    lv.add_argument("--workers", type=int, default=3)
    lv.add_argument("--houses", default="", help="house ids, comma-separated: those alone")
    rd = sub.add_parser("read")
    rd.add_argument("--budget-min", type=float, default=70)
    rd.add_argument("--min-waiting", type=int, default=0, help="send nothing until this many pictures wait")
    sub.add_parser("judge")
    sub.add_parser("recheck")
    fp = sub.add_parser("fingerprint")
    fp.add_argument("--budget-min", type=float, default=40)
    lb = sub.add_parser("labels")
    lb.add_argument("folder", help="the labelling page's documents, one JSON file each")
    a = ap.parse_args(argv)
    if a.cmd == "looks":
        rows = fetch_looks(budget_min=a.budget_min, workers=a.workers)
        n = to_volume()
        sealed = seal_thumbnails()
        print(f"clothes looks: {len(rows)} looks, {n} copies on the volume, thumbnails sealed to {sealed.name}")
    elif a.cmd == "sample":
        print(f"clothes sample: {sample()['counts']}")
    elif a.cmd == "collect":
        summary = collect_runway(budget_min=a.budget_min, workers=a.workers, live_site=not a.no_live,
                                 shows=[k.strip() for k in a.shows.split(",") if k.strip()] or None)
        n = runway_to_volume()
        print(f"clothes collect: {summary}; {n} copies on the volume")
    elif a.cmd == "live":
        summary = collect_live(budget_min=a.budget_min, workers=a.workers, houses=[h for h in a.houses.split(",") if h] or None,
                               index=archive_index())
        n = runway_to_volume()
        print(f"clothes live: {summary}; {n} copies on the volume")
    elif a.cmd == "seal":
        print(f"clothes seal: the pictures to label sealed to {seal_labelled().name}")
    elif a.cmd == "crops":
        rows = make_crops()
        n = to_volume()
        print(f"clothes crops: {sum(1 for r in rows if r['crop'])} made, {n} copies on the volume")
    elif a.cmd == "read":
        print(f"clothes read: {read(a.budget_min, a.min_waiting)}")
    elif a.cmd == "recheck":
        print(f"clothes recheck: {recheck_lines()}")
    elif a.cmd == "fingerprint":
        print(f"clothes fingerprint: {fingerprint(a.budget_min)}")
    elif a.cmd == "judge":
        j = judge_questions()
        print(f"clothes judge: {j['status']}; {j.get('counts')}; standing {j['use']}")
    elif a.cmd == "labels":
        out = import_labels(Path(a.folder))
        print("clothes labels: " + json.dumps({k: {x: v[x] for x in ("pictures", "answered_in_full", "complete")}
                                                for k, v in out["labellers"].items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
