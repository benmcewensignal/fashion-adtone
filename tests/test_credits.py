import csv
from datetime import date

import pytest

from adtone import credits, registry
from adtone.registry import Event, House


def _c(house, person, cd="", role="photographer", published="", campaign="", scope="fashion",
       work_type="advertising"):
    return credits.Credit(house=house, client=house, scope=scope, campaign=campaign, work_type=work_type,
                          published=published, creative_director=cd, cd_basis="source" if cd else "", role=role,
                          person=person, source_url="https://example.org/x")


def _write(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=credits.COLUMNS)
        w.writeheader()
        w.writerows(rows)


GOOD = dict(house="gucci", client="Gucci", scope="fashion", campaign="", work_type="advertising",
            published="2026-02", creative_director="Demna", cd_basis="source", role="photographer",
            person="Andrew Miksys", source_url="https://models.com/work/x", notes="")


def test_seed_table_loads_against_the_registry():
    rows = credits.load()
    reg = registry.load()
    assert len(rows) >= 250
    assert {c.house for c in rows} <= {h.id for h in reg.houses}
    assert all(credits.sources(c.source_url) for c in rows)
    assert not [c for c in rows if c.role not in credits.ROLES]
    assert len(credits.stated_links()) >= 1


def test_every_problem_is_reported_at_once(tmp_path):
    bad = [dict(GOOD), dict(GOOD, house="nowhere"), dict(GOOD, role="model"), dict(GOOD, published="Feb 2026"),
           dict(GOOD, source_url="http://insecure.example"), dict(GOOD, cd_basis=""), dict(GOOD)]
    p = tmp_path / "c.csv"
    _write(p, bad)
    with pytest.raises(ValueError) as e:
        credits.load(p, house_ids={"gucci"})
    msg = str(e.value)
    for expected in ("line 3: unknown house", "line 4: role 'model'", "line 5: published",
                     "line 6: every row needs its source page", "line 7: creative_director and cd_basis",
                     "line 8: repeats an earlier row"):
        assert expected in msg


def test_wrong_columns_are_refused(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text("house,person\ngucci,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="columns must be"):
        credits.load(p, house_ids={"gucci"})


def test_spelling_variants_of_one_credit_match():
    assert credits.norm("Louise & Maria Thornfeldt") == credits.norm("Louise and Maria Thornfeldt")
    assert credits.norm("Zoë Ghertner") == credits.norm("Zoe Ghertner")
    assert credits.norm("Benoît Delhomme") == credits.norm("BENOIT DELHOMME")


def test_year_comes_from_the_date_then_the_campaign_name():
    assert _c("gucci", "a", published="2025-09-22").year == 2025
    assert _c("gucci", "a", campaign="'Hiver' 2026").year == 2026
    assert _c("gucci", "a", campaign="first campaign").year is None


def test_carryover_splits_on_the_creative_director_and_reads_unattributed_rows_by_date():
    h = House("x", "X", "treated", "o", ["X"], [], [Event("designer_debut", "New One", date(2025, 10, 1), True)])
    rows = [_c("x", "Kept", cd="Old One"), _c("x", "Kept", cd="New One"), _c("x", "Gone", published="2025-05"),
            _c("x", "Unclear", published="2025-10"), _c("x", "Later", published="2026-01"),
            _c("x", "Fresh", cd="New One"), _c("x", "Stylist", cd="Old One", role="stylist"),
            _c("x", "Lookbook", cd="Old One", work_type="lookbook"), _c("y", "Elsewhere", cd="Old One")]
    co = credits.carryover(rows, h)
    assert co["before"] == ["Gone", "Kept"] and co["after"] == ["Fresh", "Kept"] and co["shared"] == ["Kept"]


def test_yearly_carryover_only_compares_consecutive_years():
    rows = [_c("p", "A", published="2024"), _c("p", "A", published="2025"), _c("p", "B", published="2025"),
            _c("p", "B", published="2027")]
    yc = credits.yearly_carryover(rows, "p")
    assert [(y["from"], y["to"], y["shared"]) for y in yc] == [(2024, 2025, ["A"])]


def test_crossings_need_two_houses_and_leave_beauty_out_by_default():
    rows = [_c("a", "David Sims"), _c("b", "DAVID  SIMS"), _c("a", "Solo"), _c("c", "David Sims", scope="beauty"),
            _c("a", "Meisel"), _c("b", "Meisel", scope="beauty"), _c("b", "Sims")]
    assert credits.crossings(rows) == {"David Sims": ["a", "b"]}
    assert credits.crossings(rows, scope=None)["David Sims"] == ["a", "b", "c"]


def test_imports_find_the_people_who_follow_a_designer():
    rows = [_c("a", "P", cd="X", published="2024"), _c("a", "S", cd="X", role="stylist", published="2024"),
            _c("a", "Q", cd="Somebody Else", published="2024"), _c("b", "P", cd="X", published="2026"),
            _c("b", "P", cd="X", role="director", published="2026"), _c("b", "Q", cd="X", published="2026"),
            _c("c", "P", cd="Y", published="2025")]
    moves = credits.designer_moves(rows)
    assert moves == [{"designer": "X", "from": "a", "to": "b"}]
    assert credits.imports(rows)[0]["people"] == {"P": ["director", "photographer"]}


def test_backcat_rows_convert_without_the_people_in_the_pictures():
    row = {"kind": "campaign", "url": "https://models.com/work/x", "title": "X Winter 26", "published": "2026-09-01",
           "people": {"Designer": ["Louise Trotter"], "Photographer": ["Juergen Teller"], "Model": ["Somebody"],
                      "Fashion Editor/Stylist": ["Katie Shaw"], "Casting Director": ["Anita Bitton"],
                      "Manicurist": ["Elena Greco"]}}
    out = credits.from_backcat(row, "bottega_veneta")
    assert {(c.role, c.person) for c in out} == {("photographer", "Juergen Teller"), ("stylist", "Katie Shaw"),
                                                 ("casting", "Anita Bitton")}
    assert all(c.creative_director == "Louise Trotter" and c.cd_basis == "source" for c in out)
    assert credits.from_backcat({**row, "kind": "magazine cover"}, "bottega_veneta") == []


def test_the_seed_shows_the_moves_described_in_the_findings():
    rows = credits.load()
    moves = {(m["designer"], m["from"], m["to"]): set(m["people"]) for m in credits.imports(rows)}
    assert {"Alec Soth", "Louise & Maria Thornfeldt", "Rahim Fortune"} <= moves[("Matthieu Blazy", "bottega_veneta", "chanel")]
    assert {"David Sims", "Benjamin Bruno"} <= moves[("Jonathan Anderson", "loewe", "dior")]
    assert "Demna" in moves[("Demna", "balenciaga", "gucci")]
    assert len(credits.crossings(rows)["David Sims"]) >= 4
    text = credits.summary(rows, registry.load(), credits.stated_links())
    assert "Not covered:" in text and "kept:" in text
