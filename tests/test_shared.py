from datetime import date, timedelta

import numpy as np

from adtone import shared as S

SPEC = {"enums": {"subject": ["worn_full", "worn_part", "product_alone", "no_clothing"],
                  "colour_main": ["black", "white_ivory", "red", "not_applicable"]},
        "lists": {"garments": {"options": ["coat", "dress", "trousers", "none"]}},
        "integers": {"street_couture_axis": {"min": 1, "max": 5}}}
JUDGE = {"use": ["subject", "colour_main", "garments", "street_couture_axis"], "options": {"garments": ["coat", "dress"]},
         "status": "provisional"}


def _look(colour, garments, axis, subject="worn_full"):
    return {"subject": subject, "colour_main": colour, "garments": garments, "street_couture_axis": axis}


def test_a_position_is_each_answer_s_share_of_the_pictures_it_applies_to():
    comps = S.components(SPEC, JUDGE)
    assert comps == [("colour_main", "black"), ("colour_main", "white_ivory"), ("colour_main", "red"),
                     ("garments", "coat"), ("garments", "dress"), ("street_couture_axis", None)]
    pics = [_look("black", ["coat", "trousers"], 4), _look("black", ["dress"], 5), _look("red", ["coat"], 3),
            _look("not_applicable", ["none"], 3, subject="no_clothing")]          # no clothing: none of it applies
    v = S.position(pics, comps)
    assert np.allclose(v, [2 / 3, 0, 1 / 3, 2 / 3, 1 / 3, 4.0])
    # a question fewer pictures answer than the minimum is left out of the position
    v2 = S.position(pics[:2], comps)
    assert np.isnan(v2).all()


def test_positions_are_placed_against_their_own_kind_and_the_gap_reads_direction():
    rng = np.random.default_rng(1)
    ps = {f"h{i}": rng.normal(size=12) for i in range(5)}
    ps["h0"][3] = np.nan
    z = S.standardise(ps)
    col = np.array([z[h][0] for h in sorted(z)])
    assert abs(col.mean()) < 1e-9 and abs(col.std() - 1) < 1e-9 and np.isnan(z["h0"][3])
    assert S.standardise({k: ps[k] for k in ("h0", "h1", "h2")}) == {}                 # too few to place
    a = np.array([1.0, -1.0] * 6)
    assert S.gap(a, 0.5 * a) == (0.0, 12) and S.gap(a, -a) == (2.0, 12)
    assert S.gap(a, np.full(12, np.nan)) == (None, 0)


def test_the_collection_s_campaign_is_its_concepts_first_seen_in_the_window():
    show = date(2026, 3, 2)
    d = lambda k: show + timedelta(days=k)
    ads = [{"ad_id": "m:1", "first": d(50), "line": "collection", "pictures": [("p1", "ffff000000000000")]},
           {"ad_id": "s:9", "first": d(60), "line": None, "pictures": [("p1", "ffff000000000000")]},       # same picture
           {"ad_id": "m:2", "first": d(55), "line": "other", "pictures": [("p2", "0000ffff00000000")]},
           {"ad_id": "m:3", "first": d(56), "line": "other", "pictures": [("p3", "0000ffff00000001")]},    # a near copy of p2
           {"ad_id": "m:4", "first": d(70), "line": "accessories", "pictures": [("p4", "00000000ffff0000")]},
           {"ad_id": "m:5", "first": d(70), "line": "other", "pictures": [("p5", "000000000000ffff")]},    # worn, but not full
           {"ad_id": "m:6", "first": d(10), "line": "collection", "pictures": [("p6", "f0f0f0f0f0f0f0f0")]}]  # the show period
    cs = S.concepts(ads)
    assert sorted(c["ads"] for c in cs) == [1, 1, 1, 2, 2]
    answers = {"p1": {"subject": "worn_part"}, "p2": {"subject": "worn_full"}, "p3": {"subject": "worn_part"},
               "p4": {"subject": "worn_full"}, "p5": {"subject": "worn_part"}, "p6": {"subject": "worn_full"}}
    got = S.campaign(cs, show, (45, 203), answers)
    # named by its text; named by nothing but showing a full outfit; accessories never; no outfit shown; too early
    assert got == {"concepts": 2, "ads": 4, "shas": ["p1", "p2", "p3"]}


def test_no_advertising_position_is_computed_for_the_registered_test_s_windows_before_the_freeze(monkeypatch):
    monkeypatch.setattr(S, "MIN_COMMON", 3)                       # the toy rubric has six answers
    assert S.held({"season": "2027 SS rtw"}, frozen=False) and S.held({"season": "2027 AW men"}, frozen=False)
    assert not S.held({"season": "2026 AW rtw"}, frozen=False) and not S.held({"season": "2027 SS rtw"}, frozen=True)
    show = date(2026, 3, 2)
    looks = [{"house": h, "category": "rtw", "date": "2026-03-03", "sha": f"{h}{k}", "outfit": True}
             for h in ("a", "b", "c", "d") for k in range(8)]
    looks += [{"house": h, "category": "rtw", "date": "2026-09-30", "sha": f"{h}x{k}", "outfit": True}
              for h in ("a", "b", "c", "d") for k in range(8)]
    colours = {"a": "black", "b": "red", "c": "white_ivory", "d": "black"}
    readings = {x["sha"]: _look(colours[x["house"]], ["coat"] if x["house"] in "ab" else ["dress"], 2 + "abcd".index(x["house"]))
                for x in looks}
    for x in looks[:3]:
        readings[x["sha"]] = _look("red", ["dress"], 5)
    ads, answers = {}, {}
    for i, h in enumerate("abcd"):
        ads[h] = [{"ad_id": f"m:{h}{k}", "first": show + timedelta(days=50 + k), "line": "collection",
                   "pictures": [(f"{h}ad{k}", None)]} for k in range(8)]
        for k in range(8):
            answers[f"{h}ad{k}"] = _look(colours["dcba"[i]], ["dress"] if h in "ab" else ["coat"], 1 + i)
    rec = {"paid_social_record": {"campaign_period": "complete"}}
    cols = [{"id": f"{h}:2026:AW:rtw", "house": h, "season": "2026 AW rtw", "category": "rtw", "main": True,
             "date": show.isoformat(), "campaign_window": [45, 203], "advertising": rec} for h in "abcd"]
    cols += [{"id": f"{h}:2027:SS:rtw", "house": h, "season": "2027 SS rtw", "category": "rtw", "main": True,
              "date": "2026-09-30", "campaign_window": [45, 147], "advertising": rec} for h in "abcd"]
    out = S.build(cols, looks, readings, ads, answers, SPEC, JUDGE, frozen=False)
    by = {r["id"]: r for r in out["collections"]}
    assert out["runways_placed"] == 8 and out["advertising_placed"] == 4 and out["advertising_held"] == 4
    assert by["a:2027:SS:rtw"]["advertising"] == {"held": True} and "gap" not in by["a:2027:SS:rtw"]
    assert by["a:2026:AW:rtw"]["advertising"]["ads"] == 8 and by["a:2026:AW:rtw"]["gap"]["answers"] == 6
    assert out["by_season"]["2026 AW"]["gaps"] == 4 and out["by_season"]["2027 SS"]["advertising_placed"] == 0
    # a window the paid social record does not cover whole gets no advertising position
    cols[0]["advertising"] = {"paid_social_record": {"campaign_period": "partly survivors"}}
    out = S.build(cols, looks, readings, ads, answers, SPEC, JUDGE, frozen=False)
    assert {r["id"]: r for r in out["collections"]}["a:2026:AW:rtw"]["advertising"] == {"record": "partly survivors"}
