from adtone import events as E


def row(house, designer, scope="both", announced="", first="", left=""):
    return {"house_id": house, "designer": designer, "scope": scope, "announced_date": announced, "first_show_date": first,
            "left_announced_date": left, "last_show_date": "", "source_urls": "https://example.org/a", "verified": "true"}


def test_every_pending_appointment_is_registered_for_the_forward_calls():
    # adding an appointment to reference/designers.csv without its record in forward/events.yml fails here
    chk = E.checks()
    assert chk["unregistered"] == [] and chk["mismatched"] == []
    assert all(w["record"] for w in chk["watch"]), chk["watch"]


def test_an_appointment_after_the_freeze_needs_a_record():
    designers = [row("x", "Ann Lee", announced="2026-11-01", first="2027-03-02")]
    assert E.checks(designers, [])["unregistered"][0]["who"] == "Ann Lee"
    rec = {"house": "x", "successor": "Ann Lee", "announced": "2026-11-01", "first_show": "2027-03-02"}
    assert E.checks(designers, [rec]) == {"unregistered": [], "mismatched": [], "watch": []}
    wrong = {**rec, "first_show": "2027-03-09"}
    assert E.checks(designers, [wrong])["mismatched"][0]["events_yml"] == "2027-03-09"


def test_an_appointment_still_to_show_is_pending_and_studio_or_menswear_are_not():
    designers = [row("x", "Ann Lee", announced="2026-11-01"), row("y", "design studio", announced="2026-11-01"),
                 row("z", "Bo Ray", scope="menswear", announced="2026-11-01"),
                 row("w", "Cy Day", announced="2024-02-01", first="2024-09-27")]
    assert [u["house"] for u in E.checks(designers, [])["unregistered"]] == ["x"]


def test_a_departure_without_a_named_successor_is_watched():
    designers = [row("x", "Ann Lee", announced="2020-01-01", first="2020-09-01", left="2026-05-01"),
                 row("x", "design studio", announced="2026-05-01")]
    w = E.checks(designers, [])["watch"]
    assert w == [{"house": "x", "left": "Ann Lee", "left_announced": "2026-05-01", "for_now": "design studio", "record": False}]
    designers.append(row("x", "Bo Ray", announced="2026-09-01", first="2027-03-01"))
    assert E.checks(designers, [{"house": "x", "successor": "Bo Ray", "first_show": "2027-03-01"}])["watch"] == []


def test_each_change_is_due_its_readings_and_its_adjudication():
    designers = [row("x", "Ann Lee", announced="2025-03-01", first="2025-09-27")]
    rec = [{"house": "y", "successor": "Bo Ray", "first_show": "2027-03-02"}]
    d = E.due(designers, rec)
    assert [(x["house"], x["due"], x["what"]) for x in d] == [
        ("x", "2025-12", "first campaign reading"), ("x", "2026-09", "reading at 12 months"),
        ("x", "2027-09", "reading at 24 months"), ("y", "2028-09", "designer-change-v1 adjudication")]


def test_the_register_joins_every_source_with_a_source_per_row():
    reg = E.register()
    kinds = {e["kind"] for e in reg}
    assert {"creative lead appointed", "first show", "chief executive appointed", "ambassador named"} <= kinds
    assert all(e["date"][:4].isdigit() and e["house"] for e in reg)
    assert sum(1 for e in reg if e["source"]) / len(reg) > 0.95
