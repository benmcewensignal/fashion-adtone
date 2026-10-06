from datetime import date, timedelta
from urllib.parse import quote

import numpy as np

from adtone import attention, config, success
from adtone.concepts import Concept
from adtone.registry import Event, House, Registry
from adtone.synth import FakeResponse, FakeSession

QUERY = {"query": {
    "redirects": [{"from": "Yves Saint Laurent (brand)", "to": "Saint Laurent Paris"}],
    "pages": [{"title": "Gucci"}, {"title": "Saint Laurent Paris"},
              {"title": "Jil Sander (brand)", "missing": True}, {"title": "Jil Sander (company)", "missing": True},
              {"title": "Jil Sander", "pageprops": {"disambiguation": ""}}]}}


SEARCH = {"query": {"search": [{"title": "Jil Sander (fashion house)"}, {"title": "Fashion in Hamburg"}]}}
FOUND = {"query": {"pages": [{"title": "Jil Sander (fashion house)"}]}}


def _handler(views: dict[str, dict[str, int]]):
    def h(url, params):
        if url == attention.API and params.get("list") == "search":
            return FakeResponse(200, payload=SEARCH)
        if url == attention.API and params.get("titles") == "Jil Sander (fashion house)":
            return FakeResponse(200, payload=FOUND)
        if url == attention.API:
            return FakeResponse(200, payload=QUERY)
        for title, series in views.items():
            if f"/{quote(title.replace(' ', '_'), safe='')}/daily/" in url:
                return FakeResponse(200, payload={"items": [{"timestamp": d.replace("-", "") + "00", "views": v}
                                                            for d, v in series.items()]})
        return FakeResponse(404, text="")
    return h


def _reg(*ids):
    names = {"gucci": "Gucci", "saint_laurent": "Saint Laurent", "jil_sander": "Jil Sander", "nowhere": "Nowhere House"}
    return Registry(1, "T", [House(i, names.get(i, i), "control", "o", [i], [], []) for i in ids])


def test_a_house_whose_article_cannot_be_found_is_named_by_the_probe(tmp_data):
    def h(url, params):
        if params and params.get("list") == "search":
            return FakeResponse(200, payload={"query": {"search": [{"title": "Something Else"}]}})
        return FakeResponse(200, payload={"query": {"pages": [{"title": "Nowhere House", "missing": True}]}})
    st = attention.collect(FakeSession(h), _reg("nowhere"), "r2", date(2025, 1, 1), date(2025, 1, 3))
    assert st["unresolved"] == ["nowhere"]
    assert any("nowhere: no Wikipedia article found" in e for e in attention.probe("r2"))


def test_titles_resolve_through_redirects_and_skip_missing_and_disambiguation_pages():
    sess = FakeSession(_handler({}))
    out = attention.resolve(sess, {k: attention.ARTICLES[k] for k in ("gucci", "saint_laurent", "jil_sander")})
    assert out == {"gucci": "Gucci", "saint_laurent": "Saint Laurent Paris", "jil_sander": None}
    assert attention.UA  # the Wikimedia APIs ask for a descriptive agent


def test_collect_writes_each_series_and_the_probe_names_the_gaps(tmp_data):
    days = {f"2025-01-{d:02d}": 100 + d for d in range(1, 11)}
    sess = FakeSession(_handler({"Gucci": days, "Saint Laurent Paris": days}))
    st = attention.collect(sess, _reg("gucci", "saint_laurent", "jil_sander"), "r1", date(2025, 1, 1), date(2025, 1, 10))
    assert st["titles"]["saint_laurent"] == "Saint Laurent Paris"
    assert st["titles"]["jil_sander"] == "Jil Sander (fashion house)" and st["found_by"]["jil_sander"] == "search"
    assert st["found_by"]["gucci"] == "listed" and st["unresolved"] == []
    series = attention.load_series("gucci")
    assert len(series) == 10 and series[date(2025, 1, 3)] == 103
    errs = attention.probe("r1")
    assert any("gucci: only 10 days" in e for e in errs)
    assert any("jil_sander: only 0 days" in e for e in errs)   # found by search, but no views in the fake
    assert attention.probe("other-run") == ["no attention run other-run on file"]


def test_a_missing_article_reads_as_no_views_rather_than_a_failure():
    assert attention.daily_views(FakeSession(_handler({})), "Nowhere", date(2025, 1, 1), date(2025, 1, 2)) == {}


def _series(level_before, level_after, anchor, days=200):
    return {anchor + timedelta(days=k): (level_before if k < 0 else level_after) for k in range(-days, days)}


def test_attention_change_needs_both_sides_and_is_read_against_the_controls():
    a = date(2025, 10, 1)
    assert success.log_change(_series(100, 200, a), a) == np.log(2)
    thin = {d: v for d, v in _series(100, 200, a).items() if d >= a - timedelta(days=30)}
    assert success.log_change(thin, a) is None
    reg = Registry(1, "T", [House("t", "t", "treated", "o", ["t"], [], [Event("designer_debut", "D", a, True)])]
                   + [House(c, c, "control", "o", [c], [], []) for c in ("c1", "c2", "c3")])
    series = {"t": _series(100, 200, a), "c1": _series(100, 110, a), "c2": _series(100, 100, a), "c3": _series(100, 120, a)}
    ch = success.attention_changes(series, reg)["t"]
    assert ch["controls_used"] == 3 and abs(ch["relative"] - (np.log(2) - np.log(1.1))) < 1e-3


def test_spearman_shares_tied_ranks():
    assert abs(success.spearman([1, 2, 3, 4, 5], [5, 6, 7, 8, 7]) - 0.8207826816681233) < 1e-12
    assert list(success.ranks([3, 1, 3, 2])) == [3.5, 1.0, 3.5, 2.0]


def test_association_finds_a_planted_relation_reports_the_bar_and_refuses_too_few_houses():
    rng = np.random.default_rng(1)
    houses = [f"h{i}" for i in range(10)]
    shift = {h: float(i) for i, h in enumerate(houses)}
    planted = {h: shift[h] + rng.normal(0, 0.5) for h in houses}
    r = success.association(shift, planted, n_perm=5000)
    assert r["rho"] > 0.9 and r["p"] < 0.01 and 0.55 < r["bar_95"] < 0.7
    null = success.association(shift, {h: float(rng.normal()) for h in houses}, n_perm=5000)
    assert abs(null["rho"]) < null["bar_95"] or null["p"] > 0.01
    assert success.association({"a": 1.0}, {"a": 2.0})["status"] == "insufficient"


def test_the_leaders_table_marks_the_method_break_and_forms_spells():
    rows = success.leaders()
    assert {r["method"] for r in rows} == {"lyst-v1", "lyst-v2"}
    sp = success.spells(success.leader_by_month(rows))
    assert [(h, ms[0], ms[-1]) for h, ms in sp] == [("saint_laurent", "2025-07", "2025-12"), ("chanel", "2026-01", "2026-06")]


def _tone(w: float, seed=2, n=8, d=6):
    """Eight houses; from 2026 every house but h0 moves a share w of the way to h0's look."""
    rng = np.random.default_rng(seed)
    looks = {f"h{i}": rng.normal(size=d) for i in range(n)}
    months = [f"2025-{m:02d}" for m in range(7, 13)] + [f"2026-{m:02d}" for m in range(1, 7)]
    tone = {h: {} for h in looks}
    for m in months:
        for h, look in looks.items():
            v = look + rng.normal(0, 0.3, d)
            if m >= "2026-01" and h != "h0":
                v = (1 - w) * v + w * looks["h0"]
            tone[h][m] = v
    return tone


LEAD = {f"2026-{m:02d}": "h0" for m in range(1, 7)}


def test_drift_towards_the_leader_is_found_when_houses_partly_converge_on_it():
    first = [success.leader_drift(_tone(0.3, seed), LEAD)[0]["rank"] == 1 for seed in range(20)]
    assert np.mean(first) >= 0.7
    r = success.leader_drift(_tone(0.3, 2), LEAD)[0]
    assert r["status"] == "ok" and r["of"] == 8 and r["mean_change"] > 0 and r["best_possible_p"] == 0.125


def test_with_no_convergence_the_leader_ranks_first_about_as_often_as_chance():
    first = [success.leader_drift(_tone(0.0, seed), LEAD)[0]["rank"] == 1 for seed in range(40)]
    assert np.mean(first) <= 0.25


def test_heavy_convergence_is_understated_because_followers_inherit_the_look():
    """Documented limit: when almost every house converges, the stand-in leaders look like the leader too."""
    r = success.leader_drift(_tone(0.7, 2), LEAD)[0]
    assert r["mean_change"] > 0.5 and r["rank"] > 1


def test_main_waits_for_the_freeze(tmp_data, capsys):
    assert success.main([]) == 0
    assert "waits until the design is frozen" in capsys.readouterr().out
    assert not (config.RESULTS_DIR / success.OUT_NAME).exists()
