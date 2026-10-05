from datetime import date

import numpy as np
import pytest

from adtone import analysis
from adtone.registry import Event, House, Registry
from adtone.synth import make_world

DEBUTS = {"t1": date(2026, 2, 15), "t2": date(2026, 3, 1), "t3": date(2026, 3, 15), "t4": date(2026, 2, 20),
          "balenciaga": date(2026, 2, 20), "gucci": date(2026, 3, 1)}
CONTROLS = ["c1", "c2", "c3", "c4", "c5", "c6"]
N_PERM = 199
PARAMS = dict(dim=32, noise=0.12, campaign_sd=0.08)


def reg():
    houses = [House(h, h, "treated", "G", [h], [h], [Event("designer_debut", f"D-{h}", d, True)]) for h, d in DEBUTS.items()]
    houses += [House(h, h, "control", "G", [h], [h], []) for h in CONTROLS]
    return Registry(1, "TEST", houses)


def world(shift: float, mover=None, seed=3, gucci_shift=None):
    spec = {h: {"debut": d, "shift": shift} for h, d in DEBUTS.items()}
    if gucci_shift is not None:
        spec["gucci"]["shift"] = gucci_shift
    spec.update({h: {"debut": None, "shift": 0.0} for h in CONTROLS})
    return make_world(spec, mover=mover, seed=seed, **PARAMS).concepts


@pytest.fixture(scope="module")
def planted():
    return analysis.analyse(world(1.5, mover=("balenciaga", "gucci", 0.7)), reg(), N_PERM)


@pytest.fixture(scope="module")
def null():
    return analysis.analyse(world(0.0, seed=5), reg(), N_PERM)


def test_planted_debut_shifts_are_recovered(planted):
    h1 = planted["event_study"]["h1"]
    assert h1["n_treated_sufficient"] == 6 and h1["n_controls_with_placebo"] == 6
    assert h1["supported"] is True and h1["fraction_above"] >= 0.8
    for h, e in planted["event_study"]["treated"].items():
        assert e["z"] > 2.5, h
        assert e["p"] == pytest.approx(e["min_p"]) or e["p"] < 0.05, h


def test_no_shift_means_no_support(null):
    h1 = null["event_study"]["h1"]
    assert h1["supported"] is False
    zs = [p["z"] for p in null["event_study"]["control_placebo"]]
    assert np.median(zs) < 2


def test_controls_stay_quiet_when_half_the_panel_moves(planted):
    zs = [p["z"] for p in planted["event_study"]["control_placebo"]]
    assert np.median(zs) < 2, "a control field contaminated by the treated houses would push these up"


def test_changepoints_find_known_breaks_and_spare_controls(planted, null):
    assert planted["h3"]["supported"] is True
    for h in ("t1", "t2", "t3", "t4"):
        assert 0 <= planted["changepoints"][h]["days_after_debut"] <= 120
    assert null["h3"]["control_false_positives"] <= 1


def test_mover_transfer_detected_when_planted(planted):
    mv = planted["mover"]
    assert mv["supported"] is True and mv["transfer"] > 0 and mv["specific"] is True
    assert mv["coefficients"]["balenciaga"] == max(mv["coefficients"].values())


def test_mover_not_supported_when_destination_moves_elsewhere():
    out = analysis.analyse(world(1.5, mover=None, seed=8), reg(), N_PERM)
    assert out["mover"]["supported"] is False


def test_rubric_deltas_describe_the_planted_change(planted):
    d = {(r["field"], r["value"]): r for r in planted["rubric_deltas"]["t1"]}
    assert d[("light", "low_key")]["delta"] == pytest.approx(1.0)
    assert d[("street_couture_axis", "mean")]["delta"] == pytest.approx(-2.0)


def test_thin_data_is_reported_as_insufficient_not_tested():
    cs = [c for c in world(1.5) if c.house_id not in ("t1",) or c.first_seen < date(2025, 12, 1)]
    out = analysis.analyse(cs, reg(), 49)
    assert out["event_study"]["treated"]["t1"]["status"] == "insufficient"


def test_few_blocks_use_an_exact_test_with_its_floor_reported(planted):
    e = planted["event_study"]["treated"]["t1"]
    assert e["exact"] is True
    assert e["min_p"] == pytest.approx(1 / __import__("math").comb(e["blocks_pre"] + e["blocks_post"], e["blocks_pre"]), abs=1e-4)


def test_specificity_survives_the_full_panel_collinearity():
    """Regression test: on the real 18-house registry, plain least squares let the controls'
    coefficients inflate together (their residuals nearly sum to zero) and outrank Balenciaga."""
    from adtone import registry
    real = registry.load()
    spec = {h.id: {"debut": h.debut.date if h.debut else None, "shift": 1.5 if h.group == "treated" else 0.0}
            for h in real.houses}
    w = make_world(spec, dim=32, noise=0.12, campaign_sd=0.08, start=date(2025, 6, 1), days=480, campaigns=14,
                   mover=("balenciaga", "gucci", 0.7), seed=4)
    mv = analysis.analyse(w.concepts, real, N_PERM)["mover"]
    assert mv["supported"] is True
    assert "gucci:pre" in mv["coefficients"] and mv["coefficients"]["gucci:pre"] < 0
