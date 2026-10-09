from datetime import date

import numpy as np

from adtone import benchmark as B


def _pic(house, month, group, vec, i=0):
    v = np.asarray(vec, float)
    kind = {"image": "brand_image", "product": "product_on_model", "other": "promotional"}[group]
    return {"sha": f"{house}{month}{group}{i}", "house": house, "month": month, "kind": kind,
            "group": group, "vec": v / np.linalg.norm(v)}


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
    assert last["h0"]["clothes"]["status"].startswith("waiting")
    assert out["association"]["status"].startswith("insufficient")          # 12 collections with every term


def test_the_thread_workflow_owns_the_benchmark_results():
    from adtone import guard
    assert guard.violations("thread", ["data/results/benchmark.json", "data/results/benchmark.csv"]) == []


# ---------- the clothes ----------

SPEC = {"enums": {"subject": ["worn_full", "worn_part", "product_alone", "no_clothing"],
                  "silhouette": ["fitted", "straight", "flared", "volume", "not_visible", "not_applicable"],
                  "colour_main": ["black", "white_ivory", "red", "blue", "beige_camel", "not_applicable"]},
        "lists": {"garments": {"options": ["coat", "dress", "trousers", "skirt", "jacket", "none"], "min": 1, "max": 3}},
        "integers": {"street_couture_axis": {"min": 1, "max": 5}}}
USE = ["colour_main", "garments", "silhouette", "street_couture_axis", "subject"]
STYLES = {   # each house's clothes, as the chance of each answer
    "tailored": {"silhouette": {"straight": .6, "fitted": .3, "volume": .1}, "colour_main": {"black": .6, "beige_camel": .3, "white_ivory": .1},
                 "garments": {"coat": .5, "trousers": .6, "jacket": .5}, "street_couture_axis": {4: .6, 5: .3, 3: .1}},
    "romantic": {"silhouette": {"flared": .6, "volume": .3, "fitted": .1}, "colour_main": {"red": .4, "white_ivory": .4, "blue": .2},
                 "garments": {"dress": .8, "skirt": .3}, "street_couture_axis": {4: .5, 5: .4, 3: .1}},
    "street": {"silhouette": {"volume": .7, "straight": .3}, "colour_main": {"blue": .5, "black": .3, "white_ivory": .2},
               "garments": {"jacket": .7, "trousers": .7}, "street_couture_axis": {1: .5, 2: .4, 3: .1}},
    "minimal": {"silhouette": {"straight": .7, "fitted": .3}, "colour_main": {"white_ivory": .6, "beige_camel": .3, "black": .1},
                "garments": {"dress": .4, "coat": .4, "skirt": .3}, "street_couture_axis": {3: .6, 4: .3, 2: .1}},
}


def _dress(style, rng, subject="worn_full"):
    s = STYLES[style]
    pick = lambda d: list(d)[rng.choice(len(d), p=np.array(list(d.values())) / sum(d.values()))]
    g = [x for x, p in s["garments"].items() if rng.random() < p] or [max(s["garments"], key=s["garments"].get)]
    return {"subject": subject, "silhouette": pick(s["silhouette"]), "colour_main": pick(s["colour_main"]),
            "garments": g[:3], "street_couture_axis": int(pick(s["street_couture_axis"]))}


def test_likeness_is_about_one_for_two_draws_of_the_same_clothes_and_falls_as_they_part():
    from adtone.clothes import Wardrobe
    rng = np.random.default_rng(5)
    ans = {f"a{i}": _dress("tailored", rng) for i in range(12)}
    ans.update({f"b{i}": _dress("tailored", rng) for i in range(40)})
    ans.update({f"c{i}": _dress("romantic", rng) for i in range(40)})
    ans.update({f"d{i}": {"subject": "product_alone", "garments": ["coat"], "silhouette": "not_applicable",
                          "colour_main": "black", "street_couture_axis": 4} for i in range(10)})
    w = Wardrobe(ans, SPEC, USE, {"garments": ["coat", "dress", "trousers", "skirt", "jacket"]})
    a, b, c = [[f"{k}{i}" for i in range(n)] for k, n in (("a", 12), ("b", 40), ("c", 40))]
    same, apart = w.compare(a, b, key="same"), w.compare(a, c, key="apart")
    assert same["distance"] > 0.1                       # twelve looks against forty differ by chance alone
    assert abs(same["likeness"] - 1) < 0.15 and apart["likeness"] < 0.5
    assert same["as_far_by_chance"] > 0.05 and apart["as_far_by_chance"] == 0    # told apart only when they differ
    assert w.compare(a, b, key="same") == same          # the same comparison comes out the same
    assert same["questions"] == 4                       # what the picture shows is not a question about the clothes
    packshots = w.profile([f"d{i}" for i in range(10)])
    assert "silhouette" not in packshots and packshots["garments"]["shares"] == {"coat": 1.0}   # worn questions need a person


def _clothes_world(seed=7):
    """Four houses over two seasons, each with its runway and its shop window. h0 shows its own runway's
    clothes in its shop window; h1 shows clothes like h2's; h3 changes its runway in the second season."""
    rng = np.random.default_rng(seed)
    styles = {"h0": "tailored", "h1": "romantic", "h2": "street", "h3": "minimal"}
    seasons = [("2025 AW rtw", "2025-03-03", "2025-05"), ("2026 SS rtw", "2025-09-29", "2025-12")]
    cols, pics, runway, ans = [], [], [], {}
    for s, d, month in seasons:
        for h, st in styles.items():
            if h == "h3" and d == "2025-09-29":
                st = "street"
            cols.append({"id": f"{h}:{s}", "house": h, "season": s, "date": d, "category": "rtw", "main": True,
                         "show": {"heat": 0.3, "lasting": 0.0}, "scene": None, "campaign": {}, "advertising": {}})
            for i in range(20):
                sha = f"r{h}{d}{i}"
                runway.append({"sha": sha, "house": h, "date": d if h != "h1" else "2025-03-06" if d == "2025-03-03" else d,
                               "category": "rtw"})
                ans[sha] = _dress(st, rng)
            shown = {"h1": "street"}.get(h, st)
            for i in range(14):
                p = _pic(h, month, "image" if i % 2 else "product", rng.normal(size=8), i)
                pics.append(p)
                ans[p["sha"]] = _dress(shown, rng) if i % 7 else _dress(shown, rng, "product_alone")
    return cols, pics, runway, ans


def test_the_runway_is_translated_and_followed_into_the_shop_window():
    cols, pics, runway, ans = _clothes_world()
    judge = {"status": "provisional", "use": USE, "options": {"garments": ["coat", "dress", "trousers", "skirt", "jacket"]},
             "questions": {q: {"status": "provisional"} for q in USE}}
    out = B.build(cols, pics, last_month="2026-06", clothes={"judge": judge, "readings": ans, "runway": runway, "spec": SPEC})
    r = {(x["house"], x["season"]): x["clothes"] for x in out["collections"]}
    now = {h: r[(h, "2026 SS")] for h in ("h0", "h1", "h2", "h3")}
    assert all(c["status"] == "provisional" for c in now.values())
    assert r[("h1", "2025 AW")]["runway"]["show"] == "2025-03-06"      # a runway page dated three days off
    sw = {h: c["shop_window"] for h, c in now.items()}
    assert sw["h0"]["worn"] == 12 and sw["h0"]["reach"] == round(12 / 14, 3)
    assert sw["h0"]["transmission"]["value"] > 0.8 > sw["h1"]["transmission"]["value"]
    assert sw["h0"]["transmission"]["percentile"] > sw["h1"]["transmission"]["percentile"]
    assert sw["h1"]["transmission"]["told_apart"] and not sw["h0"]["transmission"]["told_apart"]
    assert sw["h0"]["recognisable"]["value"] == 1.0 and sw["h1"]["recognisable"]["value"] < 0.5   # h1's window looks like h2
    mv = {h: c["runway"]["movement"]["value"] for h, c in now.items()}
    assert max(mv, key=mv.get) == "h3" and now["h3"]["runway"]["movement"]["previous"] == "2025-03-03"
    assert now["h0"]["runway"]["profile"]["garments"]["n"] == 20
    wd = {h: s["distinctness"]["value"] for h, s in sw.items()}
    assert max(wd, key=wd.get) == "h0"                                  # the only window not showing street clothes
    wm = {h: s["movement"]["value"] for h, s in sw.items()}
    assert max(wm, key=wm.get) == "h3" and sw["h3"]["movement"]["told_apart"]     # its window followed its new runway
    assert out["seasons"]["2026 SS"]["runway"] == 4 and out["seasons"]["2026 SS"]["transmission"] == 4
    assert out["clothes"]["status"] == "provisional" and "subject" not in out["clothes"]["use"]
    few = B.build([c for c in cols if c["house"] != "h3"], pics, last_month="2026-06",
                  clothes={"judge": judge, "readings": ans, "runway": runway, "spec": SPEC})
    assert all("percentile" not in x["clothes"]["shop_window"]["transmission"] for x in few["collections"])   # three are too few to place
    nothing = B.build(cols, pics, last_month="2026-06", clothes={"judge": {"status": "waiting on the reading of the test set", "use": []}})
    assert nothing["collections"][0]["clothes"] == {"status": "waiting on the reading of the test set"}
