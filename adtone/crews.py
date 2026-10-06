"""Crews: tie campaign credits to the collected ads, and separate photographers from houses.

    python -m adtone.crews               # the house and photographer graph, from the credits alone
    python -m adtone.crews --decompose   # with collected data: link concepts to crews, then decompose

Each ad concept is tied to a campaign, and so to its crew, by an image match against the back
catalogue when one exists (the same matcher adtone.calibrate uses), otherwise by date: a campaign
launched within a week of the concept's first run, or listed for the month that contains it, and
failing that the house's most recent earlier campaign within a window. When the campaigns that
qualify were shot by different photographers, the concept stays unlinked rather than guessed.

The decomposition splits concept tone (the analysis residuals) into a house part and a photographer
part, the way labour economists split wages into firm and worker effects. Only photographers who
work for more than one house separate the two, so it runs on the largest set of houses those
photographers connect, and it reports the houses left outside. Where each photographer has only a
few concepts, the photographer share comes out too large, because noise in each estimate reads as
spread between photographers. The corrected shares subtract that bias, assuming equal noise
across concepts (Andrews, Gill, Schank and Upward 2008).

Designers and their crews moved together in 2025 (docs/CREDITS.md), so a debut cannot separate a
designer from a photographer. Photographers crossing houses within the window do. Exploratory:
no registered test reads this unless an amendment registers it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from . import config, registry, store
from . import credits as cr

PRE_RELEASE_DAYS = 7  # a campaign launched within a week either side of a concept's first run is a candidate
WINDOW_DAYS = 150     # a concept first seen this long after a launch is no longer tied to it
MIN_MOVERS = 3        # photographers linked at two or more units before shares are reported
OUT_NAME = "crews.json"


@dataclass(frozen=True)
class Campaign:
    id: str
    house: str
    start: date | None
    precision: str                     # day, month, year or none
    creative_director: str
    photographers: tuple[str, ...]
    crew: tuple[tuple[str, str], ...]  # (role, person)
    work_type: str
    scope: str
    name: str
    source_url: str


@dataclass(frozen=True)
class Link:
    campaign_id: str
    photographers: tuple[str, ...]
    creative_director: str
    method: str                        # image or date
    days_from_start: int | None


def _start(published: str) -> tuple[date | None, str]:
    if not published:
        return None, "none"
    p = [int(x) for x in published.split("-")]
    if len(p) == 3:
        return date(*p), "day"
    if len(p) == 2:
        return date(p[0], p[1], 1), "month"
    return date(p[0], 1, 1), "year"


def _span(c: Campaign) -> tuple[date, date]:
    """The days a listing could mean: one day, or every day of its month."""
    if c.precision == "day":
        return c.start, c.start
    nxt = date(c.start.year + (c.start.month == 12), c.start.month % 12 + 1, 1)
    return c.start, nxt - timedelta(days=1)


def campaigns(credits: list[cr.Credit]) -> list[Campaign]:
    """Credit rows grouped into campaign entries: one per house, name, date, director, source and type."""
    groups: dict[tuple, list[cr.Credit]] = defaultdict(list)
    for c in credits:
        groups[(c.house, c.client, c.campaign, c.published, c.creative_director, c.source_url,
                c.work_type, c.scope)].append(c)
    out = []
    for key, rows in groups.items():
        start, precision = _start(key[3])
        cid = key[0] + ":" + hashlib.sha1("|".join(key).encode("utf-8")).hexdigest()[:10]
        out.append(Campaign(
            id=cid, house=key[0], start=start, precision=precision, creative_director=key[4],
            photographers=tuple(dict.fromkeys(c.person for c in rows if c.role == "photographer")),
            crew=tuple(dict.fromkeys((c.role, c.person) for c in rows)),
            work_type=key[6], scope=key[7], name=key[2], source_url=key[5]))
    return sorted(out, key=lambda c: (c.house, c.start or date.min, c.id))


def _near(c: Campaign, t: date, days: int) -> bool:
    lo, hi = _span(c)
    return abs((lo - t).days) <= days if c.precision == "day" else lo <= t <= hi


def link(concepts, camps: list[Campaign], exact: dict[str, str] | None = None,
         pre_release_days: int = PRE_RELEASE_DAYS, window_days: int = WINDOW_DAYS) -> tuple[dict[str, Link], dict]:
    """Concept id -> Link. `exact` maps concept ids to campaign ids found by image match; those win.

    By date: campaigns near the concept's first run (launched within pre_release_days of it, or listed
    for the month containing it) take it; failing those, the most recent earlier campaign within the
    window. When the campaigns that qualify were shot by different photographers, it is a tie."""
    by_id = {c.id: c for c in camps}
    usable: dict[str, list[Campaign]] = defaultdict(list)
    for c in camps:
        if c.scope == "fashion" and c.work_type == "advertising" and c.photographers and c.precision in ("day", "month"):
            usable[c.house].append(c)
    links: dict[str, Link] = {}
    stats = {"image": 0, "date": 0, "tie": 0, "unlinked": 0}
    for k in concepts:
        t = k.first_seen
        hit = by_id.get((exact or {}).get(k.concept_id, ""))
        if hit is not None and hit.photographers:
            links[k.concept_id] = Link(hit.id, hit.photographers, hit.creative_director, "image",
                                       (t - hit.start).days if hit.start else None)
            stats["image"] += 1
            continue
        pool = usable.get(k.house_id, [])
        chosen = [c for c in pool if _near(c, t, pre_release_days)]
        if not chosen:
            prior = [c for c in pool if _span(c)[0] <= t <= _span(c)[1] + timedelta(days=window_days)]
            if not prior:
                stats["unlinked"] += 1
                continue
            latest = max(_span(c)[0] for c in prior)
            chosen = [c for c in prior if _span(c)[0] == latest]
        if len({frozenset(cr.norm(p) for p in c.photographers) for c in chosen}) > 1:
            stats["tie"] += 1
            continue
        best = min(chosen, key=lambda c: (abs((_span(c)[0] - t).days), c.id))
        links[k.concept_id] = Link(best.id, best.photographers, best.creative_director, "date", (t - best.start).days)
        stats["date"] += 1
    return links, stats


def components(edges: list[tuple[str, str]]) -> list[dict]:
    """Connected sets of a bipartite graph of (unit, photographer) edges, largest first."""
    parent: dict[tuple, tuple] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for u, p in edges:
        a, b = find(("u", u)), find(("p", p))
        if a != b:
            parent[a] = b
    comps: dict[tuple, dict] = defaultdict(lambda: {"units": set(), "photographers": set()})
    for node in list(parent):
        comps[find(node)]["units" if node[0] == "u" else "photographers"].add(node[1])
    ordered = sorted(comps.values(), key=lambda c: (-len(c["units"]), -len(c["photographers"]), sorted(c["units"])))
    return [{"units": sorted(c["units"]), "photographers": sorted(c["photographers"])} for c in ordered]


def graph_summary(credits: list[cr.Credit]) -> dict:
    """What the credits alone say about identification: which houses photographers connect."""
    rows = cr.select(credits)   # advertising photographers, fashion scope
    names = {c.key: c.person for c in rows}
    edges = sorted({(c.house, c.key) for c in rows})
    comps = components(edges)
    if not comps:
        return {"components": 0, "connected": [], "outside": sorted({c.house for c in credits}), "movers": {}}
    big = set(comps[0]["units"])
    houses_of: dict[str, set[str]] = defaultdict(set)
    for h, p in edges:
        houses_of[p].add(h)
    movers = {names[p]: sorted(hs) for p, hs in houses_of.items() if len(hs) >= 2 and hs <= big}
    return {"components": len(comps), "connected": sorted(big),
            "outside": sorted({c.house for c in credits} - big),
            "movers": dict(sorted(movers.items(), key=lambda kv: (-len(kv[1]), kv[0])))}


def decompose(rows: list[tuple[np.ndarray, str, tuple[str, ...]]], min_movers: int = MIN_MOVERS) -> dict:
    """Split tone into unit and photographer parts on the largest connected set.

    rows: (tone vector, unit, photographer keys). A concept credited to k photographers gives each
    a weight of 1/k. Shares are of the total variance, summed over the vector's dimensions."""
    if not rows:
        return {"status": "insufficient", "reason": "no linked concepts"}
    comps = components([(u, p) for _, u, ps in rows for p in ps])
    count = defaultdict(int)
    unit_comp = {u: i for i, c in enumerate(comps) for u in c["units"]}
    for _, u, _ in rows:
        count[unit_comp[u]] += 1
    best = comps[max(count, key=lambda i: (count[i], -i))]
    units, phots = best["units"], best["photographers"]
    keep = [r for r in rows if r[1] in set(units)]
    n, nu, nq = len(keep), len(units), len(phots)
    ui, pi = {u: i for i, u in enumerate(units)}, {p: i for i, p in enumerate(phots)}
    x = np.zeros((n, nu + nq))
    y = np.vstack([np.atleast_1d(np.asarray(r[0], dtype=float)) for r in keep])
    units_of: dict[str, set[str]] = defaultdict(set)
    for i, (_, u, ps) in enumerate(keep):
        x[i, ui[u]] = 1.0
        for p in ps:
            x[i, nu + pi[p]] += 1.0 / len(ps)
            units_of[p].add(u)
    movers = sum(1 for us in units_of.values() if len(us) >= 2)
    rank = int(np.linalg.matrix_rank(x))
    base = {"n_concepts": n, "n_units": nu, "n_photographers": nq, "n_movers": movers,
            "dropped_concepts": len(rows) - n, "units_outside": sorted({r[1] for r in rows} - set(units))}
    if movers < min_movers or n - rank < 10:
        return {"status": "insufficient", **base,
                "reason": f"needs {min_movers} photographers linked at two or more units and 10 spare degrees of freedom"}
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    resid = y - x @ coef
    h, p = x[:, :nu], x[:, nu:]
    part_h, part_p = h @ coef[:nu], p @ coef[nu:]

    def var(a):
        return float(((a - a.mean(axis=0)) ** 2).sum() / n)

    total, vh, vp, ve = var(y), var(part_h), var(part_p), var(resid)
    vc = float(2 * ((part_h - part_h.mean(axis=0)) * (part_p - part_p.mean(axis=0))).sum() / n)
    # Each component is a quadratic form in the coefficients; with equal noise its expected bias is
    # sigma^2 times the trace of the form against the coefficients' covariance.
    g = np.linalg.pinv(x.T @ x)
    one = np.ones(n)

    def gram(a, b):
        return (a.T @ b - np.outer(a.T @ one, b.T @ one) / n) / n

    mh, mp, mc = (np.zeros_like(g) for _ in range(3))
    mh[:nu, :nu] = gram(h, h)
    mp[nu:, nu:] = gram(p, p)
    mc[:nu, nu:] = gram(h, p)
    mc[nu:, :nu] = gram(h, p).T
    s2 = float((resid ** 2).sum() / (n - rank))
    bh, bp, bc = (s2 * float(np.trace(m @ g)) for m in (mh, mp, mc))
    corrected = {"unit": vh - bh, "photographer": vp - bp, "covariance": vc - bc}
    corrected["residual"] = total - sum(corrected.values())
    share = lambda d: {k: round(v / total, 4) for k, v in d.items()}
    return {"status": "ok", **base, "total_variance": round(total, 6),
            "naive": share({"unit": vh, "photographer": vp, "covariance": vc, "residual": ve}),
            "corrected": share(corrected)}


def rows_for(concepts, links: dict[str, Link], vectors: dict[str, np.ndarray], unit: str = "house") -> list:
    """Decomposition rows. unit 'era' splits each house by creative director, where the link names one."""
    out = []
    for k in concepts:
        ln = links.get(k.concept_id)
        if ln is None or k.concept_id not in vectors:
            continue
        u = k.house_id if unit == "house" else f"{k.house_id}:{cr.norm(ln.creative_director) or 'unattributed'}"
        out.append((vectors[k.concept_id], u, tuple(dict.fromkeys(cr.norm(p) for p in ln.photographers))))
    return out


def image_matches(concepts, live_media: dict, live_vecs: dict, back_media: dict, back_camps: dict,
                  back_vecs: dict) -> dict[str, str]:
    """Concept id -> backcat campaign id, where a back-catalogue image reappears in the concept's ads."""
    from .calibrate import match
    phash = {im["sha"]: im["phash"] for m in live_media.values() for im in m.get("images", [])}
    meta = [{"house": k.house_id, "date": k.first_seen, "phash": phash.get(s, "0" * 16), "vec": live_vecs[s],
             "concept_id": k.concept_id} for k in concepts for s in k.shas if s in live_vecs]
    back = []
    for cid, m in back_media.items():
        pub = (back_camps.get(cid) or {}).get("published")
        if m.get("status") != "resolved" or not pub:
            continue
        back += [{"house": m["house_id"], "date": date.fromisoformat(pub), "phash": im["phash"],
                  "vec": back_vecs[im["sha"]], "campaign_id": cid} for im in m.get("images", []) if im["sha"] in back_vecs]
    best: dict[str, tuple[float, str]] = {}
    for mm, b, cos in match(meta, back):
        if cos > best.get(mm["concept_id"], (-2.0, ""))[0]:
            best[mm["concept_id"]] = (cos, b["campaign_id"])
    return {k: v for k, (_, v) in best.items()}


def backcat_credits(reg: registry.Registry) -> tuple[list[cr.Credit], dict]:
    from .backcat import paths
    camps = store.ShardedTable(paths()["campaigns"], "campaign_id").rows
    out: list[cr.Credit] = []
    ids = {h.id for h in reg.houses}
    for row in camps.values():
        if row.get("house_id") in ids:
            out += cr.from_backcat(row, row["house_id"], client=row.get("title", ""))
    return out, camps


def run(concepts, credit_rows: list[cr.Credit], vectors: dict[str, np.ndarray],
        exact_backcat: dict[str, str] | None = None) -> dict:
    camps = campaigns(credit_rows)
    by_url = {c.source_url: c.id for c in camps}
    exact = {k: by_url[f"https://models.com/work/{v}"] for k, v in (exact_backcat or {}).items()
             if f"https://models.com/work/{v}" in by_url}
    links, stats = link(concepts, camps, exact)
    return {"links": stats, "by_house": decompose(rows_for(concepts, links, vectors, "house")),
            "by_era": decompose(rows_for(concepts, links, vectors, "era"))}


def format_graph(g: dict) -> str:
    lines = [f"Houses joined by photographers who work for more than one of them: {len(g['connected'])} "
             f"({', '.join(g['connected'])}).",
             f"Outside that set: {', '.join(g['outside']) or 'none'}.",
             f"{len(g['movers'])} photographers link houses:"]
    lines += [f"  {name}: {', '.join(hs)}" for name, hs in g["movers"].items()]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.crews")
    ap.add_argument("--decompose", action="store_true", help="link collected concepts to crews and decompose")
    ap.add_argument("--instrument", default=config.instrument())
    ap.add_argument("--embedder", default=config.EMBED_TAG)
    args = ap.parse_args(argv)
    reg = registry.load()
    seed = cr.load(house_ids={h.id for h in reg.houses})
    extra, back_camps = backcat_credits(reg)
    rows = seed + extra
    print(format_graph(graph_summary(rows)))
    if not args.decompose:
        return 0
    from . import analysis
    reasons = analysis.gate(reg)
    if reasons:
        print("decomposition waits until the design is frozen: " + "; ".join(reasons))
        return 0
    concepts, info = analysis.load_concepts(args.instrument, args.embedder, reg)
    watch = {h.id for h in reg.group("watch")}
    cs = [c for c in analysis.eligible(concepts) if c.house_id not in watch]
    if not cs:
        print("no collected concepts yet")
        return 0
    res = analysis.residuals(cs, {h.id for h in reg.group("control")})
    from .backcat import paths
    from .embed import VectorStore
    P = paths()
    exact = image_matches(cs, store.ShardedTable(config.MEDIA_DIR, "ad_id").rows, VectorStore(args.embedder).vecs,
                          store.ShardedTable(P["media"], "campaign_id").rows, back_camps,
                          VectorStore(args.embedder, root=P["vectors"]).vecs)
    out = {"status": "EXPLORATORY: not a registered test.", "generated_at": store.utc_now(),
           "instrument": args.instrument, "embedder": args.embedder, "inputs": info,
           "credits": {"seed_rows": len(seed), "backcat_rows": len(extra)},
           "params": {"pre_release_days": PRE_RELEASE_DAYS, "window_days": WINDOW_DAYS, "min_movers": MIN_MOVERS},
           **run(cs, rows, res, exact)}
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.RESULTS_DIR / OUT_NAME).write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(json.dumps({k: out[k] for k in ("links", "by_house", "by_era")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
