"""From ads to concepts to campaign blocks.

A brand running one concept in forty variants shows forty rows in the repository.
Counting rows would weight a tone by how many variants the media buyer made, so the
unit is the concept: ads joined by a shared image, or by images within a few bits of
perceptual hash (the same creative cropped or resized).

Concepts launched close together come from one shoot and share a look, so they are
not independent. They are grouped into campaign blocks, and every null in the
analysis permutes whole blocks, never single concepts. (The fashion-position lesson:
the right null unit was the show, not the look.)
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from . import config


@dataclass
class Concept:
    house_id: str
    concept_id: str
    first_seen: date
    ad_ids: list[str]
    shas: list[str]
    vec: np.ndarray
    outputs: list[dict] = field(default_factory=list)
    reach: int = 0
    block: int = -1

    @property
    def creative_type(self) -> str | None:
        return _mode([o["creative_type"] for o in self.outputs])

    @property
    def category(self) -> str | None:
        return _mode([o["category"] for o in self.outputs])


def _mode(values: list[str]) -> str | None:
    if not values:
        return None
    c = Counter(values)
    best = max(c.values())
    return sorted(v for v, n in c.items() if n == best)[0]


class _UF:
    def __init__(self, items):
        self.p = {i: i for i in items}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def _popcount64(x: np.ndarray) -> np.ndarray:
    return np.unpackbits(x.view(np.uint8)).reshape(-1, 64).sum(axis=1)


def _start_date(ad: dict) -> date | None:
    ts = ad.get("start") or ad.get("created")
    return date.fromisoformat(ts[:10]) if ts else None


def build(ads: dict[str, dict], media: dict[str, dict], obs: dict[str, dict], vecs: dict[str, np.ndarray],
          phash_max: int = config.PHASH_MAX_DIST) -> list[Concept]:
    """Concepts per house from resolved, embedded ads. Ads without a start date or vector are skipped."""
    by_house: dict[str, list[str]] = defaultdict(list)
    images: dict[str, list[dict]] = {}
    for ad_id, m in media.items():
        ad = ads.get(ad_id)
        if not ad or m.get("status") != "resolved" or _start_date(ad) is None:
            continue
        imgs = [i for i in m.get("images", []) if i["sha"] in vecs]
        if imgs:
            by_house[ad["house_id"]].append(ad_id)
            images[ad_id] = imgs
    concepts: list[Concept] = []
    for hid, ad_ids in sorted(by_house.items()):
        uf = _UF(ad_ids)
        sha_owner: dict[str, str] = {}
        flat: list[tuple[str, str]] = []   # (ad_id, phash) per distinct sha
        for a in ad_ids:
            for im in images[a]:
                if im["sha"] in sha_owner:
                    uf.union(a, sha_owner[im["sha"]])
                else:
                    sha_owner[im["sha"]] = a
                    flat.append((a, im["phash"]))
        if len(flat) > 1:
            h = np.array([int(p, 16) for _, p in flat], dtype=np.uint64)
            for i in range(len(flat) - 1):
                close = np.nonzero(_popcount64(h[i] ^ h[i + 1:]) <= phash_max)[0]
                for j in close:
                    uf.union(flat[i][0], flat[i + 1 + int(j)][0])
        groups: dict[str, list[str]] = defaultdict(list)
        for a in ad_ids:
            groups[uf.find(a)].append(a)
        for root, members in groups.items():
            shas = sorted({im["sha"] for a in members for im in images[a]})
            v = np.mean([vecs[s] for s in shas], axis=0)
            v = v / (np.linalg.norm(v) or 1.0)
            concepts.append(Concept(
                house_id=hid, concept_id=f"{hid}:{min(members)}",
                first_seen=min(_start_date(ads[a]) for a in members),
                ad_ids=sorted(members), shas=shas, vec=v,
                outputs=[obs[s] for s in shas if s in obs],
                reach=sum(int(ads[a].get("eu_total_reach") or 0) for a in members),
            ))
    assign_blocks(concepts)
    return concepts


def assign_blocks(concepts: list[Concept], gap_days: int = config.BLOCK_GAP_DAYS) -> None:
    """Within each house, a new block starts when the gap since the last launch exceeds gap_days."""
    by_house: dict[str, list[Concept]] = defaultdict(list)
    for c in concepts:
        by_house[c.house_id].append(c)
    for cs in by_house.values():
        cs.sort(key=lambda c: (c.first_seen, c.concept_id))
        block, prev = 0, None
        for c in cs:
            if prev is not None and (c.first_seen - prev) > timedelta(days=gap_days):
                block += 1
            c.block = block
            prev = c.first_seen
