"""Pin, then measure, the Modal reader.

    python -m adtone.reader_pilot record-pin /tmp/pin.log [--force]   # after `modal run reader/modal_app.py`
    python -m adtone.reader_pilot pilot --n 64                          # after `modal deploy reader/modal_app.py`

The pin is recorded once. The instrument is named by its weights' commit, so the workflow never changes
a recorded revision unless a person asks for a re-pin, which is only legitimate before the freeze.

The pilot sends generated test images (shapes and gradients drawn here, never photographs) through the
deployed reader, and records cold start, warm throughput, the share of replies that pass the rubric,
and what 10,000 images would cost at Modal's published rate for the GPU in use. It measures plumbing and
price, not reading: whether the reader reads fashion advertising well is the human check's job.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time

import numpy as np
from PIL import Image, ImageDraw

from . import config, store

# Modal's published per-second GPU prices, modal.com/pricing, read 6 October 2026.
GPU_PRICE_PER_S = {"T4": 0.000164, "L4": 0.000222, "A10G": 0.000306, "L40S": 0.000542,
                   "A100-40GB": 0.000583, "A100-80GB": 0.000694, "H100": 0.001097}
_PIN = re.compile(r"ADTONE_PIN (\{.*\})")


def record_pin(log_text: str, force: bool = False, path=config.READER_STATE) -> dict:
    m = _PIN.search(log_text)
    if not m:
        raise SystemExit("no ADTONE_PIN line in the log: the pin step did not finish")
    pin = json.loads(m.group(1))
    if not re.fullmatch(r"[0-9a-f]{40}", pin.get("revision", "")):
        raise SystemExit(f"not a commit hash: {pin.get('revision')!r}")
    state = store.read_state(path)
    if state.get("revision") and state["revision"] != pin["revision"] and not force:
        raise SystemExit(f"a different revision is already recorded ({state['revision'][:12]}); "
                         "re-pinning changes the instrument and needs --force, before the freeze only")
    state.update({"model": pin["model"], "revision": pin["revision"], "pinned_at": store.utc_now()})
    store.write_state(path, state)
    return state


def synthetic_jpegs(n: int, seed: int = 0, size=(1080, 1350)) -> list[bytes]:
    """Drawn test cards: varied colour, shapes and text-free composition, at a typical ad size."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        top, bottom = rng.integers(0, 256, 3), rng.integers(0, 256, 3)
        grad = np.linspace(0, 1, size[1])[:, None, None]
        arr = (top * (1 - grad) + bottom * grad).astype(np.uint8) * np.ones((1, size[0], 1), np.uint8)
        img = Image.fromarray(arr, "RGB")
        d = ImageDraw.Draw(img)
        for _ in range(int(rng.integers(2, 7))):
            x0, y0 = int(rng.integers(0, size[0] - 100)), int(rng.integers(0, size[1] - 100))
            x1, y1 = x0 + int(rng.integers(60, 500)), y0 + int(rng.integers(60, 600))
            fill = tuple(int(v) for v in rng.integers(0, 256, 3))
            (d.ellipse if rng.random() < 0.5 else d.rectangle)([x0, y0, x1, y1], fill=fill)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)
        out.append(buf.getvalue())
    return out


def pilot(remote, rubric, gpu: str, n: int = 64, batch: int = 8, clock=time.monotonic) -> dict:
    """remote(jpegs, system, schema, grammar) returns replies: strings, or dicts carrying the text with
    why it stopped and how long it ran. A failure of the remote itself is recorded, not raised, so the
    record survives a reader that does not start."""
    from collections import Counter

    from .score import ScoreError, gbnf, json_schema, parse
    schema, grammar = json_schema(rubric), gbnf(rubric)
    jpegs = synthetic_jpegs(n)
    batches = [jpegs[i:i + batch] for i in range(0, len(jpegs), batch)]
    times, valid, invalid, errors, samples = [], 0, 0, [], []
    finish, tokens, modes, mode_errors = Counter(), [], Counter(), []
    failure = None
    for b in batches:
        t0 = clock()
        try:
            replies = remote(b, rubric.prompt, schema, grammar)
        except Exception as e:
            failure = f"{e.__class__.__name__}: {str(e)[:400]}"
            break
        times.append(clock() - t0)
        for r in replies:
            text = r["text"] if isinstance(r, dict) else r
            if isinstance(r, dict):
                finish[str(r.get("finish_reason"))] += 1
                tokens.append(int(r.get("tokens") or 0))
                modes[str(r.get("mode"))] += 1
                if r.get("mode_errors") and not mode_errors:
                    mode_errors = list(r["mode_errors"])
            if len(samples) < 3:
                samples.append({"chars": len(text or ""), "head": (text or "")[:160], "tail": (text or "")[-60:]})
            try:
                parse(text, rubric)
                valid += 1
            except ScoreError as e:
                invalid += 1
                errors.append(str(e)[:120])
    out = {"gpu": gpu, "images": n, "batch": batch, "valid": valid, "invalid": invalid,
           "valid_share": round(valid / max(1, valid + invalid), 3), "sample_errors": errors[:5],
           "sample_replies": samples, "finish_reasons": dict(finish), "modes": dict(modes),
           "mode_errors": mode_errors[:3],
           "tokens_mean": round(sum(tokens) / len(tokens), 1) if tokens else None,
           "tokens_max": max(tokens) if tokens else None,
           "price_source": "modal.com/pricing, read 6 October 2026"}
    if failure:
        out["failure"] = failure
    if not times:
        return {**out, "cold_start_s": None, "warm_seconds_per_image": None, "cost_per_10k_images_usd": None}
    warm = times[1:] or times
    warm_images = sum(len(b) for b in batches[1:len(times)]) or len(batches[0])
    sec_per_image = sum(warm) / warm_images
    price = GPU_PRICE_PER_S.get(gpu)
    return {**out, "cold_start_s": round(times[0], 1), "warm_seconds_per_image": round(sec_per_image, 3),
            "cost_per_10k_images_usd": None if price is None else round(sec_per_image * price * 10_000, 2)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("record-pin")
    rp.add_argument("log")
    rp.add_argument("--force", action="store_true")
    pp = sub.add_parser("pilot")
    pp.add_argument("--n", type=int, default=64)
    a = ap.parse_args(argv)
    if a.cmd == "record-pin":
        st = record_pin(open(a.log, encoding="utf-8").read(), force=a.force)
        print(f"pinned {st['model']} at {st['revision'][:12]}")
        return 0
    import modal
    from .score import load_rubric
    reader = modal.Cls.from_name(config.MODAL_APP, "Reader")()
    ident = reader.identity.remote()
    if ident.get("revision") != config.open_model_revision():
        print(f"::error::deployed reader runs {ident.get('revision')!r}, recorded pin is {config.open_model_revision()!r}")
        return 1
    res = pilot(reader.read_meta.remote, load_rubric(), ident.get("gpu", "L4"), n=a.n)
    res = {"run": config.run_id(), "at": store.utc_now(), "instrument": config.instrument("modal"),
           "vllm": ident.get("vllm"), **res}
    store.append_jsonl(config.PROV_DIR / "reader.jsonl", [res])
    state = store.read_state(config.READER_STATE)
    state["last_pilot"] = res
    store.write_state(config.READER_STATE, state)
    print(json.dumps(res, indent=2))
    if res["valid_share"] < 0.95:
        print("::error::fewer than 95% of pilot replies passed the rubric; the reader is not fit to run")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
