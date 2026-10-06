from datetime import date

import numpy as np

from adtone import config, credits, crews
from adtone.concepts import Concept


def _cr(house, person, published, campaign="", role="photographer", cd="", scope="fashion", work_type="advertising",
        source="https://example.org/x"):
    return credits.Credit(house=house, client=house, scope=scope, campaign=campaign, work_type=work_type,
                          published=published, creative_director=cd, cd_basis="source" if cd else "", role=role,
                          person=person, source_url=source)


def _concept(cid, house, when, vec=None, shas=()):
    return Concept(house_id=house, concept_id=cid, first_seen=when, ad_ids=[], shas=list(shas),
                   vec=np.zeros(4) if vec is None else vec)


def test_credit_rows_group_into_campaign_entries():
    rows = [_cr("h", "X", "2026-01", "Spring", cd="D"), _cr("h", "S", "2026-01", "Spring", role="stylist", cd="D"),
            _cr("h", "Y", "2026-03-01", "Summer", cd="D")]
    camps = crews.campaigns(rows)
    assert [c.photographers for c in camps] == [("X",), ("Y",)]
    assert camps[0].crew == (("photographer", "X"), ("stylist", "S")) and camps[0].precision == "month"
    assert camps[1].start == date(2026, 3, 1) and camps[1].precision == "day"


def test_links_take_image_matches_first_then_dates_and_leave_ties_unlinked():
    rows = [_cr("h", "X", "2026-01", "A"), _cr("h", "Y", "2026-03-01", "B"), _cr("h", "Z", "2026-03-05", "C"),
            _cr("h", "W", "2026", "year only"), _cr("h", "S", "2026-02", "crew only", role="stylist"),
            _cr("h", "V", "2026-01-20", "beauty", scope="beauty")]
    camps = crews.campaigns(rows)
    a = next(c for c in camps if c.name == "A")
    concepts = [_concept("dated", "h", date(2026, 1, 10)), _concept("tie", "h", date(2026, 3, 3)),
                _concept("late", "h", date(2027, 1, 1)), _concept("matched", "h", date(2027, 6, 1))]
    links, stats = crews.link(concepts, camps, exact={"matched": a.id})
    assert links["dated"].photographers == ("X",) and links["dated"].method == "date" and links["dated"].days_from_start == 9
    assert links["matched"].campaign_id == a.id and links["matched"].method == "image"
    assert "tie" not in links and "late" not in links
    assert stats == {"image": 1, "date": 1, "tie": 1, "unlinked": 1}


def test_graph_reports_the_connected_set_and_the_houses_outside_it():
    rows = [_cr("a", "P", "2026-01"), _cr("b", "P", "2026-02"), _cr("b", "Q", "2026-03"), _cr("c", "R", "2026-01")]
    g = crews.graph_summary(rows)
    assert g["connected"] == ["a", "b"] and g["outside"] == ["c"] and g["movers"] == {"P": ["a", "b"]}


def test_the_seed_credits_already_connect_most_houses():
    g = crews.graph_summary(credits.load())
    assert len(g["connected"]) >= 12 and "David Sims" in g["movers"]


def _world(seed, n_units=8, n_phot=40, n_obs=2400, d=6, sa=0.3, sp=0.2, se=0.5, per_phot=None, p_move=0.5):
    """Units and photographers with planted effects; a ring of movers keeps every unit connected."""
    rng = np.random.default_rng(seed)
    alpha = rng.normal(0, np.sqrt(sa), (n_units, d))
    psi = rng.normal(0, np.sqrt(sp), (n_phot, d))
    works = [[p % n_units] for p in range(n_phot)]
    for p in range(n_units):
        works[p].append((p + 1) % n_units)
    for p in range(n_units, n_phot):
        if rng.random() < p_move:
            other = int(rng.integers(n_units))
            if other not in works[p]:
                works[p].append(other)
    plan = [p for p in range(n_phot) for _ in range(per_phot)] if per_phot else list(rng.integers(n_phot, size=n_obs))
    rows, a, s = [], [], []
    for p in plan:
        u = works[p][int(rng.integers(len(works[p])))]
        rows.append((alpha[u] + psi[p] + rng.normal(0, np.sqrt(se), d), f"u{u}", (f"p{p}",)))
        a.append(alpha[u])
        s.append(psi[p])
    a, s, y = np.array(a), np.array(s), np.array([r[0] for r in rows])
    var = lambda m: float(((m - m.mean(0)) ** 2).sum() / len(m))
    tot = var(y)
    return rows, {"unit": var(a) / tot, "photographer": var(s) / tot}


def test_decomposition_recovers_planted_shares():
    for seed in range(3):
        rows, truth = _world(seed)
        r = crews.decompose(rows)
        assert r["status"] == "ok" and r["dropped_concepts"] == 0
        assert abs(r["corrected"]["unit"] - truth["unit"]) < 0.04
        assert abs(r["corrected"]["photographer"] - truth["photographer"]) < 0.04


def test_few_concepts_per_photographer_inflate_the_naive_share_and_the_correction_removes_it():
    naive, corrected = [], []
    for seed in range(8):
        rows, truth = _world(100 + seed, n_units=10, n_phot=200, d=4, sa=0.3, sp=0.05, se=1.0, per_phot=3, p_move=0.3)
        r = crews.decompose(rows)
        naive.append(r["naive"]["photographer"] - truth["photographer"])
        corrected.append(r["corrected"]["photographer"] - truth["photographer"])
    assert np.mean(naive) > 0.2
    assert abs(np.mean(corrected)) < 0.05


def test_thin_evidence_is_refused():
    rows = [(np.ones(3) * i, "a", ("p",)) for i in range(30)] + [(np.ones(3) * i, "b", ("p",)) for i in range(30)]
    r = crews.decompose(rows)
    assert r["status"] == "insufficient" and r["n_movers"] == 1
    assert crews.decompose([])["status"] == "insufficient"


def test_end_to_end_from_credits_and_concepts():
    rng = np.random.default_rng(5)
    houses = [f"h{i}" for i in range(6)]
    phots = [f"Photographer {j}" for j in range(12)]
    alpha = {h: rng.normal(0, 0.6, 4) for h in houses}
    psi = {p: rng.normal(0, 0.5, 4) for p in phots}
    rows, concepts, vectors = [], [], {}
    k = 0
    for i, h in enumerate(houses):
        for m in range(12):   # one campaign a month, alternating a house regular and a travelling photographer
            p = phots[i] if m % 2 == 0 else phots[6 + (i + m // 2) % 6]
            rows.append(_cr(h, p, f"2026-{m + 1:02d}", f"c{m}", cd=f"Director {i}"))
            for day in (3, 10, 15):
                cid = f"k{k}"
                k += 1
                concepts.append(_concept(cid, h, date(2026, m + 1, day)))
                vectors[cid] = alpha[h] + psi[p] + rng.normal(0, 0.3, 4)
    out = crews.run(concepts, rows, vectors)
    assert out["links"]["date"] == len(concepts) and out["links"]["tie"] == 0
    r = out["by_house"]
    assert r["status"] == "ok" and r["n_units"] == 6 and r["n_movers"] >= 3
    assert r["corrected"]["photographer"] > 0.1 and r["corrected"]["unit"] > 0.1
    assert out["by_era"]["status"] == "ok"


def test_image_matches_tie_concepts_to_backcat_campaigns_by_url():
    v = np.array([1.0, 0.0, 0.0, 0.0])
    concepts = [_concept("k1", "h", date(2026, 2, 1), shas=["s1"])]
    live_media = {"ad1": {"images": [{"sha": "s1", "phash": "0" * 16}]}}
    back_media = {"h-h-winter-26": {"status": "resolved", "house_id": "h", "images": [{"sha": "b1", "phash": "f" * 16}]}}
    back_camps = {"h-h-winter-26": {"published": "2026-01-15"}}
    exact = crews.image_matches(concepts, live_media, {"s1": v}, back_media, back_camps, {"b1": v})
    assert exact == {"k1": "h-h-winter-26"}
    rows = [_cr("h", "Juergen Teller", "2026-01-15", source="https://models.com/work/h-h-winter-26"),
            _cr("h", "Someone Else", "2026-01-20", source="https://example.org/other")]
    out = crews.run(concepts, rows, {"k1": v}, exact)
    assert out["links"]["image"] == 1


def test_main_prints_the_graph_and_waits_for_the_freeze(tmp_data, capsys):
    assert crews.main(["--decompose"]) == 0
    text = capsys.readouterr().out
    assert "Houses joined by photographers" in text and "waits until the design is frozen" in text
    assert not (config.RESULTS_DIR / crews.OUT_NAME).exists()
