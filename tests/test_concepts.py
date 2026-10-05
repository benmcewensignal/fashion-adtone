from datetime import date

import numpy as np

from adtone.concepts import Concept, assign_blocks, build


def _vec(seed):
    v = np.random.default_rng(seed).normal(size=8)
    return v / np.linalg.norm(v)


def test_shared_images_and_near_duplicates_join_while_distinct_creatives_stay_apart():
    ads = {a: {"house_id": "h", "start": d, "eu_total_reach": 10}
           for a, d in [("a", "2026-01-01"), ("b", "2026-01-03"), ("c", "2026-01-04"), ("d", "2026-01-05")]}
    media = {
        "a": {"status": "resolved", "images": [{"sha": "s1", "phash": "ffff0000ffff0000"}]},
        "b": {"status": "resolved", "images": [{"sha": "s1", "phash": "ffff0000ffff0000"}]},   # same image
        "c": {"status": "resolved", "images": [{"sha": "s2", "phash": "ffff0000ffff0003"}]},   # 2 bits away
        "d": {"status": "resolved", "images": [{"sha": "s3", "phash": "0000ffff0000ffff"}]},   # different
    }
    vecs = {"s1": _vec(1), "s2": _vec(2), "s3": _vec(3)}
    obs = {"s1": {"creative_type": "brand_image", "category": "ready_to_wear"}}
    cs = build(ads, media, obs, vecs)
    groups = sorted(sorted(c.ad_ids) for c in cs)
    assert groups == [["a", "b", "c"], ["d"]]
    abc = next(c for c in cs if "a" in c.ad_ids)
    assert abc.first_seen == date(2026, 1, 1) and abc.reach == 30 and abc.shas == ["s1", "s2"]
    assert abc.creative_type == "brand_image"
    assert abs(np.linalg.norm(abc.vec) - 1) < 1e-9


def test_unresolved_or_unembedded_ads_are_skipped():
    ads = {"a": {"house_id": "h", "start": "2026-01-01"}, "b": {"house_id": "h", "start": "2026-01-01"}}
    media = {"a": {"status": "no_candidates"}, "b": {"status": "resolved", "images": [{"sha": "x", "phash": "0" * 16}]}}
    assert build(ads, media, {}, {}) == []


def test_blocks_break_on_gaps_longer_than_three_weeks():
    days = [1, 5, 12, 40, 44, 90]
    cs = [Concept("h", f"c{i}", date(2026, 1, 1).fromordinal(date(2026, 1, 1).toordinal() + d), [], [], _vec(i))
          for i, d in enumerate(days)]
    assign_blocks(cs)
    assert [c.block for c in sorted(cs, key=lambda c: c.first_seen)] == [0, 0, 0, 1, 1, 2]
