"""The season benchmark: each brand's collection followed through the stages and set against the other
brands of the same season, like for like.

    python -m adtone.benchmark        # after `python -m adtone.thread build`
                                      # -> data/results/benchmark.json and data/results/benchmark.csv

For every main collection in data/thread/collections.jsonl:

  the show          the jump in attention against the season, whether it lasted (30 to 120 days after, net
                    of the median brand), the momentum the brand brought in, press volume and tone;
  the scene         ambassador appointments in the 180 days before;
  the shop window   the homepage pictures first shown after the show and before the brand's next main show
                    (six months at most), so that one season's window never overlaps the next. Pictures are
                    dated by month: a month belongs to the window if its fifteenth day does. Each measure is
                    like for like, campaign pictures with campaign pictures and product with product, and is
                    also given as a percentile among the brands of the same season:
      distinctness  one less the cosine between the brand's centroid and the mean of the other brands'
                    centroids for the same months, by the fingerprint (OpenCLIP ViT-B-32);
      movement      one less the cosine between the brand's centroid and its own in the previous window;
                    both on equal numbers of pictures: every centroid is of MIN_KIND pictures drawn at random,
                    averaged over EQUAL_DRAWS draws. A centroid of many pictures is steadier than one of few,
                    so on whole sets a brand that shows many pictures looks typical and one that shows few
                    looks distinct, whatever its pictures are like;
      mix           the shares of campaign pictures, product and everything else, and how far they moved
                    from the previous window (total variation distance);
  the campaigns     what the brand published for the season, as the Thread joins it;
  the advertising   the ads first shown around the show (a week before to three weeks after) and in the
                    campaign window, as the Thread counts them: the search and video ads, by format and how many
                    advertise beauty, wherever their record covers the window (it keeps an ad for a year after
                    it last ran, so earlier windows hold only the ads that kept running), and the paid social
                    archive's once it is on file;
  the clothes       the runway looks and the shop window's pictures of an outfit worn, described with the
                    frozen clothes rubric (rubric/clothes-v1.md) on the questions that stand: for the runway
                    and for the shop window, distinctness against the season's other brands and movement
                    from the brand's own last one; the shop window's likeness to its own runway; and whether
                    the shop window looks more like its own runway than the season's others, the house's
                    signature. Likeness is net of chance at the numbers of pictures compared
                    (adtone/clothes.py). Provisional until the labels have checked the questions it rests on.
                    Whether a runway's change carries into what the house shows is Amendment 3's test, and it
                    runs on the advertising: the shop window changes too little between seasons, and its
                    early months cannot be told from the previous show's, to carry it.

Then, across collections, which shop-window measures go with attention that lasts, read on the months of
the window up to the end of the lasting window (day 120): within brands, with momentum held constant, with
bootstrap intervals. Descriptive throughout.

Picture kind is the larger reader's (Qwen3-VL-32B) where it has read the picture and the pinned smaller
reader's otherwise. The two agree on campaign picture or not at kappa 0.73; they differ mostly between
product on a model and packshot, which this groups together as product.
"""
from __future__ import annotations

import csv
import json
import sys
import zlib
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from . import config, store

THREAD_DIR = config.DATA / "thread"
HOMEPAGES = config.DATA / "homepages"
VECTORS = HOMEPAGES / "vectors" / "openclip-vitb32-laion2b"
LARGER_READER = config.DATA / "luxury" / "readings" / "qwen3-tone.jsonl"
RESULTS = config.RESULTS_DIR / "benchmark.json"
TABLE = config.RESULTS_DIR / "benchmark.csv"

SHOP_DAYS = 182          # the shop window runs at most six months after the show
ATTENTION_END = 120      # the last day of the lasting window (adtone/runway.py LASTING): the association with lasting
                         # attention reads only the shop window's months up to it, since a picture put up later
                         # cannot have moved it. Over a quarter of a window's pictures go up after day 120.
MIN_KIND = 3             # pictures of a kind, in a window, before a brand gets a value for that kind
MIN_PEERS = 3            # other brands with that kind in the same months, before distinctness is given
MIN_MIX = 3              # pictures in a window before its mix is given
EQUAL_DRAWS = 200        # draws of MIN_KIND pictures a side, so that no brand's sample size sets its place
GROUPS = ("image", "product")
GROUP_OF = {"brand_image": "image", "product_on_model": "product", "product_packshot": "product",
            "catalogue_grid": "product"}      # promotional, events, text and other: "other"
BOOT = 2000


# ---------- the pictures ----------

def advertising(adv: dict) -> dict:
    """The Thread's advertising stage for one collection: the paid social archive's ads (none on file while its
    collector is refused) and the search and video ads with how much of each window their record covers."""
    sv = adv.get("search_video") or {}
    return {"paid_social": {"show_period": adv.get("show_period", 0), "campaign_period": adv.get("campaign_period", 0)},
            "search_video": {p: sv.get(p) or {"record": "not read"} for p in ("show_period", "campaign_period")}}


def advertised(row: dict) -> bool:
    """Ads on file for either window: the paid social archive's, or search and video ads on a window their record
    covers whole."""
    a = row.get("advertising") or {}
    paid = a.get("paid_social") or {}
    sv = a.get("search_video") or {}
    return bool(paid.get("show_period") or paid.get("campaign_period")) or any(
        (sv.get(p) or {}).get("record") == "complete" and (sv.get(p) or {}).get("ads", 0) > 0
        for p in ("show_period", "campaign_period"))


def advertising_known(row: dict) -> bool:
    """Whether the record could show this collection's ads: the paid social archive's on file, or the house's own
    search and video account read over a whole window. Without either, no ads on file is not the same as none."""
    a = row.get("advertising") or {}
    paid = a.get("paid_social") or {}
    sv = a.get("search_video") or {}
    return bool(paid.get("show_period") or paid.get("campaign_period")) or any(
        (sv.get(p) or {}).get("record") == "complete" for p in ("show_period", "campaign_period"))


def kind_group(creative_type: str | None) -> str:
    return GROUP_OF.get(creative_type or "", "other")


def first_shown(captures: Path | None = None) -> dict[tuple[str, str], str]:
    """(brand, picture) -> the first month an archived capture of the homepage shows it. A reading's own month is
    the capture it was first read from, and the archive is read newest month first, so for a picture left up
    for months that is usually the last month it was shown, not the first."""
    first: dict[tuple[str, str], str] = {}
    for p in sorted(Path(captures or HOMEPAGES / "captures").glob("*.jsonl")):
        for row in store.read_jsonl(p):
            if row.get("status") != "resolved":
                continue
            for im in row.get("images") or []:
                k = (row["house_id"], im["sha"])
                if k not in first or row["month"] < first[k]:
                    first[k] = row["month"]
    return first


def load_pictures(obs_path: Path | None = None, larger: Path | None = None, vectors: Path | None = None,
                  captures: Path | None = None) -> list[dict]:
    """Every homepage picture read: brand, the month it was first shown, its kind group and fingerprint."""
    obs_path = obs_path or HOMEPAGES / "obs" / "tone-v1.jsonl"
    larger = larger or LARGER_READER
    vectors = vectors or VECTORS
    first = first_shown(captures)
    vec = {}
    for f in sorted(Path(vectors).glob("*.npz")):
        z = np.load(f, allow_pickle=True)
        for s, v in zip(z["shas"], z["vecs"]):
            n = float(np.linalg.norm(v))
            if n:
                vec[str(s)] = np.asarray(v, float) / n
    big = {}
    if Path(larger).exists():
        for r in store.read_jsonl(Path(larger)):
            big[r.get("sha")] = (r.get("out") or {}).get("creative_type")
    out = []
    for r in store.read_jsonl(Path(obs_path)):
        if r.get("status") != "ok" or r.get("sha") not in vec:
            continue
        ct = big.get(r["sha"]) or (r.get("output") or {}).get("creative_type")
        out.append({"sha": r["sha"], "house": r["house_id"], "month": first.get((r["house_id"], r["sha"]), r["month"]),
                    "kind": ct, "group": kind_group(ct), "vec": vec[r["sha"]]})
    return out


def window_months(show: date, end: date) -> list[str]:
    """The months whose fifteenth day falls after the show and on or before the window's end."""
    months, y, m = [], show.year, show.month
    while True:
        mid = date(y, m, 15)
        if mid > end:
            break
        if mid > show:
            months.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


def shop_window_end(show: date, next_main: date | None) -> date:
    end = show + timedelta(days=SHOP_DAYS)
    return min(end, next_main - timedelta(days=1)) if next_main and next_main > show else end


# ---------- the measures ----------

def _centroid(vs: list[np.ndarray]) -> np.ndarray:
    c = np.mean(vs, axis=0)
    n = float(np.linalg.norm(c))
    return c / n if n else c


def _seed(*groups: list[dict]) -> int:
    """A seed fixed by the pictures themselves, so a brand's value does not depend on the order brands are read."""
    return zlib.crc32("|".join(sorted(str(p.get("sha", i)) for ps in groups for i, p in enumerate(ps))).encode())


def _drawn(vs: list[np.ndarray], k: int, draws: int, rng: np.random.Generator) -> np.ndarray:
    """Unit centroids of k pictures drawn without replacement, one row per draw."""
    X = np.vstack(vs)
    idx = np.argsort(rng.random((draws, len(vs))), axis=1)[:, :k]
    C = X[idx].mean(axis=1)
    return C / np.linalg.norm(C, axis=1, keepdims=True)


def distinctness(brand: list[dict], peers: dict[str, list[dict]], min_kind: int = MIN_KIND,
                 min_peers: int = MIN_PEERS, draws: int = EQUAL_DRAWS) -> dict:
    """Like for like: for each kind, one less the cosine between the brand's centroid and the mean of the
    other brands' centroids of the same kind; then the kinds averaged, weighted by the brand's pictures.
    Every centroid is of min_kind pictures, drawn again and again, so a brand's number of pictures does not
    move its value: on whole sets, many pictures make a steady centroid that sits close to the market."""
    out = {}
    for g in GROUPS:
        mine = [p for p in brand if p["group"] == g]
        others = {o: [p for p in ps if p["group"] == g] for o, ps in peers.items()}
        others = {o: ps for o, ps in others.items() if len(ps) >= min_kind}
        if len(mine) >= min_kind and len(others) >= min_peers:
            rng = np.random.default_rng(_seed(mine, *others.values()))
            c = _drawn([p["vec"] for p in mine], min_kind, draws, rng)
            m = np.mean([_drawn([p["vec"] for p in ps], min_kind, draws, rng) for o, ps in sorted(others.items())], axis=0)
            m = m / np.linalg.norm(m, axis=1, keepdims=True)
            out[g] = {"value": round(float(np.mean(1 - np.sum(c * m, axis=1))), 4), "n": len(mine), "peers": len(others)}
    if out:
        w = np.array([v["n"] for v in out.values()], float)
        out["value"] = round(float(np.average([v["value"] for v in out.values()], weights=w)), 4)
    return out


def movement(now: list[dict], before: list[dict], min_kind: int = MIN_KIND, draws: int = EQUAL_DRAWS) -> dict:
    """Like for like: for each kind, one less the cosine between this window's centroid and the previous one's,
    each of min_kind pictures drawn again and again, so that larger windows do not look steadier."""
    out = {}
    for g in GROUPS:
        a = [p for p in now if p["group"] == g]
        b = [p for p in before if p["group"] == g]
        if len(a) >= min_kind and len(b) >= min_kind:
            rng = np.random.default_rng(_seed(a, b))
            ca = _drawn([p["vec"] for p in a], min_kind, draws, rng)
            cb = _drawn([p["vec"] for p in b], min_kind, draws, rng)
            out[g] = {"value": round(float(np.mean(1 - np.sum(ca * cb, axis=1))), 4), "n": min(len(a), len(b))}
    if out:
        w = np.array([v["n"] for v in out.values() if isinstance(v, dict)], float)
        out["value"] = round(float(np.average([v["value"] for v in out.values() if isinstance(v, dict)], weights=w)), 4)
    return out


def mix(pictures: list[dict]) -> dict | None:
    if len(pictures) < MIN_MIX:
        return None
    c = Counter(p["group"] for p in pictures)
    return {g: round(c[g] / len(pictures), 3) for g in ("image", "product", "other")}


def tvd(a: dict, b: dict) -> float:
    return round(0.5 * sum(abs(a.get(k, 0) - b.get(k, 0)) for k in set(a) | set(b)), 3)


def percentiles(values: dict[str, float]) -> dict[str, int]:
    """Each brand's place among the season's brands, 0 the least and 100 the most."""
    keys = sorted(values, key=lambda k: values[k])
    if len(keys) < 2:
        return {k: 50 for k in keys}
    return {k: round(100 * i / (len(keys) - 1)) for i, k in enumerate(keys)}


# ---------- the benchmark ----------

def season_order(s: str) -> tuple:
    """Spring-summer of a year is shown the autumn before its autumn-winter: 2026 SS, then 2026 AW."""
    p = s.split()
    return (int(p[0]), 0 if p[1] == "SS" else 1) if len(p) >= 2 and p[0].isdigit() else (0, 0)


def season_of(c: dict) -> str:
    """Year and half, across categories: a brand that shows menswear joins the season it shows in."""
    p = c["season"].split()
    return f"{p[0]} {p[1]}" if len(p) >= 2 else c["season"]


# ---------- the clothes ----------

CLOTHES = config.DATA / "clothes"
MIN_LOOKS = 8            # runway looks read as an outfit worn, before a show's clothes are measured
MIN_WORN = 5             # shop-window pictures of an outfit worn, before the runway is followed into them
MIN_RUNWAYS = 2          # other runways of the season, before a runway is set against them
MIN_PLACED = 4           # collections of a season with a clothes measure, before each is given a place among them
MATCH_DAYS = 7           # a runway page's show date and the collection's may differ by a few days
TOLD_APART = 0.05        # a difference that random splits of the same pictures reach less often than this


def load_clothes(path: Path | None = None) -> dict | None:
    """The reader's answers and which questions stand (data/clothes/judge.json); None before the test set
    has been read and judged."""
    from . import clothes
    from .score import load_rubric
    d = Path(path or CLOTHES)
    if not (d / "judge.json").exists():
        return None
    judge = json.loads((d / "judge.json").read_text(encoding="utf-8"))
    out = {"judge": judge}
    if judge.get("use"):
        out.update(readings=clothes.readings(d / "readings.jsonl"),
                   runway=store.read_jsonl(d / "runway.jsonl") if (d / "runway.jsonl").exists() else [],
                   spec=load_rubric(clothes.VERSION).spec)
    return out


def _mean_or_none(xs: list) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(float(np.mean(xs)), 4) if xs else None


def clothes_measures(rows: list[dict], windows: dict[str, list[dict]], cl: dict,
                     peers: dict[str, dict[str, list[dict]]] | None = None) -> dict:
    """The runway translated, and followed into the shop window, for every main collection with its looks
    read. On each show's looks that show an outfit worn:
      distinctness   one less the mean likeness to each other brand's runway of the season;
      movement       one less the likeness to the brand's own previous runway;
    on the shop window's pictures that show an outfit worn (campaign pictures and product):
      distinctness   one less the mean likeness to each other brand's in the same months;
      movement       one less the likeness to the brand's own previous window;
    and from one to the other:
      own_runway     the likeness between the runway and the shop window: a level, which mixes what every
                     house's shop window does (sell more plainly than it shows) and the house's own choices
                     with anything carried, so it is described, not read as transmission;
      recognisable   the share of the season's other runways the shop window is less like than its own: the
                     house's signature.
    Likeness is net of chance at the numbers of pictures compared (adtone/clothes.py). Every figure carries
    the standing of the questions it rests on: provisional until the labels have checked them."""
    from . import clothes as C
    judge = cl["judge"]
    status = judge.get("status")
    if not judge.get("use"):
        for r in rows:
            r["clothes"] = {"status": status}
        return {"status": status}
    ans = cl["readings"]
    worn = {s for s, a in ans.items() if C.shows_outfit(a)}
    by_show = defaultdict(list)
    for x in cl["runway"]:
        if C.page_fits(x.get("page", ""), x["category"]):      # not a house's other line kept beside the show
            by_show[(x["house"], x["category"], x["date"])].append(x["sha"])
    shows = defaultdict(list)
    for (h, cat, d), shas in by_show.items():
        shows[(h, cat)].append((date.fromisoformat(d), shas))

    looks, shop, matched = {}, {}, {}
    for r in rows:
        d = date.fromisoformat(r["date"])
        best = min(shows.get((r["house"], r["category"]), []), key=lambda s: (abs((s[0] - d).days), s[0]), default=None)
        if best is not None and abs((best[0] - d).days) <= MATCH_DAYS:
            matched[r["id"]] = best[0].isoformat()
            looks[r["id"]] = [s for s in best[1] if s in worn]
        read = [p["sha"] for p in windows.get(r["id"], []) if p["group"] in GROUPS and p["sha"] in ans]
        shop[r["id"]] = (read, [s for s in read if s in worn])
    peers = peers or {}
    theirs_worn = {rid: {o: [p["sha"] for p in ps if p["group"] in GROUPS and p["sha"] in worn] for o, ps in pp.items()}
                   for rid, pp in peers.items()}
    wanted = ({s for v in looks.values() for s in v} | {s for _, w in shop.values() for s in w}
              | {s for pp in theirs_worn.values() for ws in pp.values() for s in ws})
    table = C.Wardrobe({s: ans[s] for s in wanted}, cl["spec"], judge["use"], judge.get("options"))

    for r in rows:
        lk, (read, wn) = looks.get(r["id"]), shop[r["id"]]
        r["clothes"] = {"status": status,
                        "runway": None if lk is None else {"show": matched[r["id"]], "looks": len(lk),
                                                           "profile": table.profile(lk, top=3) if len(lk) >= MIN_LOOKS else None,
                                                           "distinctness": {}, "movement": {}},
                        "shop_window": {"read": len(read), "worn": len(wn),
                                        "reach": round(len(wn) / len(read), 3) if read else None,
                                        "profile": table.profile(wn, top=3) if len(wn) >= MIN_WORN else None,
                                        "distinctness": {}, "movement": {}, "own_runway": {}, "recognisable": {}}}
        if len(wn) >= MIN_WORN:
            others = {o: ws for o, ws in theirs_worn.get(r["id"], {}).items() if len(ws) >= MIN_WORN}
            if len(others) >= MIN_PEERS:
                sims = [table.compare(wn, ws, key=r["id"] + "|window|" + o) for o, ws in sorted(others.items())]
                like = _mean_or_none([x["likeness"] for x in sims if x])
                if like is not None:
                    r["clothes"]["shop_window"]["distinctness"] = {
                        "value": round(1 - like, 4), "peers": len(others),
                        "told_apart": sum(x["as_far_by_chance"] < TOLD_APART for x in sims if x)}

    ok = {r["id"]: r for r in rows if len(looks.get(r["id"]) or []) >= MIN_LOOKS}
    seasons = defaultdict(list)
    for r in rows:
        seasons[r["season"]].append(r)
    for s, rs in seasons.items():
        runways = [r for r in rs if r["id"] in ok]
        for r in runways:
            others = [o for o in runways if o["house"] != r["house"]]
            if len(others) >= MIN_RUNWAYS:
                sims = [table.compare(looks[r["id"]], looks[o["id"]], key="|".join(sorted((r["id"], o["id"])))) for o in others]
                like = _mean_or_none([x["likeness"] for x in sims if x])
                if like is not None:
                    r["clothes"]["runway"]["distinctness"] = {
                        "value": round(1 - like, 4), "runways": len(others),
                        "told_apart": sum(x["as_far_by_chance"] < TOLD_APART for x in sims if x)}
            sw = r["clothes"]["shop_window"]
            if sw["worn"] >= MIN_WORN:
                own = table.compare(looks[r["id"]], shop[r["id"]][1], key=r["id"] + "|shop")
                if own and own["likeness"] is not None:
                    sw["own_runway"] = {"value": own["likeness"], "in_common": round(1 - own["distance"], 4),
                                          "by_chance": round(1 - own["by_chance"], 4), "questions": own["questions"],
                                          "told_apart": own["as_far_by_chance"] < TOLD_APART}
                    if len(others) >= MIN_RUNWAYS:
                        theirs = [table.compare(looks[o["id"]], shop[r["id"]][1], key=o["id"] + "|shop|" + r["id"]) for o in others]
                        theirs = [x["likeness"] for x in theirs if x and x["likeness"] is not None]
                        if len(theirs) >= MIN_RUNWAYS:
                            closer = sum(own["likeness"] > t for t in theirs) + 0.5 * sum(own["likeness"] == t for t in theirs)
                            sw["recognisable"] = {"value": round(closer / len(theirs), 3), "less_like": int(sum(own["likeness"] > t for t in theirs)),
                                                  "of": len(theirs)}
    # movement: against the brand's own last runway with its looks read, and its own last window with outfits
    by_house = defaultdict(list)
    for r in rows:
        by_house[r["house"]].append(r)
    for h, rs in by_house.items():
        prev = prev_w = None
        for r in sorted(rs, key=lambda x: x["date"]):
            if r["id"] in ok:
                if prev is not None:
                    m = table.compare(looks[r["id"]], looks[prev["id"]], key=r["id"] + "|" + prev["id"])
                    if m and m["likeness"] is not None:
                        r["clothes"]["runway"]["movement"] = {"value": round(1 - m["likeness"], 4), "previous": prev["date"],
                                                              "told_apart": m["as_far_by_chance"] < TOLD_APART}
                prev = r
            if len(shop[r["id"]][1]) >= MIN_WORN:
                if prev_w is not None:
                    m = table.compare(shop[r["id"]][1], shop[prev_w["id"]][1], key=r["id"] + "|window|" + prev_w["id"])
                    if m and m["likeness"] is not None:
                        r["clothes"]["shop_window"]["movement"] = {"value": round(1 - m["likeness"], 4), "previous": prev_w["date"],
                                                                   "told_apart": m["as_far_by_chance"] < TOLD_APART}
                prev_w = r
    # places within each season
    for rs in seasons.values():
        for part, name in (("runway", "distinctness"), ("runway", "movement"), ("shop_window", "distinctness"),
                           ("shop_window", "movement"), ("shop_window", "own_runway")):
            vals = {r["id"]: r["clothes"][part][name]["value"] for r in rs
                    if r["clothes"].get(part) and r["clothes"][part].get(name)}
            pct = percentiles(vals) if len(vals) >= MIN_PLACED else {}
            for r in rs:
                if r["id"] in pct:
                    r["clothes"][part][name]["percentile"] = pct[r["id"]]
    return {"status": status, "questions": {q: v["status"] for q, v in judge.get("questions", {}).items()},
            "use": [q for q in judge["use"] if q not in C.NOT_CLOTHES], "counts": judge.get("counts"),
            "labellers": judge.get("labellers"), "reader": judge.get("reader"),
            "runways": len(ok), "windows": sum(len(w) >= MIN_WORN for _, w in shop.values()), "pictures_read": len(ans),
            "min": {"looks": MIN_LOOKS, "worn": MIN_WORN, "runways": MIN_RUNWAYS, "placed": MIN_PLACED},
            "told_apart": f"random splits of the same pictures as far apart less than {TOLD_APART:.0%} of the time"}


def build(collections: list[dict], pictures: list[dict], last_month: str | None = None, clothes: dict | None = None) -> dict:
    mains = sorted((c for c in collections if c.get("main")), key=lambda c: (c["house"], c["date"]))
    by_house_month = defaultdict(lambda: defaultdict(list))
    for p in pictures:
        by_house_month[p["house"]][p["month"]].append(p)
    last_month = last_month or max((p["month"] for p in pictures), default=None)
    by_house = defaultdict(list)
    for c in mains:
        by_house[c["house"]].append(c)

    rows, windows, peer_windows = [], {}, {}
    for h, cs in by_house.items():
        prev_pics, prev_mix, prev_date, prev_early = None, None, None, None
        for i, c in enumerate(cs):
            d = date.fromisoformat(c["date"])
            nxt = date.fromisoformat(cs[i + 1]["date"]) if i + 1 < len(cs) else None
            end = shop_window_end(d, nxt)
            months = window_months(d, end)
            early = window_months(d, min(end, d + timedelta(days=ATTENTION_END)))
            mine_early = [p for m in early for p in by_house_month[h].get(m, [])]
            peers_early = {o: ps for o, ps in ((o, [p for m in early for p in pm.get(m, [])])
                                               for o, pm in by_house_month.items() if o != h) if ps}
            complete = bool(months) and last_month is not None and months[-1] <= last_month and (
                nxt is not None or end <= date.fromisoformat(f"{last_month}-28"))
            mine = [p for m in months for p in by_house_month[h].get(m, [])]
            windows[c["id"]] = mine
            peers = {o: [p for m in months for p in pm.get(m, [])] for o, pm in by_house_month.items() if o != h}
            peers = {o: ps for o, ps in peers.items() if ps}
            peer_windows[c["id"]] = peers
            mx = mix(mine)
            row = {"id": c["id"], "house": h, "season": season_of(c), "date": c["date"], "category": c["category"],
                   "show": {k: c["show"].get(k) for k in ("heat", "heat_z", "surprise", "lasting", "lasting_z",
                                                           "momentum", "press", "press_z", "tone", "tone_z")},
                   "scene": c.get("scene"),
                   "shop_window": {"months": months, "complete": complete, "pictures": len(mine),
                                   "by_kind": dict(Counter(p["group"] for p in mine)), "mix": mx,
                                   "mix_shift": tvd(mx, prev_mix) if mx and prev_mix else None,
                                   "distinctness": distinctness(mine, peers),
                                   "movement": movement(mine, prev_pics) if prev_pics else {},
                                   "previous": prev_date,
                                   # the same measures on the months up to day 120, for the association only
                                   "while_attention": {"months": early, "pictures": len(mine_early),
                                                       "mix": mix(mine_early),
                                                       "distinctness": distinctness(mine_early, peers_early),
                                                       "movement": movement(mine_early, prev_early) if prev_early else {}}},
                   "campaigns": {"entries": c.get("campaign", {}).get("entries", 0),
                                 "by_line": c.get("campaign", {}).get("by_line", {})},
                   "advertising": advertising(c.get("advertising") or {}),
                   "clothes": {"status": "waiting on the reading of the clothes"}}
            rows.append(row)
            if mine:
                prev_pics, prev_mix, prev_date = mine, mx, c["date"]
            if mine_early:
                prev_early = mine_early

    # places within each season
    seasons = defaultdict(list)
    for r in rows:
        seasons[r["season"]].append(r)
    for rs in seasons.values():
        for name in ("distinctness", "movement"):
            # each kind is placed among the brands showing that kind, and a brand's place is the average of its
            # kinds' places weighted by its pictures, so campaign pictures are only ever ranked against campaign
            # pictures and product against product; the same for the months up to day 120
            for get in (lambda r: r["shop_window"], lambda r: r["shop_window"]["while_attention"]):
                for g in GROUPS:
                    pct = percentiles({r["id"]: get(r)[name][g]["value"] for r in rs if g in get(r)[name]})
                    for r in rs:
                        if r["id"] in pct:
                            get(r)[name][g]["percentile"] = pct[r["id"]]
                for r in rs:
                    m = get(r)[name]
                    parts = [(m[g]["percentile"], m[g]["n"]) for g in GROUPS if g in m]
                    if parts:
                        m["percentile"] = round(sum(p * n for p, n in parts) / sum(n for _, n in parts))
        for strand in ("heat", "lasting"):
            pct = percentiles({r["id"]: r["show"][strand] for r in rs if r["show"].get(strand) is not None})
            for r in rs:
                r["show"][f"{strand}_percentile"] = pct.get(r["id"])

    worn = (clothes_measures(rows, windows, clothes, peer_windows) if clothes
            else {"status": "waiting on the reading of the clothes"})

    summary = {}
    for s, rs in sorted(seasons.items(), key=lambda kv: season_order(kv[0])):
        summary[s] = {"brands": len(rs),
                      "show": sum(r["show"]["heat"] is not None for r in rs),
                      "lasting": sum(r["show"]["lasting"] is not None for r in rs),
                      "shop_window": sum(bool(r["shop_window"]["pictures"]) for r in rs),
                      "shop_window_complete": sum(r["shop_window"]["complete"] for r in rs),
                      "distinctness": sum("value" in r["shop_window"]["distinctness"] for r in rs),
                      "movement": sum("value" in r["shop_window"]["movement"] for r in rs),
                      "campaigns": sum(bool(r["campaigns"]["entries"]) for r in rs),
                      "advertising": sum(advertised(r) for r in rs),
                      "advertising_on_record": sum(advertising_known(r) for r in rs),
                      "scene": sum(bool((r["scene"] or {}).get("covered")) for r in rs),
                      "runway": sum(bool((r["clothes"].get("runway") or {}).get("profile")) for r in rs),
                      "clothes_window": sum(bool((r["clothes"].get("shop_window") or {}).get("profile")) for r in rs),
                      "own_runway": sum(bool((r["clothes"].get("shop_window") or {}).get("own_runway")) for r in rs)}
    return {"generated_at": store.utc_now(), "status": "descriptive",
            "windows": {"shop_window": f"months after the show, to the day before the next main show, {SHOP_DAYS} days at most",
                        "lasting": "30 to 120 days after the show, net of the median brand"},
            "pictures": len(pictures), "last_month": last_month, "seasons": summary, "clothes": worn,
            "association": association(rows), "collections": rows}


def association(rows: list[dict], boot: int = BOOT, seed: int = 43) -> dict:
    """Which shop-window measures go with attention that lasts: lasting attention on the season places of
    distinctness and movement, the share of campaign pictures and momentum, within brands (each brand's own
    averages removed), standardised, with intervals from resampling collections. The shop window is read on
    its months up to day 120, where the lasting window ends: a picture put up later cannot have moved it.
    Descriptive: the shop window's job is the house's, not the show's, and attention is only the scale the
    two share."""
    names = ["distinctness", "movement", "image_share", "momentum"]
    data = []
    for r in rows:
        sw, sh = r["shop_window"]["while_attention"], r["show"]
        x = [sw["distinctness"].get("percentile"), sw["movement"].get("percentile"),
             (sw["mix"] or {}).get("image"), sh.get("momentum")]
        if sh.get("lasting") is None or any(v is None for v in x):
            continue
        data.append((r["house"], sh["lasting"], x, r["date"]))
    by = defaultdict(list)
    for h, y, x, d in data:
        by[h].append((y, x, d))
    X, Y, dates = [], [], []
    for h, items in by.items():
        if len(items) < 2:
            continue
        ys = np.array([y for y, _, _ in items]); xs = np.array([x for _, x, _ in items], float)
        Y += list(ys - ys.mean()); X += list(xs - xs.mean(axis=0)); dates += [d for _, _, d in items]
    out = {"n": len(Y), "brands": sum(1 for v in by.values() if len(v) >= 2), "terms": names,
           "from": min(dates) if dates else None}
    if len(Y) < 30:
        return {**out, "status": "insufficient: fewer than 30 collections with every term in brands with two"}
    X, Y = np.array(X), np.array(Y)
    sd = X.std(axis=0)
    keep = sd > 0
    Xs = X[:, keep] / sd[keep]
    beta = np.linalg.lstsq(Xs, Y, rcond=None)[0]
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(boot):
        k = rng.integers(0, len(Y), len(Y))
        draws.append(np.linalg.lstsq(Xs[k], Y[k], rcond=None)[0])
    lo, hi = np.quantile(np.array(draws), [0.05, 0.95], axis=0)
    terms = [n for n, k in zip(names, keep) if k]
    return {**out, "status": "ok", "shop_window_to_day": ATTENTION_END,
            "coefficients": {n: {"estimate": round(float(b), 4), "interval_90": [round(float(a), 4), round(float(c), 4)]}
                             for n, b, a, c in zip(terms, beta, lo, hi)},
            "reading": "lasting attention in log views per standard deviation of each term, within brands, "
                       f"with the shop window read on its months up to day {ATTENTION_END}"}


def write_table(rows: list[dict], path: Path = TABLE) -> None:
    fields = ["house", "season", "date", "category", "heat", "heat_z", "lasting", "lasting_z", "momentum", "press",
              "tone", "appointments", "pictures", "complete", "image", "product", "other", "mix_shift",
              "distinctness", "distinctness_percentile", "movement", "movement_percentile", "campaigns",
              "runway_looks", "runway_distinctness", "runway_distinctness_percentile", "runway_movement",
              "runway_movement_percentile", "shop_window_worn", "own_runway", "own_runway_percentile",
              "recognisable", "clothes_status", "search_ads_show", "search_ads_campaign", "search_record_show",
              "search_record_campaign", "paid_social_show", "paid_social_campaign"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            sw, sh = r["shop_window"], r["show"]
            m = sw["mix"] or {}
            cr = r["clothes"].get("runway") or {}
            cs = r["clothes"].get("shop_window") or {}
            sv = (r.get("advertising") or {}).get("search_video") or {}
            paid = (r.get("advertising") or {}).get("paid_social") or {}
            w.writerow({"house": r["house"], "season": r["season"], "date": r["date"], "category": r["category"],
                        "heat": sh.get("heat"), "heat_z": sh.get("heat_z"), "lasting": sh.get("lasting"),
                        "lasting_z": sh.get("lasting_z"), "momentum": sh.get("momentum"), "press": sh.get("press"),
                        "tone": sh.get("tone"), "appointments": (r["scene"] or {}).get("appointments"),
                        "pictures": sw["pictures"], "complete": sw["complete"], "image": m.get("image"),
                        "product": m.get("product"), "other": m.get("other"), "mix_shift": sw["mix_shift"],
                        "distinctness": sw["distinctness"].get("value"),
                        "distinctness_percentile": sw["distinctness"].get("percentile"),
                        "movement": sw["movement"].get("value"), "movement_percentile": sw["movement"].get("percentile"),
                        "campaigns": r["campaigns"]["entries"],
                        "runway_looks": cr.get("looks"),
                        "runway_distinctness": (cr.get("distinctness") or {}).get("value"),
                        "runway_distinctness_percentile": (cr.get("distinctness") or {}).get("percentile"),
                        "runway_movement": (cr.get("movement") or {}).get("value"),
                        "runway_movement_percentile": (cr.get("movement") or {}).get("percentile"),
                        "shop_window_worn": cs.get("worn"),
                        "own_runway": (cs.get("own_runway") or {}).get("value"),
                        "own_runway_percentile": (cs.get("own_runway") or {}).get("percentile"),
                        "recognisable": (cs.get("recognisable") or {}).get("value"),
                        "clothes_status": r["clothes"].get("status"),
                        "search_ads_show": (sv.get("show_period") or {}).get("ads"),
                        "search_ads_campaign": (sv.get("campaign_period") or {}).get("ads"),
                        "search_record_show": (sv.get("show_period") or {}).get("record"),
                        "search_record_campaign": (sv.get("campaign_period") or {}).get("record"),
                        "paid_social_show": paid.get("show_period"), "paid_social_campaign": paid.get("campaign_period")})


def main(argv: list[str] | None = None) -> int:
    collections = store.read_jsonl(THREAD_DIR / "collections.jsonl")
    if not collections:
        print("no collections on file: run `python -m adtone.thread build` first")
        return 0
    out = build(collections, load_pictures(), clothes=load_clothes())
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(out, indent=1, default=float) + "\n", encoding="utf-8")
    write_table(out["collections"])
    latest = [s for s, v in out["seasons"].items() if v["shop_window_complete"] >= 10]
    print(json.dumps({"collections": len(out["collections"]), "pictures": out["pictures"],
                      "latest full season": latest[-1] if latest else None,
                      "clothes": {k: out["clothes"].get(k) for k in ("status", "runways", "pictures_read")},
                      "association": {k: out["association"].get(k) for k in ("status", "n", "brands")}}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
