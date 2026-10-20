"""The shared description of the clothes (PREREGISTRATION-AMENDMENT-3.md, a draft, section 2): each collection's
position on the runway and in its advertising for the collection, and the gap between the two (section 4).

    python -m adtone.shared          # -> data/results/shared.json

Every picture is read with rubric/clothes-v1.md, and only the questions and options that stand are used
(data/clothes/judge.json): all of them provisional until the labels decide them. A collection's position in a
channel is the share of its pictures giving each answer (for a list, the share choosing each option; for street to
couture, the mean), each answer's shares among the pictures the question applies to. Each channel is placed against
its own kind: positions are standardised across the houses in the same channel and season, so a runway is compared
with other runways and an advertisement with other advertisements. A position needs 8 pictures: 8 looks of the show
read as an outfit worn, or 8 advertisements for the season's collection first seen in the campaign window.

Which advertisements. The paid social libraries' ads (adtone/adlibs.py), fashion division, on campaign windows their
record covers whole (adtone/thread.py, social_window): before that they hold only the ads still running a year
later. Ads are joined into concepts as adtone/concepts.py joins them, by a shared picture or one within a few bits
of perceptual hash, across the libraries. A concept is the collection's when one of its ads' own text names the
collection and none names another line, or, where the text names nothing, when one of its pictures shows a full
outfit worn (adtone/runway.py, collection_sorter); the concepts first seen in the campaign window are the
collection's campaign, and their distinct pictures read for the clothes make its position. Main collections only:
the campaign window runs to the house's next main show.

The gap is one minus the cosine between a collection's standardised runway and advertising positions, on the answers
both have: a wide gap is a strategy (the show as statement, the advertising as commerce), not a fault, and no gap is
called better.

Description only. The amendment is a draft, and positions computed before its freeze are description. Its
registered test reads its first result from the collections whose advertising positions were not computed before
the freeze, the spring-summer 2027 campaign windows onward, so no advertising position is computed here for those
windows until the amendment's hash is on file (HELD_FROM). Section 3's transmission statistic is not computed here.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date

import numpy as np

from . import config, store

RESULTS = config.RESULTS_DIR / "shared.json"
THREAD = config.DATA / "thread" / "collections.jsonl"
LIBRARIES = ("metaweb", "snap", "pinterest")
FROZEN = config.ROOT / "PREREGISTRATION-AMENDMENT-3.sha256"
MIN_PICTURES = 8         # looks of the show, or advertisements for the collection, before a position (section 2)
MIN_ANSWERS = 3          # pictures answering a question before its shares enter a position (adtone/clothes.py)
MIN_PLACED = 4           # positions of a channel in a season before they are standardised (adtone/benchmark.py)
MIN_COMMON = 10          # standardised answers two positions share before a gap is given
HELD_FROM = (2027, 0)    # spring-summer 2027: the registered test's first campaign windows


def season_order(season: str) -> tuple:
    """Spring-summer of a year is shown the autumn before its autumn-winter: 2026 SS, then 2026 AW."""
    p = season.split()
    return (int(p[0]), 0 if p[1] == "SS" else 1) if len(p) >= 2 and p[0].isdigit() else (0, 0)


def season_of(c: dict) -> str:
    p = c["season"].split()
    return f"{p[0]} {p[1]}" if len(p) >= 2 else c["season"]


def held(c: dict, frozen: bool | None = None) -> bool:
    """Whether a collection's advertising position waits for the amendment's freeze."""
    frozen = FROZEN.exists() if frozen is None else frozen
    return not frozen and season_order(c["season"]) >= HELD_FROM


def components(spec: dict, judge: dict) -> list[tuple[str, object]]:
    """The answers a position is made of: (question, option) for each option that stands, and (question, None) for
    street to couture's mean."""
    from . import clothes as C
    out = []
    for q in judge.get("use") or []:
        if q in C.NOT_CLOTHES:
            continue
        if q in spec.get("integers", {}):
            out.append((q, None))
        elif q in spec.get("lists", {}):
            opts = (judge.get("options") or {}).get(q) or spec["lists"][q]["options"]
            out += [(q, o) for o in opts if o not in C.NOT_JUDGED]
        elif q in spec.get("enums", {}):
            out += [(q, v) for v in spec["enums"][q] if v not in C.NOT_JUDGED]
    return out


def position(answers: list[dict], comps: list[tuple[str, object]], min_answers: int = MIN_ANSWERS) -> np.ndarray:
    """One channel's position: for each question, the share of the pictures it applies to and answers that give each
    option (any of a list's), or the mean for street to couture; missing where fewer pictures answer it."""
    from . import clothes as C
    vec = np.full(len(comps), np.nan)
    by_q = defaultdict(list)
    for i, (q, _) in enumerate(comps):
        by_q[q].append(i)
    for q, idx in by_q.items():
        apply = [a for a in answers if C.applies(q, a.get("subject"))]
        if comps[idx[0]][1] is None:
            vals = [a.get(q) for a in apply if isinstance(a.get(q), (int, float)) and not isinstance(a.get(q), bool)]
            if len(vals) >= min_answers:
                vec[idx[0]] = float(np.mean(vals))
            continue
        opts = [comps[i][1] for i in idx]
        rows = []
        for a in apply:
            v = a.get(q)
            got = set(v) if isinstance(v, list) else {v}
            hit = [o in got for o in opts]
            if any(hit):
                rows.append(hit)
        if len(rows) >= min_answers:
            vec[idx] = np.array(rows, float).mean(0)
    return vec


def standardise(positions: dict[str, np.ndarray], min_placed: int = MIN_PLACED) -> dict[str, np.ndarray]:
    """Each answer as a z-score among the positions of one channel and season that have it; missing where fewer
    than `min_placed` have it or they do not vary."""
    if len(positions) < min_placed:
        return {}
    ids = sorted(positions)
    M = np.array([positions[i] for i in ids])
    Z = np.full(M.shape, np.nan)
    for j in range(M.shape[1]):
        col = M[:, j]
        ok = ~np.isnan(col)
        if ok.sum() >= min_placed:
            sd = float(col[ok].std())
            if sd > 0:
                Z[ok, j] = (col[ok] - col[ok].mean()) / sd
    return {i: Z[k] for k, i in enumerate(ids)}


def gap(a: np.ndarray, b: np.ndarray, min_common: int = MIN_COMMON) -> tuple[float | None, int]:
    """One minus the cosine between two standardised positions, on the answers both have."""
    ok = ~np.isnan(a) & ~np.isnan(b)
    n = int(ok.sum())
    if n < min_common:
        return None, n
    x, y = a[ok], b[ok]
    nx, ny = float(np.linalg.norm(x)), float(np.linalg.norm(y))
    if not nx or not ny:
        return None, n
    return round(1 - float(x @ y) / (nx * ny), 4), n


# ---------- the advertising ----------

def load_ads(libraries: tuple[str, ...] = LIBRARIES) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    """The fashion division's ads of each house, with the day each was first shown, its text-sorted line and its
    pictures, and every picture's clothes answers."""
    from . import adlibs
    ads, answers = defaultdict(list), {}
    for name in libraries:
        P = adlibs.paths(name)
        ob = P["obs"] / "clothes-v1.jsonl"
        for r in store.read_jsonl(ob) if ob.exists() else []:
            if r.get("status") == "ok" and r.get("sha") and r.get("output"):
                answers[r["sha"]] = r["output"]
        for p in sorted(P["ads"].glob("*.jsonl")) if P["ads"].exists() else []:
            for r in store.read_jsonl(p):
                start = (r.get("first_shown") or r.get("start") or "")[:10]
                if r.get("division", "fashion") != "fashion" or not r.get("house_id") or len(start) != 10:
                    continue
                pics = [(m["sha"], m.get("phash")) for m in r.get("media") or [] if m.get("sha")]
                ads[r["house_id"]].append({"ad_id": f"{name}:{r.get('ad_id')}", "first": date.fromisoformat(start),
                                           "line": r.get("line"), "pictures": pics})
    return dict(ads), answers


def concepts(house_ads: list[dict], phash_max: int = config.PHASH_MAX_DIST) -> list[dict]:
    """One house's ads joined into concepts by a shared picture or one within `phash_max` bits of perceptual hash
    (adtone/concepts.py), across the libraries: each concept with the day it was first seen, its ads' lines and its
    distinct pictures."""
    from .concepts import _UF, _popcount64
    ids = [a["ad_id"] for a in house_ads]
    uf = _UF(ids)
    owner, flat = {}, []
    for a in house_ads:
        for sha, ph in a["pictures"]:
            if sha in owner:
                uf.union(a["ad_id"], owner[sha])
            else:
                owner[sha] = a["ad_id"]
                if ph:
                    flat.append((a["ad_id"], ph))
    if len(flat) > 1:
        h = np.array([int(p, 16) for _, p in flat], dtype=np.uint64)
        for i in range(len(flat) - 1):
            for j in np.nonzero(_popcount64(h[i] ^ h[i + 1:]) <= phash_max)[0]:
                uf.union(flat[i][0], flat[i + 1 + int(j)][0])
    groups = defaultdict(list)
    by_id = {a["ad_id"]: a for a in house_ads}
    for i in ids:
        groups[uf.find(i)].append(by_id[i])
    return [{"first_seen": min(a["first"] for a in g), "ads": len(g), "lines": [a["line"] for a in g],
             "shas": sorted({s for a in g for s, _ in a["pictures"]})} for g in groups.values()]


def for_the_collection(concept: dict, answers: dict[str, dict]) -> bool:
    """A concept advertises the season's collection: by its ads' own text, or, where the text names nothing, by a
    picture of a full outfit worn (adtone/runway.py, collection_sorter)."""
    from .runway import concept_line
    line = concept_line(concept["lines"])
    return line == "collection" or (line == "other" and any((answers.get(s) or {}).get("subject") == "worn_full"
                                                          for s in concept["shas"]))


def campaign(cs: list[dict], show: date, window: tuple[int, int], answers: dict[str, dict]) -> dict:
    """The collection's campaign: its concepts first seen in the campaign window, the ads in them and their
    distinct pictures read for the clothes."""
    mine = [c for c in cs if window[0] <= (c["first_seen"] - show).days <= window[1] and for_the_collection(c, answers)]
    shas = sorted({s for c in mine for s in c["shas"] if s in answers})
    return {"concepts": len(mine), "ads": sum(c["ads"] for c in mine), "shas": shas}


# ---------- the build ----------

def build(collections: list[dict], looks: list[dict], readings: dict[str, dict], ads: dict[str, list[dict]],
          ad_answers: dict[str, dict], spec: dict, judge: dict, frozen: bool | None = None) -> dict:
    from .thread import LOOKS_MATCH_DAYS
    comps = components(spec, judge)
    mains = sorted((c for c in collections if c.get("main")), key=lambda c: (c["house"], c["date"]))
    by_show = defaultdict(lambda: defaultdict(list))
    for x in looks:
        if x.get("outfit") and x.get("sha") in readings:
            by_show[(x["house"], x.get("category"))][x["date"]].append(x["sha"])
    house_concepts = {h: concepts(a) for h, a in ads.items()}
    rows, pos = [], {"runway": defaultdict(dict), "advertising": defaultdict(dict)}
    for c in mains:
        d = date.fromisoformat(c["date"])
        s = season_of(c)
        near = sorted((abs((date.fromisoformat(k) - d).days), k) for k in by_show.get((c["house"], c["category"]), {})
                      if abs((date.fromisoformat(k) - d).days) <= LOOKS_MATCH_DAYS)
        lk = by_show[(c["house"], c["category"])][near[0][1]] if near else []
        row = {"id": c["id"], "house": c["house"], "season": c["season"], "date": c["date"],
               "runway": {"looks": len(lk), "placed": len(lk) >= MIN_PICTURES}, "advertising": {}}
        if len(lk) >= MIN_PICTURES:
            pos["runway"][s][c["id"]] = position([readings[x] for x in lk], comps)
        rec = ((c.get("advertising") or {}).get("paid_social_record") or {}).get("campaign_period")
        if held(c, frozen):
            row["advertising"] = {"held": True}
        elif rec != "complete":
            row["advertising"] = {"record": rec or "not read"}
        else:
            got = campaign(house_concepts.get(c["house"], []), d, tuple(c["campaign_window"]), ad_answers)
            row["advertising"] = {"record": rec, "concepts": got["concepts"], "ads": got["ads"], "pictures": len(got["shas"]),
                                  "placed": got["ads"] >= MIN_PICTURES and len(got["shas"]) >= MIN_ANSWERS}
            if row["advertising"]["placed"]:
                pos["advertising"][s][c["id"]] = position([ad_answers[x] for x in got["shas"]], comps)
        rows.append(row)
    z = {ch: {} for ch in pos}
    for ch, by_season in pos.items():
        for s, ps in by_season.items():
            z[ch].update(standardise(ps))
    for r in rows:
        zr, za = z["runway"].get(r["id"]), z["advertising"].get(r["id"])
        if zr is not None and za is not None:
            g, n = gap(zr, za, MIN_COMMON)
            if g is not None:
                r["gap"] = {"value": g, "answers": n}
    seasons = defaultdict(lambda: {"runways": 0, "advertising": 0, "gaps": []})
    for r in rows:
        k = " ".join(r["season"].split()[:2])
        seasons[k]["runways"] += r["id"] in z["runway"]
        seasons[k]["advertising"] += r["id"] in z["advertising"]
        if r.get("gap"):
            seasons[k]["gaps"].append(r["gap"]["value"])
    summary = {k: {"runways_placed": v["runways"], "advertising_placed": v["advertising"], "gaps": len(v["gaps"]),
                   **({"median_gap": round(float(np.median(v["gaps"])), 4)} if v["gaps"] else {})}
               for k, v in sorted(seasons.items(), key=lambda kv: season_order(kv[0])) if v["runways"] or v["advertising"]}
    return {"generated_at": store.utc_now(),
            "status": ("description: PREREGISTRATION-AMENDMENT-3.md is a draft and every clothes question is "
                       f"{judge.get('status', 'provisional')} until labelled; no advertising position is computed for "
                       "the spring-summer 2027 campaign windows onward before the freeze, and nothing here is tested"),
            "questions": [q for q in judge.get("use") or []], "answers": len(comps),
            "min": {"pictures": MIN_PICTURES, "answers": MIN_ANSWERS, "placed": MIN_PLACED, "common": MIN_COMMON},
            "runways_placed": len(z["runway"]), "advertising_placed": len(z["advertising"]),
            "gaps": sum(1 for r in rows if r.get("gap")),
            "advertising_held": sum(1 for r in rows if r["advertising"].get("held")),
            "by_season": summary, "collections": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m adtone.shared")
    ap.parse_args(argv)
    from . import clothes as C
    from .score import load_rubric
    from .thread import runway_looks
    judge_path = C.DIR / "judge.json"
    if not judge_path.exists() or not THREAD.exists():
        print("shared: waits for the clothes judge and the Thread's collections")
        return 0
    judge = json.loads(judge_path.read_text(encoding="utf-8"))
    if not judge.get("use"):
        print("shared: no clothes question stands yet")
        return 0
    ads, ad_answers = load_ads()
    out = build(store.read_jsonl(THREAD), runway_looks(), C.readings(), ads, ad_answers,
                load_rubric(C.VERSION).spec, judge)
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"shared: {out['runways_placed']} runways and {out['advertising_placed']} advertising positions placed, "
          f"{out['gaps']} gaps, {out['advertising_held']} held for the freeze")
    return 0


if __name__ == "__main__":
    sys.exit(main())
