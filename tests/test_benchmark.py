from collections import Counter
from datetime import date

import numpy as np

from adtone import benchmark as B


def _pic(house, month, group, vec, i=0):
    v = np.asarray(vec, float)
    kind = {"image": "brand_image", "product": "product_on_model", "other": "promotional"}[group]
    return {"sha": f"{house}{month}{group}{i}", "house": house, "month": month, "kind": kind,
            "group": group, "vec": v / np.linalg.norm(v)}


def test_transmission_is_one_for_the_runway_s_own_proportions_and_nought_for_nothing_in_common():
    runway = B.profile([{"silhouette": "fitted", "garments": ["coat", "skirt"]},
                        {"silhouette": "oversized", "garments": ["coat"]}], ["silhouette", "garments"])
    assert B.transmission(runway, runway) == 1.0
    other = B.profile([{"silhouette": "column", "garments": ["dress"]}], ["silhouette", "garments"])
    assert B.transmission(runway, other) == 0.0
    half = B.profile([{"silhouette": "fitted", "garments": ["coat"]}, {"silhouette": "column", "garments": ["dress"]}],
                     ["silhouette", "garments"])
    assert 0 < B.transmission(runway, half) < 1
    assert B.transmission(runway, B.profile([], ["silhouette"])) is None      # nothing to compare


def test_one_season_s_shop_window_never_overlaps_the_next():
    a, b = date(2026, 3, 2), date(2026, 9, 28)
    first = B.window_months(a, B.shop_window_end(a, b))
    second = B.window_months(b, B.shop_window_end(b, None))
    assert first[0] == "2026-03" and first[-1] == "2026-08" and not set(first) & set(second)
    assert second[0] == "2026-10" and len(second) == 6
    late = date(2026, 3, 20)                         # a show after the fifteenth: its month belongs to the window before
    assert B.window_months(late, B.shop_window_end(late, None))[0] == "2026-04"


def test_distinctness_is_like_for_like_and_finds_the_brand_that_stands_apart():
    rng = np.random.default_rng(0)
    base = rng.normal(size=16)
    pics = []
    for h in ("a", "b", "c", "d", "e"):
        for i in range(4):
            look = base + rng.normal(0, 0.1, 16) if h != "a" else -base + rng.normal(0, 0.1, 16)
            pics.append(_pic(h, "2026-04", "image", look, i))
            pics.append(_pic(h, "2026-04", "product", base * 0.5 + rng.normal(0, 0.1, 16) + 3, i))
    by = {h: [p for p in pics if p["house"] == h] for h in "abcde"}
    d = {h: B.distinctness(by[h], {o: by[o] for o in by if o != h}) for h in by}
    assert max(d, key=lambda h: d[h]["image"]["value"]) == "a"
    # a's product shots look like everyone's, so its product distinctness is ordinary
    assert d["a"]["product"]["value"] < d["a"]["image"]["value"]
    only_product = [p for p in by["b"] if p["group"] == "product"]
    assert set(B.distinctness(only_product, {o: by[o] for o in by if o != "b"})) == {"product", "value"}


def test_movement_is_larger_for_the_brand_whose_pictures_changed():
    rng = np.random.default_rng(1)
    v1, v2 = rng.normal(size=16), rng.normal(size=16)
    same_before = [_pic("s", "2025-11", "image", v1 + rng.normal(0, 0.1, 16), i) for i in range(4)]
    same_now = [_pic("s", "2026-04", "image", v1 + rng.normal(0, 0.1, 16), i) for i in range(4)]
    moved_now = [_pic("m", "2026-04", "image", v2 + rng.normal(0, 0.1, 16), i) for i in range(4)]
    assert B.movement(moved_now, same_before)["value"] > B.movement(same_now, same_before)["value"]
    assert B.movement(same_now[:2], same_before) == {}                       # too few pictures of the kind


def test_the_benchmark_follows_each_collection_and_places_it_within_its_season():
    rng = np.random.default_rng(2)
    houses = [f"h{k}" for k in range(6)]
    seasons = [("2025 AW rtw", "2025-03-03"), ("2026 SS rtw", "2025-09-29"), ("2026 AW rtw", "2026-03-02")]
    cols, pics = [], []
    looks = {h: rng.normal(size=16) for h in houses}
    for s, d in seasons:
        for h in houses:
            cols.append({"id": f"{h}:{s}", "house": h, "season": s, "date": d, "category": "rtw", "main": True,
                         "show": {"heat": 0.3, "heat_z": 0.0, "lasting": float(rng.normal(0, 0.1)), "lasting_z": 0.0,
                                  "momentum": 0.0, "press": None, "tone": None},
                         "scene": {"appointments": 1, "covered": True}, "campaign": {"entries": 2, "by_line": {}},
                         "advertising": {"show_period": 0, "campaign_period": 0}})
            month = {"2025-03-03": "2025-05", "2025-09-29": "2025-12", "2026-03-02": "2026-05"}[d]
            if h == "h5" and d == "2026-03-02":
                looks[h] = rng.normal(size=16)                   # h5 changes its look in the last season
            for i in range(4):
                pics.append(_pic(h, month, "image", looks[h] + rng.normal(0, 0.05, 16), i))
                pics.append(_pic(h, month, "product", np.ones(16) + rng.normal(0, 0.05, 16), i))
    out = B.build(cols, pics, last_month="2026-10")
    last = {r["house"]: r for r in out["collections"] if r["season"] == "2026 AW"}
    assert max(last, key=lambda h: last[h]["shop_window"]["movement"]["image"]["value"]) == "h5"
    assert last["h5"]["shop_window"]["movement"]["image"]["percentile"] == 100
    assert last["h0"]["shop_window"]["mix"] == {"image": 0.5, "product": 0.5, "other": 0.0}
    assert last["h0"]["shop_window"]["complete"] is True and last["h0"]["shop_window"]["previous"] == "2025-09-29"
    assert out["seasons"]["2026 AW"]["distinctness"] == 6 and out["seasons"]["2025 AW"]["movement"] == 0
    assert last["h0"]["transmission"]["status"].startswith("waiting")
    assert out["association"]["status"].startswith("insufficient")          # 12 collections with every term


def test_the_thread_workflow_owns_the_benchmark_results():
    from adtone import guard
    assert guard.violations("thread", ["data/results/benchmark.json", "data/results/benchmark.csv"]) == []
