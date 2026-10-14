import numpy as np
import yaml

from adtone import config, measures


def pictures(n_houses=8, years=range(2014, 2022), per_year=8, change=None, seed=1, dim=16):
    """Each house its own direction; `change` = (house, year): from that year the house takes a new direction."""
    rng = np.random.default_rng(seed)
    base = {f"h{i}": rng.normal(size=dim) for i in range(n_houses)}
    new = rng.normal(size=dim)
    out = []
    for y in years:
        for h, c in base.items():
            d = new if change and h == change[0] and y >= change[1] else c
            for j in range(per_year):
                v = d / np.linalg.norm(d) + 0.6 * rng.normal(size=dim) / np.sqrt(dim)
                out.append({"house": h, "month": f"{y}-{1 + j % 12:02d}", "sha": f"{h}{y}{j}", "type": "campaign",
                            "vec": v / np.linalg.norm(v)})
    return out


def test_a_house_that_keeps_its_look_is_recognised_and_one_that_changes_is_not():
    r = measures.recognition(pictures(change=("h3", 2018)), draws=60)
    far = r["five_years_or_more"]
    assert far["houses"]["h0"]["per_100"] > 90 and far["houses"]["h3"]["per_100"] < 20
    assert abs(far["chance_per_100"] - 100 / 8) < 0.1
    near = r["one_or_two_years"]
    assert near["houses"]["h3"]["per_100"] > far["houses"]["h3"]["per_100"]


def test_a_picture_left_up_does_not_recognise_itself():
    both = pictures(years=range(2014, 2016))
    early = [p for p in both if p["month"].startswith("2014")]
    fresh = [p for p in both if p["month"].startswith("2015")]
    left_up = [dict(p, month="2015-06", sha=p["sha"] + "-again") for p in early]     # the same pictures, shown again
    r = measures.recognition(early + left_up, draws=20)
    assert r["pairs"] == []                         # nothing new in 2015, so no pair of years to judge
    r = measures.recognition(early + left_up + fresh, draws=20)
    assert r["pairs"] and r["pairs"][0]["houses"] == 8


def test_every_measure_in_the_catalogue_has_a_status_and_a_definition():
    doc = yaml.safe_load((config.ROOT / "reference" / "measures.yml").read_text())
    ids = [m["id"] for m in doc["measures"]]
    assert len(ids) == len(set(ids))
    statuses = {"draft", "provisional", "tested", "reviewed", "outside", "frozen"}
    for m in doc["measures"]:
        assert m["status"] in statuses and m["definition"] and m["question"] and m["code"], m["id"]
    assert {"distinctness", "movement", "recognition", "convergence", "cast"} <= set(ids)
