"""What the houses say, read into the same questions as their images: the words half of Alignment.

    python -m adtone.statements read --run <id>   # each statement in reference/statements.csv, once
    python -m adtone.statements align             # writes data/results/alignment.json

Exploratory, outside the pre-registration.

A statement is a house's own text about a collection (show notes, a manifesto, the designer's words
quoted in a review), copied with its source into reference/statements.csv. The words reader
(rubric/words-v1.md) is given the text alone and answers the image rubric's questions with the
same fixed answers, or not_said where the text does not speak to a question; most texts speak to a
few. Its reply is checked against the rubric's schema, and nothing outside the fixed answers can be
recorded. The reader is Claude (ADTONE_CLAUDE_MODEL) at temperature 0; each reading is kept with
the rubric's hash and the model, which together are the instrument.

Alignment. For each statement and each question it speaks to, the share of the house's homepage
images in the six months from the show that give the stated answer, against the same share among
the other houses' images in the same months: the lift. A mood the text names is scored the same
way (the share of images read as having it), and the street-to-couture scale by the difference of
means. The statement's agreement is the mean lift over the questions it speaks to, with a
permutation p from shuffling which images are the house's. A positive agreement means the images
lean the way the words do, more than the market's images do in the same months.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np

from . import config, registry, store
from .score import ScoreError, load_rubric

STATEMENTS_FILE = config.ROOT / "reference" / "statements.csv"
RUBRIC = "words-v1"
WINDOW_MONTHS = 6
MIN_IMAGES = 6
N_PERM = 999
_OBJ = re.compile(r"\{.*\}", re.S)


def paths() -> dict[str, Path]:
    return {"readings": config.DATA / "statements" / "readings.jsonl", "state": config.STATE_DIR / "statements.json",
            "prov": config.PROV_DIR / "statements.jsonl", "result": config.RESULTS_DIR / "alignment.json"}


def statements(path: Path | None = None) -> list[dict]:
    with (path or STATEMENTS_FILE).open(encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("excerpt") or "").strip()]
    for r in rows:
        r["key"] = key_of(r)
    return rows


def key_of(r: dict) -> str:
    """A statement's identity: its house, show date and text, so an edited text is read again."""
    h = hashlib.sha256(r["excerpt"].strip().encode("utf-8")).hexdigest()[:12]
    return f"{r['house']}:{r['show_date']}:{h}"


def rubric_sha(root: Path = config.RUBRIC_DIR) -> str:
    return (root / f"{RUBRIC}.sha256").read_text().strip()


def validate(obj: dict, spec: dict) -> dict:
    """Exactly the rubric's keys, each within its fixed answers."""
    keys = set(spec["enums"]) | set(spec["lists"]) | set(spec["integers"])
    if set(obj) != keys:
        raise ScoreError(f"keys: missing {sorted(keys - set(obj))}, unexpected {sorted(set(obj) - keys)}")
    for k, allowed in spec["enums"].items():
        if obj[k] not in allowed:
            raise ScoreError(f"{k}={obj[k]!r} not in rubric")
    for k, rule in spec["lists"].items():
        v = obj[k]
        if not isinstance(v, list) or not (rule["min"] <= len(v) <= rule["max"]) or any(x not in rule["options"] for x in v) \
                or len(set(v)) != len(v):
            raise ScoreError(f"{k}={v!r} violates rubric")
    for k, rule in spec["integers"].items():
        v = obj[k]
        if isinstance(v, bool) or not isinstance(v, int) or not (rule["min"] <= v <= rule["max"]):
            raise ScoreError(f"{k}={v!r} violates rubric")
    return obj


class WordsReader:
    def __init__(self, model: str = config.CLAUDE_MODEL, client=None, max_retries: int = 5, sleep=time.sleep):
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.rubric = load_rubric(RUBRIC)
        self.client, self.model, self.max_retries, self.sleep = client, model, max_retries, sleep
        self.instrument = f"{RUBRIC}@{model}@{rubric_sha()[:12]}"
        self.calls = 0

    def _call(self, text: str, nudge: bool) -> str:
        msg = ("Read this text. Return only the JSON object.\n\n<text>\n" + text.strip() + "\n</text>"
               + ("\n\nYour previous reply did not match the required keys and values exactly." if nudge else ""))
        attempt = 0
        while True:
            self.calls += 1
            try:
                resp = self.client.messages.create(model=self.model, max_tokens=600, temperature=0,
                                                   system=self.rubric.prompt,
                                                   messages=[{"role": "user", "content": msg}])
                return "".join(getattr(b, "text", "") for b in resp.content)
            except Exception as e:   # SDK error classes vary by version; retry on status alone
                status = getattr(e, "status_code", None)
                if status not in (408, 409, 429, 500, 502, 503, 504, 529) or attempt >= self.max_retries:
                    raise ScoreError(f"API: {e.__class__.__name__} {status}") from None
                self.sleep(min(120, 4 * 2 ** attempt))
                attempt += 1

    def _parse(self, text: str) -> dict:
        m = _OBJ.search(text or "")
        if not m:
            raise ScoreError("no JSON object in response")
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise ScoreError(f"bad JSON: {e}") from None
        return validate(obj, self.rubric.spec)

    def read(self, text: str) -> dict:
        try:
            return self._parse(self._call(text, nudge=False))
        except ScoreError as first:
            if str(first).startswith("API:"):
                raise
            return self._parse(self._call(text, nudge=True))


def read_all(reader: WordsReader, rows: list[dict], run: str) -> dict:
    P = paths()
    done = {(r["key"], r["instrument"]) for r in store.read_jsonl(P["readings"]) if r.get("status") == "ok"}
    new, counts, stopped = [], {"read": 0, "invalid": 0, "already": 0}, None
    for r in rows:
        if (r["key"], reader.instrument) in done:
            counts["already"] += 1
            continue
        rec = {"key": r["key"], "house": r["house"], "show_date": r["show_date"], "season": r["season"],
               "instrument": reader.instrument, "run": run, "read_at": store.utc_now()}
        try:
            rec["output"], rec["status"] = reader.read(r["excerpt"]), "ok"
            counts["read"] += 1
        except ScoreError as e:
            if str(e).startswith("API:"):
                stopped = str(e)
                break
            rec["status"], rec["error"] = "invalid", str(e)[:300]
            counts["invalid"] += 1
        new.append(rec)
    store.append_jsonl(P["readings"], new)
    out = {"run": run, "updated_at": store.utc_now(), **counts, "statements": len(rows), "stopped": stopped,
           "instrument": reader.instrument}
    store.write_state(P["state"], out)
    store.append_jsonl(P["prov"], [out])
    return out


# ---------- alignment ----------

def _months_from(show: date, n: int = WINDOW_MONTHS) -> list[str]:
    out, y, m = [], show.year, show.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def spoken(words: dict) -> list[tuple[str, object]]:
    """(question, stated answer) for every question the text speaks to; moods one by one."""
    out = [(q, a) for q, a in words.items() if isinstance(a, str) and a != "not_said"]
    out += [("mood", m) for m in words.get("mood") or []]
    if words.get("street_couture_axis"):
        out.append(("street_couture_axis", int(words["street_couture_axis"])))
    return out


def _hits(images: list[dict], q: str, a) -> np.ndarray:
    if q == "mood":
        return np.array([1.0 if a in (im.get("mood") or []) else 0.0 for im in images])
    if q == "street_couture_axis":
        return np.array([float(im.get(q)) if isinstance(im.get(q), (int, float)) else np.nan for im in images])
    return np.array([1.0 if im.get(q) == a else 0.0 for im in images])


def align_one(words: dict, mine: list[dict], peers: list[dict], n_perm: int = N_PERM, rng=None) -> dict:
    """Lift per spoken question (house share minus peer share; for the scale, the difference of means in
    points), the mean lift over questions with share answers, and its permutation p."""
    rng = rng or np.random.default_rng(0)
    items = spoken(words)
    rows, share_qs = [], []
    allims = mine + peers
    for q, a in items:
        h = _hits(allims, q, a)
        hm, hp = h[:len(mine)], h[len(mine):]
        with np.errstate(invalid="ignore"):
            lift = float(np.nanmean(hm) - np.nanmean(hp)) if len(hp) else float("nan")
        rows.append({"question": q, "stated": a, "house": round(float(np.nanmean(hm)), 3),
                     "peers": round(float(np.nanmean(hp)), 3) if len(hp) else None,
                     "lift": None if np.isnan(lift) else round(lift, 3)})
        if q != "street_couture_axis":
            share_qs.append(h)
    out = {"questions": rows, "n_house": len(mine), "n_peers": len(peers)}
    if not share_qs or not peers:
        return out
    H = np.vstack(share_qs)        # questions x images
    n = len(mine)

    def mean_lift(idx_house: np.ndarray) -> float:
        mask = np.zeros(H.shape[1], bool)
        mask[idx_house] = True
        return float(np.mean(H[:, mask].mean(axis=1) - H[:, ~mask].mean(axis=1)))
    obs = mean_lift(np.arange(n))
    null = np.array([mean_lift(rng.permutation(H.shape[1])[:n]) for _ in range(n_perm)])
    out.update({"agreement": round(obs, 3), "p_two_sided": round((1 + int((np.abs(null) >= abs(obs) - 1e-12).sum())) / (1 + n_perm), 4)})
    return out


def align(readings: list[dict], images: list[dict], n_perm: int = N_PERM, seed: int = 11) -> list[dict]:
    """Each read statement against the homepage images in the six months from its show."""
    rng = np.random.default_rng(seed)
    by_month: dict[str, list[dict]] = defaultdict(list)
    for im in images:
        by_month[im["month"]].append(im)
    out = []
    for r in readings:
        months = _months_from(date.fromisoformat(r["show_date"]))
        window = [im for m in months for im in by_month.get(m, [])]
        mine = {im["sha"]: im["out"] for im in window if im["house"] == r["house"]}
        peers = {im["sha"]: im["out"] for im in window if im["house"] != r["house"] and im["sha"] not in mine}
        row = {"house": r["house"], "show_date": r["show_date"], "season": r["season"], "key": r["key"],
               "spoken": len(spoken(r["output"])), "months": f"{months[0]} to {months[-1]}"}
        if len(mine) < MIN_IMAGES:
            row["note"] = f"{len(mine)} homepage images in the window; at least {MIN_IMAGES} needed"
        elif not spoken(r["output"]):
            row["note"] = "the text speaks to none of the questions"
        else:
            row.update(align_one(r["output"], list(mine.values()), list(peers.values()), n_perm, rng))
        out.append(row)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.statements")
    ap.add_argument("stage", choices=["read", "align"])
    ap.add_argument("--run", default=config.run_id())
    a = ap.parse_args(argv)
    if a.stage == "read":
        if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
            store.append_jsonl(paths()["prov"], [{"run": a.run, "updated_at": store.utc_now(),
                                                 "waiting": "no ANTHROPIC_API_KEY for the words reader"}])
            print("::notice::statements: no ANTHROPIC_API_KEY secret; nothing read")
            return 0
        try:
            out = read_all(WordsReader(), statements(), a.run)
        except Exception as e:
            out = {"run": a.run, "updated_at": store.utc_now(), "crashed": f"{e.__class__.__name__}: {str(e)[:300]}"}
            store.append_jsonl(paths()["prov"], [out])
            print(f"::error::statements crashed, recorded in provenance: {out['crashed']}")
            return 1
        print(out)
        return 0
    from .character import load_homepage_images
    readings = [r for r in store.read_jsonl(paths()["readings"]) if r.get("status") == "ok"]
    latest: dict[str, dict] = {}
    for r in readings:
        latest[r["key"]] = r
    current = {s["key"] for s in statements()}
    rows = align([r for k, r in latest.items() if k in current], load_homepage_images(None))
    res = {"generated_at": store.utc_now(), "status": "exploratory: descriptive, not in the pre-registration",
           "images": "homepages, every creative type", "window_months": WINDOW_MONTHS, "statements": rows}
    paths()["result"].parent.mkdir(parents=True, exist_ok=True)
    paths()["result"].write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    scored = [r for r in rows if "agreement" in r]
    print(f"alignment: {len(rows)} statements read, {len(scored)} with enough images; "
          f"{sum(1 for r in scored if r['agreement'] > 0)} lean the way their words do")
    return 0


if __name__ == "__main__":
    sys.exit(main())
