"""Human agreement on a fixed random sample of codings.

    python -m adtone.agreement sample --n 120      # writes data/human_check/sample-<version>.csv
    python -m adtone.agreement score coded.csv     # per-field agreement with the model

The sample file holds Ad Library links and empty columns; the model's answers stay in
the observation rows, so the coding is blind. Fields below the pre-registered kappa
are excluded from claims for that rubric version.
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


def sample(n: int, instrument: str, seed: int = 20261005) -> Path:
    rubric_version = instrument.split("@", 1)[0]
    ok = {r["sha"] for r in store.read_jsonl(config.OBS_DIR / f"{rubric_version}.jsonl")
          if r["instrument"] == instrument and r.get("status") == "ok"}
    media = store.ShardedTable(config.MEDIA_DIR, "ad_id").rows
    pool = []
    for ad_id, m in sorted(media.items()):
        if m.get("status") != "resolved":
            continue
        imgs = sorted(m.get("images", []), key=lambda i: -i["w"] * i["h"])
        if imgs and imgs[0]["sha"] in ok:
            pool.append((ad_id, m["house_id"], imgs[0]["sha"]))
    rng = random.Random(seed)
    picked = rng.sample(pool, min(n, len(pool)))
    out = config.HUMAN_DIR / f"sample-{rubric_version}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ad_id", "library_url", "house_id", "sha", *FIELDS])
        for ad_id, hid, sha in picked:
            w.writerow([ad_id, config.LIBRARY_URL.format(ad_id=ad_id), hid, sha, *[""] * len(FIELDS)])
    return out


def score(coded: Path, instrument: str) -> dict:
    rubric_version = instrument.split("@", 1)[0]
    model = {r["sha"]: r["output"] for r in store.read_jsonl(config.OBS_DIR / f"{rubric_version}.jsonl")
             if r["instrument"] == instrument and r.get("status") == "ok"}
    rows = list(csv.DictReader(coded.open(encoding="utf-8")))
    out = {}
    for fld in FIELDS:
        human, machine = [], []
        for r in rows:
            if r.get(fld) and r["sha"] in model:
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
    c = sub.add_parser("score")
    c.add_argument("file", type=Path)
    ap.add_argument("--instrument", default=f"{config.RUBRIC_VERSION}@{config.CLAUDE_MODEL}")
    a = ap.parse_args(argv)
    if a.cmd == "sample":
        print(f"wrote {sample(a.n, a.instrument)}")
    else:
        for fld, v in score(a.file, a.instrument).items():
            print(f"{fld}: kappa {v['kappa']} on {v['n']} ({'usable' if v['usable'] else 'excluded'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
