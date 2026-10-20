"""Human agreement on a fixed random sample of codings.

    python -m adtone.agreement sample --n 120      # writes data/human_check/sample-<version>.csv
    python -m adtone.agreement labels <folder>     # the labelling page's answers -> data/human_check/coded-<version>-<labeller>.csv
    python -m adtone.agreement score coded.csv     # per-field agreement with the model

The sample file holds Ad Library links and empty columns; the model's answers stay in
the observation rows, so the coding is blind. Fields below the pre-registered kappa
are excluded from claims for that rubric version.

The Meta ads come through two collectors with the same row shape: the first, Meta's own
API (data/ads, data/media), and the vendor's reading of Meta's EU library (adtone/adlibs.py,
data/metaweb), where each ad's pictures are listed on its row. The sample is drawn from both
the same way: each ad on a confirmed page whose largest picture the instrument read, one
picture per ad, the frozen seed. A sampled picture names its place among the ad's pictures,
so a coder opening the ad in the library codes the same one.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from collections import Counter
from pathlib import Path

from . import config, store

FIELDS = ("creative_type", "light", "setting", "production", "styling_register", "street_couture_axis")
KAPPA_MIN = 0.6


def kappa(a: list[str], b: list[str]) -> float | None:
    pairs = [(x, y) for x, y in zip(a, b) if x and y]
    if len(pairs) < 10:
        return None
    n = len(pairs)
    po = sum(1 for x, y in pairs if x == y) / n
    ca, cb = Counter(x for x, _ in pairs), Counter(y for _, y in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / n ** 2
    return round((po - pe) / (1 - pe), 3) if pe < 1 else 1.0


def _obs_paths(rubric_version: str) -> list[Path]:
    """Where the instrument's readings of Meta ads are: the first collector's, then the vendor's."""
    from .adlibs import paths
    return [config.OBS_DIR / f"{rubric_version}.jsonl", paths("metaweb")["obs"] / f"{rubric_version}.jsonl"]


def _readings(instrument: str) -> dict[str, dict]:
    rubric_version = instrument.split("@", 1)[0]
    out = {}
    for p in _obs_paths(rubric_version):
        for r in store.read_jsonl(p) if p.exists() else []:
            if r.get("instrument") == instrument and r.get("status") == "ok" and r.get("output"):
                out[r["sha"]] = r["output"]
    return out


def _vendor_ads(confirmed: dict[str, set] | None) -> dict[str, dict]:
    """The vendor's Meta ads, as media rows of the first collector's shape: each ad's still pictures, on a page
    confirmed in the registry where `confirmed` is given."""
    from .adlibs import paths
    out = {}
    d = paths("metaweb")["ads"]
    for p in sorted(d.glob("*.jsonl")) if d.exists() else []:
        for r in store.read_jsonl(p):
            if not r.get("ad_id") or not r.get("house_id"):
                continue
            if confirmed is not None and str(r.get("page_id")) not in confirmed.get(r["house_id"], ()):
                continue
            imgs = [m for m in r.get("media") or [] if m.get("sha") and m.get("kind", "image") == "image"]
            if imgs:
                out[str(r["ad_id"])] = {"status": "resolved", "house_id": r["house_id"], "images": imgs}
    return out


def confirmed_pages() -> dict[str, set]:
    """Each house's confirmed pages: the registry's where it names them, otherwise the pages the vendor's
    collector matched to the house by name and kept after checking (data/state/metaweb.json, `keep`)."""
    from . import registry
    from .adlibs import paths
    out = {h.id: {str(p) for p in h.page_ids} for h in registry.load().houses if h.page_ids}
    st = store.read_state(paths("metaweb")["state"])
    for h, v in (st.get("advertisers") or {}).items():
        if h not in out and isinstance(v, dict):
            out[h] = {str(x.get("id")) for x in v.get("keep") or [] if x.get("id")}
    return out


def sample(n: int, instrument: str, seed: int = 20261005, confirmed: dict[str, set] | None = None) -> Path:
    rubric_version = instrument.split("@", 1)[0]
    ok = set(_readings(instrument))
    media = {**store.ShardedTable(config.MEDIA_DIR, "ad_id").rows, **_vendor_ads(confirmed)}
    pool, seen = [], set()
    for ad_id, m in sorted(media.items()):
        if m.get("status") != "resolved":
            continue
        imgs = sorted(m.get("images", []), key=lambda i: -(i.get("w") or 0) * (i.get("h") or 0))
        if imgs and imgs[0]["sha"] in ok and imgs[0]["sha"] not in seen:
            # 120 images: a picture several ads share (variants of one creative) is one image, under its first ad
            seen.add(imgs[0]["sha"])
            place = [i["sha"] for i in m.get("images", [])].index(imgs[0]["sha"]) + 1
            pool.append((ad_id, m["house_id"], imgs[0]["sha"], place, len(m.get("images", []))))
    rng = random.Random(seed)
    picked = rng.sample(pool, min(n, len(pool)))
    out = config.HUMAN_DIR / f"sample-{rubric_version}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ad_id", "library_url", "house_id", "sha", "picture", "of", *FIELDS])
        for ad_id, hid, sha, place, of in picked:
            w.writerow([ad_id, config.LIBRARY_URL.format(ad_id=ad_id), hid, sha, place, of, *[""] * len(FIELDS)])
    return out


def import_labels(folder: Path, rubric_version: str = config.RUBRIC_VERSION, write: bool = True) -> dict[str, Path]:
    """The labelling page's answers to the check (task human_check), exported one JSON file per document: the
    latest answer for each sampled picture, one coded file per labeller, by role (ben, trained-1, ...), never by
    name, in the sample file's columns."""
    import json
    sample_path = config.HUMAN_DIR / f"sample-{rubric_version}.csv"
    want = [r["sha"] for r in csv.DictReader(sample_path.open(encoding="utf-8"))] if sample_path.exists() else []
    got: dict[str, dict[str, tuple]] = {}
    began: dict[str, float] = {}
    for f in sorted(Path(folder).rglob("*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        d = doc.get("data") if isinstance(doc, dict) and isinstance(doc.get("data"), dict) else doc
        if not isinstance(d, dict) or d.get("task") != "human_check" or d.get("rubric") != rubric_version or d.get("sha") not in want:
            continue
        key = str(d.get("labeller") or "")
        if key != "ben" and not key.startswith("trained-"):
            continue
        at = float(d.get("at") or 0)
        if d["sha"] not in got.setdefault(key, {}) or at >= got[key][d["sha"]][0]:
            got[key][d["sha"]] = (at, d.get("answers") or {})
        began[key] = min(began.get(key, at), at)
    names = {"ben": "ben"}
    for i, key in enumerate(sorted((k for k in got if k != "ben"), key=lambda k: (began[k], k)), 1):
        names[key] = f"trained-{i}"
    out = {}
    for key, rows in got.items():
        path = config.HUMAN_DIR / f"coded-{rubric_version}-{names[key]}.csv"
        out[names[key]] = path
        if write:
            with path.open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["sha", *FIELDS])
                for sha in want:
                    if sha in rows:
                        a = rows[sha][1]
                        w.writerow([sha, *["" if a.get(fl) is None else str(a.get(fl)) for fl in FIELDS]])
    return out


def score(coded: Path, instrument: str) -> dict:
    model = _readings(instrument)
    rows = list(csv.DictReader(coded.open(encoding="utf-8")))
    out = {}
    for fld in FIELDS:
        human, machine = [], []
        for r in rows:
            if r.get(fld) and r["sha"] in model and model[r["sha"]].get(fld) is not None:
                human.append(r[fld].strip())
                machine.append(str(model[r["sha"]][fld]))
        k = kappa(human, machine)
        out[fld] = {"n": len(human), "kappa": k, "usable": k is not None and k >= KAPPA_MIN}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.agreement")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--n", type=int, default=120)
    lb = sub.add_parser("labels")
    lb.add_argument("folder", type=Path, help="the labelling page's documents, one JSON file each")
    c = sub.add_parser("score")
    c.add_argument("file", type=Path)
    ap.add_argument("--instrument", default=config.instrument())
    a = ap.parse_args(argv)
    if a.cmd == "sample":
        print(f"wrote {sample(a.n, a.instrument, confirmed=confirmed_pages())}")
    elif a.cmd == "labels":
        for who, path in import_labels(a.folder, a.instrument.split("@", 1)[0]).items():
            print(f"{who}: {path}")
    else:
        for fld, v in score(a.file, a.instrument).items():
            print(f"{fld}: kappa {v['kappa']} on {v['n']} ({'usable' if v['usable'] else 'excluded'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
