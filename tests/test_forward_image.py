import json
import shutil
from datetime import date

import numpy as np
import pytest

from adtone import config, forward, forward_image as F
from adtone.score import RubricChanged, check_frozen

SPEC_PATH = forward.FORWARD_DIR / "designer-change-v1.md"
CODE_PATH = config.ROOT / "adtone" / "forward_image.py"
D = "2027-03-01"
EVENT = {"house": "sl", "predecessor_left_announced": "2026-10-09", "announced": "2026-11-16", "first_show": D,
         "previous_house": None, "previous_left_announced": None}


def spec():
    return forward.load_spec(SPEC_PATH)


def world(strength=0.0, toward=None, seed=3, n=10, dim=24, quiet_after=None):
    """Synthetic homepages, three pictures a month for each house from September 2025 to February 2029, each
    house its own direction. After D the target, sl, moves towards another house's direction by `strength`.
    quiet_after: a month after which sl shows nothing."""
    rng = np.random.default_rng(seed)
    names = ["sl"] + [f"h{i}" for i in range(n)]
    base = {h: F._unit(rng.normal(size=dim)) for h in names}
    pics, m = {}, "2025-09"
    while m <= "2029-02":
        for h in names:
            if h == "sl" and quiet_after and m > quiet_after:
                continue
            c = base[h]
            if h == "sl" and toward and m >= F.shift(F.month_of(D), 1):
                c = F._unit(c + strength * base[toward])
            for j in range(3):
                v = F._unit(c + 0.35 * F._unit(rng.normal(size=dim)))
                pics.setdefault(h, []).append({"month": m, "sha": f"{h}-{m}-{j}", "kind": ("campaign", "on_model")[j % 2], "vec": v})
        m = F.shift(m, 1)
    return pics


def credits(after_names):
    rows = [{"house": "sl", "scope": "fashion", "work_type": "advertising", "role": "photographer", "published": "2025-06",
             "person": "Ana Bell and Cy Dorn"}]
    rows += [{"house": "sl", "scope": "fashion", "work_type": "advertising", "role": "photographer", "published": "2027-05",
              "person": p} for p in after_names]
    return rows


def test_the_template_and_its_code_are_frozen():
    s = spec()
    assert s["id"] == "designer-change-v1" and s["kind"] == "image-template"
    check_frozen(CODE_PATH, config.ROOT / s["code_sha256"])
    assert s["code"] == "adtone/forward_image.py"


def test_the_specification_matches_the_code():
    s = spec()
    assert (s["pre_months"], s["post_months"]) == (list(F.PRE), list(F.POST))
    pairs = {"control_margin_months": F.CONTROL_MARGIN, "min_pictures": F.MIN_PICTURES, "min_months": F.MIN_MONTHS,
             "min_controls": F.MIN_CONTROLS, "min_candidates": F.MIN_CANDIDATES, "min_others": F.MIN_OTHERS,
             "splits": F.N_SPLITS, "draws": F.N_DRAWS, "draw_size": F.DRAW, "seed": F.SEED,
             "magnitude_quantile": F.Q_MAGNITUDE, "itself_quantile": F.Q_ITSELF, "alpha": F.ALPHA,
             "origin_months": F.ORIGIN_MONTHS, "origin_left_days": F.ORIGIN_LEFT_DAYS, "crew_before_months": F.CREW_BEFORE,
             "crew_after_months": F.CREW_AFTER, "adjudicate_months": F.ADJUDICATE, "deadline_months": F.DEADLINE,
             "void_months": F.VOID, "instrument": F.INSTRUMENT, "fingerprint": F.FINGERPRINT}
    for k, v in pairs.items():
        assert s[k] == v, k
    assert set(s["probabilities"]) == set(F.CALLS) and all(0 < p < 1 for p in s["probabilities"].values())


def test_an_edited_template_is_refused(tmp_path):
    shutil.copy(SPEC_PATH, tmp_path / SPEC_PATH.name)
    shutil.copy(SPEC_PATH.with_suffix(".sha256"), tmp_path / "designer-change-v1.sha256")
    with (tmp_path / SPEC_PATH.name).open("a") as f:
        f.write("\nA later thought.\n")
    with pytest.raises(RubricChanged):
        forward.load_spec(tmp_path / SPEC_PATH.name)


def test_waits_for_a_successor_and_voids_if_no_show_comes():
    never = lambda: pytest.fail("no data should be read before a first show")
    e = {"house": "sl", "predecessor_left_announced": "2026-10-09", "first_show": None}
    assert F.evaluate(spec(), e, date(2028, 3, 31), never)["status"] == "awaiting_event"
    assert F.evaluate(spec(), e, date(2028, 4, 1), never)["status"] == "void"
    named = {**e, "announced": "2027-01-10"}           # 18 months from the later of the two dates
    assert F.evaluate(spec(), named, date(2028, 4, 1), never)["status"] == "awaiting_event"
    assert F.evaluate(spec(), named, date(2028, 7, 1), never)["status"] == "void"


def test_a_show_before_the_freeze_is_not_covered():
    out = F.evaluate(spec(), {**EVENT, "first_show": "2026-10-03"}, date(2030, 1, 1), lambda: pytest.fail("read"))
    assert out["status"] == "not_covered"


def test_nothing_is_computed_before_adjudication():
    peek = lambda: pytest.fail("the data were read before the adjudication date")
    out = F.evaluate(spec(), EVENT, date(2028, 8, 31), peek)
    assert out == {"status": "awaiting_adjudication", "first_show": D, "adjudicate_on": "2028-09-01"}


def test_a_house_that_turns_towards_another_hits_magnitude_and_itself():
    pics = world(strength=3.0, toward="h5")
    out = F.compute(EVENT, pics, credits(["Eve Fox"]), {}, n_splits=199, n_draws=100)
    assert out["magnitude"]["hit"] is True and out["magnitude"]["share_at_or_above"] == 0.0
    assert out["itself"]["hit"] is True and out["itself"]["rate"] < 0.2
    assert out["crew"] == {"before": 2, "after": 1, "stayed": 0, "hit": True}
    assert out["transfer"]["status"] == "not_applicable"
    assert "sl" not in out["controls"] and len(out["controls"]) == 10


def test_no_change_misses():
    out = F.compute(EVENT, world(seed=2), credits(["Cy Dorn"]), {}, n_splits=199, n_draws=100)
    assert out["magnitude"]["hit"] is False
    assert out["itself"]["hit"] is False and out["itself"]["rate"] > 0.9
    assert out["crew"]["hit"] is False and out["crew"]["stayed"] == 1     # one of the pair stayed


def test_with_no_change_a_hit_is_rare():
    # beating the 90th percentile of ten controls happens about one time in seven by chance alone
    mag = its = 0
    for seed in range(1, 21):
        out = F.compute(EVENT, world(seed=seed), [], {}, n_splits=199, n_draws=50)
        mag += out["magnitude"]["hit"]
        its += out["itself"]["hit"]
    assert mag <= 6 and its <= 2


def test_transfer_from_the_previous_house():
    e = {**EVENT, "previous_house": "h5", "previous_left_announced": "2026-11-02"}
    out = F.compute(e, world(strength=1.5, toward="h5"), credits([]), {}, n_splits=199, n_draws=100)
    t = out["transfer"]
    assert t["origin"] == "h5" and t["hit"] is True and t["toward_origin"] > t["strongest_other"] > -1
    assert "h5" not in out["controls"]
    assert out["crew"]["status"] == "insufficient"
    other = F.compute({**e, "previous_house": "h2"}, world(strength=1.5, toward="h5"), [], {}, n_splits=199, n_draws=100)
    assert other["transfer"]["hit"] is False


def test_transfer_needs_a_direct_move():
    late = {**EVENT, "previous_house": "h5", "previous_left_announced": "2025-12-01"}     # left 350 days before
    assert F.compute(late, world(), [], {}, n_splits=199, n_draws=100)["transfer"]["status"] == "not_applicable"
    inside = {**EVENT, "previous_house": "sl", "previous_left_announced": "2026-11-16"}
    assert F.compute(inside, world(), [], {}, n_splits=199, n_draws=100)["transfer"]["status"] == "not_applicable"


def test_a_house_with_a_change_of_its_own_is_not_a_control():
    moves = {"h1": ["2026-05"], "h2": ["2028-10"], "h3": ["2029-01"]}   # inside, inside the margin, outside it
    out = F.compute(EVENT, world(), [], moves, n_splits=199, n_draws=100)
    assert "h1" not in out["controls"] and "h2" not in out["controls"] and "h3" in out["controls"]


def test_a_recorded_crew_before_the_show_is_used():
    e = {**EVENT, "crew_before": ["Gus Hale"]}
    out = F.compute(e, world(), credits(["Ana Bell"]), {}, n_splits=199, n_draws=100)
    assert out["crew"] == {"before": 1, "after": 1, "stayed": 0, "hit": True}


def test_too_little_data_waits_then_is_recorded_as_no_result():
    pics = world(quiet_after="2027-01")
    load = lambda: (pics, [], {})
    waiting = F.evaluate(spec(), EVENT, date(2028, 9, 1), load)
    assert waiting["status"] == "awaiting_data" and "magnitude" in waiting["waiting_on"]
    final = F.evaluate(spec(), EVENT, date(2029, 3, 1), load)
    assert final["status"] == "adjudicated" and final["magnitude"]["status"] == "insufficient"


def test_the_runner_keeps_a_final_result(tmp_data):
    out = config.RESULTS_DIR / "forward-designer-change-v1.json"
    key = "sl:2026-10-09:"
    out.write_text(json.dumps({"events": {key: {"status": "void", "marker": "first"}}}))
    events = {"designer_changes": [{"house": "sl", "predecessor_left_announced": "2026-10-09", "first_show": None}]}
    doc = forward.run_image_template(spec(), events, date(2030, 1, 1), out)
    assert doc["events"][key]["marker"] == "first"
    assert json.loads(out.read_text())["events"][key]["marker"] == "first"
    # a successor named after the void is a new event with a key of its own; the void stays on file
    events["designer_changes"][0].update({"successor": "Ann Lee", "announced": "2029-11-01"})
    doc = forward.run_image_template(spec(), events, date(2030, 1, 1), out)
    assert doc["events"][key]["marker"] == "first"
    assert doc["events"]["sl:2026-10-09:2029-11-01"]["status"] == "awaiting_event"


def test_the_recorded_facts_parse():
    events = forward.load_events(forward.FORWARD_DIR / "events.yml")
    recs = events["designer_changes"]
    assert {r["house"] for r in recs} >= {"saint_laurent", "versace"}
    for r in recs:
        assert r["sources"], r["house"]
        assert r.get("first_show") is None or F._date(r["first_show"]) > date(2026, 10, 14)
