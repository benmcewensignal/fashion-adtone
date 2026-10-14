"""The house issues: one picture for each of the 28 houses, its style as it is usually pictured, drawn by the
open image model the site's illustration already uses (bakeoff/modal_paint.py: Qwen-Image, Apache 2.0), for
Focal's mock covers. Focal sets its own masthead and findings over the picture afterwards; the picture holds
no text.

    python -m adtone.issues draw [--only a,b] [--again] [--budget-min 150]
        on the runner: each house's prompt drawn once for each seed of the plan -> data/issues/run.json;
        the pictures to the private Modal volume and, sealed, to a branch of their own
    python -m adtone.issues publish
        on the runner: the picture chosen for each house (data/issues/choices.json) made into the site's copy,
        960 by 1200 WebP, onto the volume beside the originals -> data/issues/web.json, names and hashes; the
        site's build fetches them through bakeoff/modal_issues_web.py and checks each hash

The plan (data/issues/plan.json) holds one prompt for each house, the seeds (and, for a house drawn again with a
new prompt, seeds of its own in seeds_for, so its earlier pictures stay as they were) and the drawing settings. The
prompts never name a house and ask for no text, logo, monogram or house pattern; where a person is in the
picture they are seen from behind, with no face. None of the pictures reaches the repository, which is
public: the originals go to the private Modal volume, the copies to look at leave the runner only sealed to a
key held outside the repository, and the repository keeps each picture's hash.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import tarfile
import time
from pathlib import Path

from . import config, store
from . import flex as F

DIR = config.DATA / "issues"
PLAN = DIR / "plan.json"
RUN = DIR / "run.json"
CHOICES = DIR / "choices.json"
WEB = DIR / "web.json"
WEB_SIZE = (960, 1200)
PROV = config.PROV_DIR / "issues.jsonl"
LOCAL = config.ROOT / "issues_pictures"        # ignored by git; on the runner only
VOL_DIR = "/issues-v1"
REVIEW_EDGE = 1360


def _log(event: str, **kw) -> None:
    store.append_jsonl(PROV, [{"at": store.utc_now(), "event": event, **kw}])


def check_plan(plan: dict, houses: dict[str, str]) -> list[str]:
    """What is wrong with the plan: a house without a prompt, or a prompt that names a house or asks for a mark."""
    bad = []
    prompts = plan.get("houses", {})
    for hid in houses:
        if not prompts.get(hid):
            bad.append(f"{hid}: no prompt")
    names = {n.lower() for n in houses.values()} | {n.lower().split()[0] for n in houses.values() if len(n.split()[0]) > 4}
    for hid, text in prompts.items():
        low = f" {text.lower()} "
        for n in sorted(names):
            if f" {n} " in low or f" {n}," in low or f" {n}." in low or f" {n}'" in low:
                bad.append(f"{hid}: names {n!r}")
        for word in ("logo", "monogram", "lettering", "brand"):
            if word in low and f"no {word}" not in low and f"no {word}s" not in low:
                bad.append(f"{hid}: asks for {word!r}")
    if "text" not in plan.get("negative", "") or "logo" not in plan.get("negative", ""):
        bad.append("the negative prompt must keep out text and logos")
    return bad


def review_copy(png: bytes) -> bytes:
    from PIL import Image
    from .bakeoff import _jpeg
    return _jpeg(Image.open(io.BytesIO(png)), REVIEW_EDGE, 92)


def seal(review: dict[str, bytes], local: Path | None = None, key_path: Path | None = None) -> Path:
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
    key = PublicKey(base64.b64decode((key_path or SEAL_KEY).read_text().strip()))
    out = local / "issues.tar.sealed"
    out.write_bytes(SealedBox(key).encrypt(buf.getvalue()))
    return out


def draw(plan: dict | None = None, starter=None, put=None, only: list[str] | None = None, again: bool = False,
         budget_min: float = 150, seal_fn=None) -> dict:
    """Each house's prompt drawn once for each seed; a house already drawn is passed over unless again."""
    from . import registry
    plan = plan or json.loads(PLAN.read_text(encoding="utf-8"))
    houses = {h.id: h.name for h in registry.load().houses}
    problems = check_plan(plan, houses)
    if problems:
        raise SystemExit("the plan is not fit to draw: " + "; ".join(problems))
    starter = starter or F._painter("Starter")
    put = put or F.put_files
    seal_fn = seal_fn or seal
    run = json.loads(RUN.read_text(encoding="utf-8")) if RUN.exists() else {}
    rows = {r["name"]: r for r in run.get("rows", [])}
    seeds_of = lambda h: plan.get("seeds_for", {}).get(h, plan["seeds"])     # a house drawn again gets seeds of its own
    todo = [h for h in sorted(plan["houses"]) if (not only or h in only)]
    if not again:
        todo = [h for h in todo if not all(f"{h}-{s}" in rows for s in seeds_of(h))]
    w, hgt = plan["draw_size"]
    style, neg = plan.get("style", ""), plan["negative"]
    t0, review, drawn = time.monotonic(), {}, []
    for h in todo:
        if (time.monotonic() - t0) / 60 > budget_min:
            _log("budget", left=[x for x in todo if x not in drawn])
            break
        prompt = plan["houses"][h] + (" " + style if style else "")
        t1 = time.monotonic()
        pngs = starter.make.remote(prompt, neg, seeds_of(h), w, hgt, plan["steps"], plan["cfg"])
        secs = round(time.monotonic() - t1)
        files = {}
        for seed, png in zip(seeds_of(h), pngs):
            name = f"{h}-{seed}"
            files[f"{VOL_DIR}/png/{name}.png"] = png
            jpg = review_copy(png)
            review[f"{name}.jpg"] = jpg
            rows[name] = {"name": name, "house": h, "seed": seed, "png_sha": F.sha_of(png), "jpg_sha": F.sha_of(jpg),
                          "prompt_sha": F.sha_of(prompt.encode())[:12], "seconds": secs, "at": store.utc_now()}
        put(files)
        drawn.append(h)
        print(f"drew {h}: {len(pngs)} pictures in {secs} s", flush=True)
    out = {"version": plan.get("version"), "at": store.utc_now(), "models": json.loads(F.MODELS.read_text()) if F.MODELS.exists() else {},
           "draw_size": plan["draw_size"], "steps": plan["steps"], "cfg": plan["cfg"], "seeds": plan["seeds"],
           "negative": neg, "style": style, "rows": [rows[k] for k in sorted(rows)],
           "drawn_this_run": drawn, "minutes": round((time.monotonic() - t0) / 60, 1)}
    DIR.mkdir(parents=True, exist_ok=True)
    RUN.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    if review:
        seal_fn(review)
    _log("draw", houses=len(drawn), pictures=len(review), minutes=out["minutes"])
    return out


def web_copy(png: bytes) -> bytes:
    """the site's copy: cut to 4:5 about the middle if it is not already, 960 by 1200, WebP"""
    from PIL import Image
    img = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = img.size
    if abs(w * 5 - h * 4) > 8:
        if w * 5 > h * 4:
            nw = h * 4 // 5
            img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
        else:
            nh = w * 5 // 4
            img = img.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    img = img.resize(WEB_SIZE, Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=80, method=6)
    return buf.getvalue()


def publish(choices: dict | None = None, get=None, put=None) -> dict:
    """The chosen picture of each house as the site's copy, onto the volume; their names and hashes recorded."""
    import hashlib
    choices = choices or json.loads(CHOICES.read_text(encoding="utf-8"))
    get = get or F.get_file
    put = put or F.put_files
    files, out = {}, {}
    for h, seed in sorted(choices.items()):
        webp = web_copy(get(f"{VOL_DIR}/png/{h}-{seed}.png"))
        name = f"{h}-{hashlib.sha256(webp).hexdigest()[:12]}.webp"
        files[f"{VOL_DIR}/web/{name}"] = webp
        out[h] = {"seed": seed, "name": name, "sha1": hashlib.sha1(webp).hexdigest(), "size": len(webp)}
    put(files)
    old = json.loads(WEB.read_text(encoding="utf-8")) if WEB.exists() else {}
    rec = {"at": store.utc_now(), "size": list(WEB_SIZE), "url": old.get("url", ""), "pictures": out}
    DIR.mkdir(parents=True, exist_ok=True)
    WEB.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    _log("publish", pictures=len(out))
    return rec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m adtone.issues")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("draw")
    d.add_argument("--only", default="")
    d.add_argument("--again", action="store_true")
    d.add_argument("--budget-min", type=float, default=150)
    sub.add_parser("publish")
    a = ap.parse_args(argv)
    if a.cmd == "draw":
        out = draw(only=[x for x in a.only.split(",") if x] or None, again=a.again, budget_min=a.budget_min)
        print(f"{len(out['drawn_this_run'])} houses drawn in {out['minutes']} min")
    elif a.cmd == "publish":
        out = publish()
        print(f"{len(out['pictures'])} pictures published to the volume")
    return 0


if __name__ == "__main__":
    sys.exit(main())
