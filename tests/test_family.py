from datetime import date

import pytest

from adtone import family, registry
from adtone.concepts import Concept
from adtone.registry import Event, House, Registry
from adtone.synth import make_world

DEBUTS = {"bottega_veneta": date(2026, 2, 20), "chanel": date(2026, 3, 1), "loewe": date(2026, 2, 25),
          "dior": date(2026, 2, 15), "fendi": date(2026, 3, 10), "balenciaga": date(2026, 2, 20), "gucci": date(2026, 3, 1)}
CONTROLS = ["c1", "c2", "c3", "c4", "c5", "c6"]
N_PERM = 199
WORLD = dict(dim=32, noise=0.12, campaign_sd=0.08, start=date(2025, 6, 1), days=480, campaigns=14)


def reg():
    hs = [House(h, h, "treated", "G", [h], [h], [Event("designer_debut", f"D-{h}", d, True)]) for h, d in DEBUTS.items()]
    hs += [House(h, h, "control", "G", [h], [h], []) for h in CONTROLS]
    return Registry(1, "TEST", hs)


def concepts(movers, seed=3, shift=0.6):
    spec = {h: {"debut": d, "shift": shift} for h, d in DEBUTS.items()}
    spec.update({h: {"debut": None, "shift": 0.0} for h in CONTROLS})
    return make_world(spec, movers=movers, seed=seed, **WORLD).concepts


def family_of(cs):
    from adtone.analysis import event_study, residuals
    res = residuals(cs, set(CONTROLS))
    by = {}
    for c in cs:
        by.setdefault(c.house_id, []).append(c)
    away = event_study(by, res, reg(), N_PERM)["treated"]
    return family.transfer_family(by, res, reg(), N_PERM, away)


def test_teams_that_travelled_transfer_and_the_designer_who_moved_alone_does_not():
    out = family_of(concepts([("bottega_veneta", "chanel", 0.7), ("loewe", "dior", 0.7)]))
    by = {t["label"]: t for t in out["tests"]}
    assert by["blazy"]["supported"] and by["anderson"]["supported"]
    assert by["chiuri"]["supported"] is False
    assert by["mulier"]["status"] == "not in panel"
    assert out["reading"]["reading"] == "image-makers" and "Chiuri" in out["reading"]["why"]
    assert "away" in by["blazy"] and by["blazy"]["p_holm"] >= by["blazy"]["p"]


def test_a_look_that_moves_without_its_team_is_read_as_the_designers():
    out = family_of(concepts([("dior", "fendi", 0.7)], seed=4))
    assert out["reading"]["reading"] == "designer"


@pytest.mark.parametrize("ok,expected", [
    ({"blazy": True, "anderson": False, "chiuri": False}, "photographers"),
    ({"demna": True, "blazy": False, "anderson": False, "chiuri": False}, "designer and photographer, inseparable"),
    ({"demna": False, "blazy": False, "anderson": False, "chiuri": False}, "no transfer"),
    ({"blazy": None, "anderson": None}, "untested"),
])
def test_the_reading_is_fixed_before_the_data(ok, expected):
    assert family.attribution([{"label": k, "supported": v} for k, v in ok.items()])["reading"] == expected


def test_the_september_cluster_is_tested_as_one_shock():
    from adtone.analysis import residuals
    window, placebos = (date(2026, 2, 14), date(2026, 3, 11)), (date(2025, 11, 1), date(2026, 1, 10), date(2026, 5, 1))
    for shift, low in ((1.5, True), (0.0, False)):
        cs = concepts([], seed=6, shift=shift)
        res = residuals(cs, set(CONTROLS))
        by = {}
        for c in cs:
            by.setdefault(c.house_id, []).append(c)
        out = family.pooled_shift(by, res, reg(), N_PERM, window=window, placebos=placebos, draws=4000)
        assert out["status"] == "ok" and len(out["treated_z"]) == 7 and out["n_placebo"] >= 6
        assert (out["p"] < 0.05) is low


def _c(h, d, kind, i, text="none", conf=0.8):
    import numpy as np
    return Concept(house_id=h, concept_id=f"{h}{i}", first_seen=d, ad_ids=[str(i)], shas=[str(i)], vec=np.ones(4),
                   outputs=[{"creative_type": kind, "category": "ready_to_wear", "text_in_image": text, "confidence": conf}])


def test_a_change_in_the_ad_mix_is_reported_as_its_own_outcome():
    r = Registry(1, "T", [House("t", "t", "treated", "G", ["t"], ["t"], [Event("designer_debut", "D", date(2026, 1, 1), True)])]
                 + [House(c, c, "control", "G", [c], [c], []) for c in ("a", "b")])
    by = {"t": [_c("t", date(2025, 10, 1 + i), "brand_image", i) for i in range(10)]
               + [_c("t", date(2026, 2, 1 + i), "product_packshot", 20 + i) for i in range(10)]}
    for c in ("a", "b"):
        by[c] = [_c(c, date(2025, 10, 1 + i), "brand_image", i) for i in range(10)] + \
                [_c(c, date(2026, 2, 1 + i), "brand_image", 20 + i) for i in range(10)]
    out = family.mix_change(by, r, placebos=(date(2026, 1, 1),))
    assert out["houses"]["t"]["tvd"] == 1.0 and out["houses"]["t"]["control_percentile"] == 1.0


def test_sensitivities_drop_logos_and_low_confidence_reads():
    cs = [_c("t", date(2026, 1, 1), "brand_image", 1), _c("t", date(2026, 1, 2), "brand_image", 2, text="logo_only"),
          _c("t", date(2026, 1, 3), "brand_image", 3, conf=0.3)]
    assert [c.concept_id for c in family.no_text(cs)] == ["t1", "t3"]
    assert [c.concept_id for c in family.high_confidence(cs)] == ["t1", "t2"]


def test_the_frozen_tests_read_only_the_core_panel():
    r = registry.load()
    core = r.core()
    assert len(core.houses) == 19 and {h.tier for h in core.houses} == {"core"}
    assert {h.id for h in r.tier("extension")} >= {"versace", "alaia", "givenchy"}
    assert r.by_id("versace").debut.designer == "Dario Vitale"   # Mulier has not shown yet (February 2027)


def test_an_unknown_tier_is_refused(tmp_path):
    p = tmp_path / "houses.yml"
    p.write_text("version: 1\nstatus: DRAFT\nhouses:\n  - {id: x, name: X, group: control, tier: maybe, search_terms: [X], page_ids: [], events: []}\n")
    with pytest.raises(ValueError, match="tier"):
        registry.load(p)


LONG = dict(dim=32, noise=0.12, campaign_sd=0.08, start=date(2024, 10, 1), days=720, campaigns=24)


def _world(spec_debuts, registered, controls_shift=None, seed=8):
    spec = {h: {"debut": d, "shift": 1.5} for h, d in spec_debuts.items()}
    spec.update({c: {"debut": (controls_shift or {}).get(c), "shift": 1.5 if (controls_shift or {}).get(c) else 0.0}
                 for c in CONTROLS})
    cs = make_world(spec, seed=seed, **LONG).concepts
    hs = [House(h, h, "treated", "G", [h], [h], [Event("designer_debut", "D", d, True)]) for h, d in registered.items()]
    hs += [House(c, c, "control", "G", [c], [c], []) for c in CONTROLS]
    from adtone.analysis import residuals
    res = residuals(cs, set(CONTROLS))
    by = {}
    for c in cs:
        by.setdefault(c.house_id, []).append(c)
    return by, res, Registry(1, "T", hs)


SEPT = {"t1": date(2025, 9, 23), "t2": date(2025, 9, 28), "t3": date(2025, 10, 1), "t4": date(2025, 10, 4)}


def test_treated_houses_do_not_shift_at_a_fake_debut_before_the_real_one():
    by, res, r = _world(SEPT, SEPT)
    out = family.in_time_placebo(by, res, r, N_PERM)
    assert out["n_tested"] >= 3 and out["rate"] <= 0.25


def test_a_change_registered_half_a_year_late_trips_the_in_time_placebo():
    late = {h: date(d.year + (d.month + 6 > 12), (d.month + 6 - 1) % 12 + 1, d.day) for h, d in SEPT.items()}
    by, res, r = _world(SEPT, late)
    out = family.in_time_placebo(by, res, r, N_PERM)
    assert out["rate"] >= 0.75


def test_the_detector_is_finding_debuts_not_september():
    by, res, r = _world(SEPT, SEPT)
    ok = family.season_check(by, res, r, N_PERM)
    assert ok["treated_hit_rate"] >= 0.75 and ok["control_september_rate"] <= 0.2 and ok["passes"] is True
    sept_controls = {c: date(2025, 9, 25) for c in CONTROLS[:4]}
    by, res, r = _world(SEPT, SEPT, controls_shift=sept_controls)
    bad = family.season_check(by, res, r, N_PERM)
    assert bad["control_september_rate"] >= 0.5 and bad["passes"] is False


@pytest.mark.parametrize("primary,mix,verdict", [
    ({"season_check": {"controls": {"c": {}}, "control_false_positive_rate": 0.1, "passes": True},
      "in_time_placebo": {"rate": 0.0, "n_tested": 4}, "pooled_shift": {"status": "ok", "p": 0.01}}, None, "pass"),
    ({"season_check": {"controls": {"c": {}}, "control_false_positive_rate": 0.4, "passes": True},
      "in_time_placebo": {"rate": 0.0, "n_tested": 4}}, None, "fail"),
    ({"season_check": {"controls": {"c": {}}, "control_false_positive_rate": 0.1, "passes": False}}, None, "fail"),
    ({"season_check": {"controls": {"c": {}}, "control_false_positive_rate": 0.0, "passes": True},
      "pooled_shift": {"status": "ok", "p": 0.4}},
     {"houses": {"a": {"control_percentile": 1.0}, "b": {"control_percentile": 0.97}}}, "fail"),
    ({"season_check": {}, "in_time_placebo": {"n_tested": 0}}, None, "not yet testable"),
])
def test_the_kill_rule_is_written_before_the_data(primary, mix, verdict):
    out = family.kill_rule(primary, mix)
    assert out["verdict"] == verdict
    assert out["saint_laurent"] == {"pass": "scored as frozen", "fail": "not scored", "not yet testable": "pending"}[verdict]


def test_the_whole_amendment_runs_end_to_end_with_owner_clustering():
    spec = {h: {"debut": d, "shift": 0.8} for h, d in DEBUTS.items()}
    spec.update({h: {"debut": None, "shift": 0.0} for h in CONTROLS})
    cs = make_world(spec, movers=[("bottega_veneta", "chanel", 0.6)], seed=9, **WORLD).concepts
    hs = [House(h, h, "treated", "Kering" if h in ("gucci", "balenciaga", "bottega_veneta") else "LVMH", [h], [h],
                [Event("designer_debut", "D", d, True)]) for h, d in DEBUTS.items()]
    hs += [House(c, c, "control", "Kering" if c == "c1" else "Other", [c], [c], []) for c in CONTROLS]
    out = family.run(cs, Registry(1, "T", hs), n_perm=49)
    assert set(out) >= {"primary", "kill_rule", "owner_clustered", "mix", "sensitivity"}
    assert set(out["sensitivity"]) == {"high_confidence", "no_logo_or_text", "core_controls_only",
                                       "without_art_direction_changes"}
    assert out["kill_rule"]["verdict"] in ("pass", "fail", "not yet testable")
