import json

import pytest

from adtone import config, confirm, registry, status, store

TEXT = """version: 1
status: DRAFT

houses:
  - id: gucci
    name: Gucci
    group: treated
    owner: Kering
    search_terms: ["Gucci"]
    page_ids: []
    events:
      - {kind: designer_debut, designer: Demna, date: 2025-09-23, verified: true}
  - {id: prada, name: Prada, group: control, owner: Prada Group, search_terms: ["Prada"], page_ids: [], events: []}
  - {id: hermes, name: Hermès, group: control, owner: Hermès, search_terms: ["Hermès"], page_ids: [], events: []}
  - {id: chloe, name: Chloé, group: watch, owner: Richemont, search_terms: ["Chloé"], page_ids: [], events: []}
"""
CANDS = {"selected": {"gucci": {"page_id": "11", "page_name": "Gucci", "rule": "exact"},
                      "prada": {"page_id": "22", "page_name": "Prada", "rule": "exact"},
                      "hermes": {"page_id": "33", "page_name": "Hermes Birkin Shop", "rule": "contains"}}}


@pytest.fixture
def reg_file(tmp_data, tmp_path, monkeypatch):
    p = tmp_path / "houses.yml"
    p.write_text(TEXT)
    monkeypatch.setattr(config, "REGISTRY_FILE", p)
    return p


def test_set_page_ids_edits_block_and_flow_entries_and_keeps_comments():
    out = confirm.set_page_ids("# note\n" + TEXT, "gucci", ["11"])
    out = confirm.set_page_ids(out, "prada", ["22"])
    assert out.startswith("# note\n") and '    page_ids: ["11"]' in out and 'page_ids: ["22"], events' in out


def test_accept_all_takes_exact_picks_only_and_overrides_add_hand_found_ids(reg_file):
    reg = registry.load(reg_file)
    text, done = confirm.apply(TEXT, reg, CANDS, "all", "hermes=44", False)
    assert done == ["gucci=11", "hermes=44", "prada=22"], "the reseller-like suggestion is never taken by 'all'"


def test_freeze_needs_every_panel_house_but_not_watch_houses(reg_file):
    reg = registry.load(reg_file)
    with pytest.raises(SystemExit, match="hermes"):
        confirm.apply(TEXT, reg, CANDS, "all", "", True)
    text, _ = confirm.apply(TEXT, reg, CANDS, "all", "hermes=44", True)
    reg_file.write_text(text)
    frozen = registry.load(reg_file)
    assert frozen.status == "FROZEN" and frozen.by_id("chloe").page_ids == []


def test_a_frozen_registry_is_never_edited(reg_file):
    reg_file.write_text(TEXT.replace("status: DRAFT", "status: FROZEN"))
    with pytest.raises(SystemExit, match="frozen"):
        confirm.apply(reg_file.read_text(), registry.load(reg_file), CANDS, "all", "", False)


def test_status_page_builds_on_empty_data_and_lists_what_needs_you(reg_file):
    page = status.render(status.gather())
    assert "No collection has succeeded yet" in page and "Gucci" in page
    store.write_state(config.CANDIDATES_FILE, CANDS)
    store.write_state(config.STATE_DIR / "collect.json", {"last_success": "2026-10-06T04:00:00Z", "total_ads": 900,
                                                          "token": {"checked": True, "days_left": 9}})
    page = status.render(status.gather())
    assert "2 automatic page picks await confirmation" in page and "hermes" in page and "expires in 9 days" in page
