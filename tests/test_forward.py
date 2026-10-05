import json
import shutil
from datetime import date, timedelta

import pytest

from adtone import analysis, config, forward
from adtone.registry import House, Registry
from adtone.score import RubricChanged
from adtone.synth import make_world

SPEC_PATH = forward.FORWARD_DIR / "saint-laurent-v1.md"
D = date(2027, 3, 2)
CONTROLS = [f"c{i}" for i in range(1, 7)]
N_PERM = 199


def reg():
    hs = [House(c, c, "control", "G", [c], [c], []) for c in CONTROLS]
    hs.append(House("saint_laurent", "Saint Laurent", "control", "Kering", ["Saint Laurent"], ["sl"], []))
    hs.append(House("chloe", "Chloé", "watch", "Richemont", ["Chloé"], ["ch"], []))
    return Registry(1, "FROZEN", hs)


def concepts(shift, mover=None, seed=2):
    spec = {c: {"debut": None, "shift": 0.0} for c in CONTROLS}
    spec["saint_laurent"] = {"debut": D, "shift": shift}
    spec["chloe"] = {"debut": None, "shift": 0.0}
    return make_world(spec, dim=32, noise=0.12, campaign_sd=0.08, start=date(2026, 10, 10), days=440,
                      campaigns=15, mover=mover, seed=seed).concepts


def run(cs, events, today):
    spec = forward.load_spec(SPEC_PATH)
    return forward.evaluate(spec, events, lambda: [c for c in cs if c.first_seen <= today], reg(), today, N_PERM)


ADJ = D + timedelta(days=270)


def test_the_registered_spec_is_frozen_and_parses():
    spec = forward.load_spec(SPEC_PATH)
    assert spec["id"] == "saint-laurent-v1" and spec["house"] == "saint_laurent" and spec["adjudicate_after_days"] == 270


def test_an_edited_spec_is_refused(tmp_path):
    shutil.copy(SPEC_PATH, tmp_path / SPEC_PATH.name)
    shutil.copy(SPEC_PATH.with_suffix(".sha256"), tmp_path / "saint-laurent-v1.sha256")
    with (tmp_path / SPEC_PATH.name).open("a") as f:
        f.write("\nA later thought.\n")
    with pytest.raises(RubricChanged):
        forward.load_spec(tmp_path / SPEC_PATH.name)


def test_waits_for_the_event_and_voids_if_it_never_comes():
    spec = forward.load_spec(SPEC_PATH)
    never = lambda: pytest.fail("no data should be touched before the event")
    assert forward.evaluate(spec, {}, never, reg(), date(2027, 6, 1))["status"] == "awaiting_event"
    assert forward.evaluate(spec, {}, never, reg(), date(2028, 1, 1))["status"] == "void"


def test_nothing_is_computed_before_adjudication():
    spec = forward.load_spec(SPEC_PATH)
    peek = lambda: pytest.fail("the data were read before the adjudication date")
    out = forward.evaluate(spec, {"saint_laurent_successor_debut": D.isoformat()}, peek, reg(), ADJ - timedelta(days=1))
    assert out["status"] == "awaiting_adjudication" and out["adjudicate_on"] == ADJ.isoformat()


def test_planted_change_hits_all_three_predictions():
    events = {"saint_laurent_successor_debut": D.isoformat(), "saint_laurent_successor_announced": "2027-01-15",
              "saint_laurent_successor_previous_house": "chloe"}
    out = run(concepts(1.5, mover=("chloe", "saint_laurent", 0.7)), events, ADJ)
    assert out["status"] == "adjudicated"
    assert out["prediction_1_magnitude"]["hit"] is True
    assert out["prediction_2_timing"]["hit"] is True and 0 <= out["prediction_2_timing"]["days_after_event"] <= 120
    assert out["prediction_3_transfer"]["hit"] is True
    assert "chloe" not in out["field"] and "saint_laurent" not in out["field"]


def test_no_change_misses_and_transfer_is_not_applicable_without_an_origin():
    out = run(concepts(0.0, seed=6), {"saint_laurent_successor_debut": D.isoformat()}, ADJ)
    assert out["status"] == "adjudicated"
    assert out["prediction_1_magnitude"]["hit"] is False
    assert out["prediction_3_transfer"] == {"status": "not_applicable"}


def test_a_control_that_changes_leadership_leaves_the_field():
    events = {"saint_laurent_successor_debut": D.isoformat(), "control_leadership_changes": {"c3": "2027-02-01"}}
    out = run(concepts(1.5), events, ADJ)
    assert out["dropped_controls"] == ["c3"] and "c3" not in out["field"]


def test_a_recorded_result_is_never_recomputed(tmp_data):
    out = config.RESULTS_DIR / "forward-saint-laurent-v1.json"
    out.write_text(json.dumps({"status": "adjudicated", "marker": "first"}))
    assert forward.main(["--today", "2030-01-01"]) == 0
    assert json.loads(out.read_text())["marker"] == "first"


def test_watch_houses_stay_out_of_the_v1_panel():
    from tests.test_analysis import reg as panel_reg, world
    r = panel_reg()
    r.houses.append(House("chloe", "Chloé", "watch", "Richemont", ["Chloé"], ["ch"], []))
    cs = world(1.5, mover=("balenciaga", "gucci", 0.7))
    cs += [c for c in concepts(0.0) if c.house_id == "chloe"]
    out = analysis.analyse(cs, r, N_PERM)
    assert "chloe" not in out["mover"]["rivals"] and "chloe" not in out["mover"]["coefficients"]
    assert all(p["house"] != "chloe" for p in out["event_study"]["control_placebo"])
