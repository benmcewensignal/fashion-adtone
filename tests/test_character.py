"""Character readings: a planted shift is found, no shift stays quiet, growth is joined by half-year."""
from datetime import date

import numpy as np

from adtone import character
from adtone.revenue import Figure
from adtone.score import load_rubric


def _enc():
    return character.Encoder(load_rubric().spec)


def _ans(people="one", setting="studio_plain", mood=("serene",), scale=3, ctype="brand_image"):
    return {"creative_type": ctype, "light": "high_key", "colour_temperature": "neutral", "saturation": "muted",
            "setting": setting, "people": people, "gaze": "to_camera", "expression": "neutral", "pose": "posed_static",
            "framing": "medium", "primary_subject": "garment", "styling_register": "formal_tailored",
            "production": "polished_commercial", "text_in_image": "none", "mood": list(mood), "street_couture_axis": scale,
            "category": "ready_to_wear"}


def _images(house, month, n, rng, **kw):
    out = []
    for i in range(n):
        v = rng.normal(size=8)
        out.append({"house": house, "month": month, "sha": f"{house}-{month}-{i}", "out": _ans(**kw), "vec": v})
    return out


def test_a_planted_shift_is_found_and_a_steady_house_is_not():
    rng = np.random.default_rng(1)
    ims = []
    for m in ("2024-02", "2024-04"):
        ims += _images("moved", m, 10, rng, people="one", setting="studio_plain", mood=("austere",), scale=5)
        ims += _images("steady", m, 10, rng, people="none", setting="landscape_nature", mood=("serene",))
        ims += _images("third", m, 10, rng, people="group", setting="interior")
    for m in ("2024-08", "2024-10"):
        ims += _images("moved", m, 10, rng, people="none", setting="landscape_nature", mood=("playful",), scale=1)
        ims += _images("steady", m, 10, rng, people="none", setting="landscape_nature", mood=("serene",))
        ims += _images("third", m, 10, rng, people="group", setting="interior")
    # mixed answers inside one house's periods: the null should see these as exchangeable
    for m, k in (("2024-03", 0), ("2024-09", 1)):
        for i in range(20):
            ims.append({"house": "mixed", "month": m, "sha": f"mx-{m}-{i}", "vec": rng.normal(size=8),
                        "out": _ans(people=("one" if (i + k) % 2 else "none"))})
    res = character.read(ims, _enc(), n_perm=199)
    s = res["moved"]["shifts"][0]
    assert (s["from"], s["to"]) == ("2024H1", "2024H2") and s["rubric_p"] <= 0.01 and s["rubric"] > 0.2
    assert any(m["question"] == "setting" and m["answer"] == "landscape_nature" and m["change"] == 1.0 for m in s["moved"])
    assert res["steady"]["shifts"][0]["rubric"] == 0 and res["steady"]["shifts"][0]["rubric_p"] == 1.0
    assert res["mixed"]["shifts"][0]["rubric_p"] > 0.2
    assert res["moved"]["periods"]["2024H1"]["street_couture"] == 5.0
    assert res["moved"]["periods"]["2024H1"]["n"] == 20       # each image once per period
    assert set(res["third"]["distinct"]) == {"2024H1", "2024H2"}


def test_too_few_images_or_a_gap_give_no_shift():
    rng = np.random.default_rng(2)
    ims = _images("a", "2023-02", 3, rng) + _images("a", "2023-08", 10, rng)
    ims += _images("b", "2022-02", 10, rng) + _images("b", "2023-02", 10, rng)   # 2022H1 to 2023H1 skips a half
    res = character.read(ims, _enc(), n_perm=99)
    assert res["a"]["shifts"] == [] and res["b"]["shifts"] == []


def _fig(start, end, ptype, rev, comp, measure="house_revenue"):
    return Figure("gucci", measure, "", ptype, date.fromisoformat(start), date.fromisoformat(end), rev, "EUR", None, comp,
                  True, "https://x")


def test_half_year_growth_from_halves_or_weighted_quarters():
    figs = [_fig("2024-01-01", "2024-03-31", "quarter", 100.0, 10.0), _fig("2024-04-01", "2024-06-30", "quarter", 300.0, -2.0),
            _fig("2024-07-01", "2024-12-31", "half", 500.0, 4.0), _fig("2024-07-01", "2024-09-30", "quarter", 200.0, 99.0)]
    g = character.half_growth("gucci", figs)
    assert g == {"2024H1": 1.0, "2024H2": 4.0}       # (10*100 - 2*300) / 400; the reported half wins


def test_shift_and_next_growth_are_correlated_within_house():
    houses = {}
    growth = {}
    rng = np.random.default_rng(3)
    for h in ("a", "b", "c"):
        shifts, g = [], {}
        for k, half in enumerate(["2020H1", "2020H2", "2021H1", "2021H2", "2022H1", "2022H2"]):
            d = float(rng.uniform(0.05, 0.4))
            nxt = character.next_period(half)
            shifts.append({"from": "x", "to": half, "rubric": d})
            g[nxt] = 50 * d + rng.normal(scale=0.5)
        houses[h] = {"shifts": shifts}
        growth[h] = g
    out = character.success(houses, growth, n_perm=199)
    assert out["pairs"] == 18 and out["spearman"] > 0.8 and out["p_two_sided"] < 0.05
