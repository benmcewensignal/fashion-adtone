import json
from datetime import date

from adtone import collect, config, store
from adtone.adlib import AdLibraryClient
from adtone.registry import Event, House, Registry
from adtone.synth import FakeResponse, FakeSession, api_ad


def reg_with_pages():
    return Registry(version=1, status="TEST", houses=[
        House("alpha", "Alpha", "treated", "G", ["Alpha"], ["111"], [Event("designer_debut", "X", date(2026, 3, 1), False)]),
        House("beta", "Beta", "control", "G", ["Beta"], ["222"], []),
    ])


def test_normalise_drops_snapshot_url_token_and_copy_text():
    raw = api_ad("9", "111", "2026-02-01T10:00:00+0000", body="Découvrez la nouvelle collection €1200!")
    row = collect.normalise(raw, "alpha", "run1")
    text = json.dumps(row)
    assert "access_token" not in text and "SECRETTOKEN" not in text and "render_ad" not in text
    assert "Découvrez" not in text
    assert row["copy"]["has_price"] is True and row["copy"]["n_excl"] == 1 and row["copy"]["sha"]
    assert row["reach"]["by_gender"] == {"female": 500, "male": 180, "unknown": 5}
    assert row["reach"]["by_age"]["25-34"] == 405
    assert row["target_locations"] == [{"name": "France", "type": "countries", "excluded": False}]


def make_client(ads_by_page):
    def handler(url, params):
        ids = json.loads(params["search_page_ids"])
        return FakeResponse(200, {"data": [a for p in ids for a in ads_by_page.get(p, [])]})
    return AdLibraryClient("TOKEN1234567890", session=FakeSession(handler), sleep=lambda s: None, pause=0)


def test_backfill_then_incremental_updates_without_duplicates(tmp_data):
    reg = reg_with_pages()
    ads = {"111": [api_ad("1", "111", "2026-01-05T00:00:00+0000"), api_ad("2", "111", "2026-02-05T00:00:00+0000")],
           "222": [api_ad("3", "222", "2026-02-07T00:00:00+0000")]}
    c = make_client(ads)
    s1 = collect.run(c, reg, "backfill", "run1", today=date(2026, 10, 5))
    assert s1["new"] == 3 and s1["per_house"] == {"alpha": 2, "beta": 1}
    assert s1["since"] == "2025-08-31"
    assert sorted(p.name for p in config.ADS_DIR.glob("*.jsonl")) == ["2026-01.jsonl", "2026-02.jsonl"]

    ads["111"][0]["ad_delivery_stop_time"] = "2026-03-01T00:00:00+0000"
    s2 = collect.run(make_client(ads), reg, "incremental", "run2", today=date(2026, 10, 12))
    assert s2["mode"] == "incremental" and s2["new"] == 0 and s2["updated"] == 3
    table = store.ShardedTable(config.ADS_DIR, "ad_id")
    assert len(table.rows) == 3
    assert table.get("1")["stop"].startswith("2026-03-01")
    assert table.get("1")["first_collected"] == "run1" and table.get("1")["last_collected"] == "run2"
    state = store.read_state(config.STATE_DIR / "collect.json")
    assert state["last_mode"] == "incremental" and state["total_ads"] == 3
    assert len(store.read_jsonl(config.PROV_DIR / "collect.jsonl")) == 2


def test_incremental_window_overlaps_the_last_success(tmp_data):
    store.write_state(config.STATE_DIR / "collect.json", {"last_success": "2026-09-28T04:00:00Z"})
    s = collect.run(make_client({}), reg_with_pages(), "incremental", "r", today=date(2026, 10, 5))
    assert s["since"] == "2026-08-24"
    assert s["houses_without_ads"] == ["alpha", "beta"]


def test_ads_from_unknown_pages_are_counted_not_stored(tmp_data):
    c = make_client({"111": [api_ad("1", "999", "2026-01-05T00:00:00+0000")]})
    s = collect.run(c, reg_with_pages(), "backfill", "r", today=date(2026, 10, 5))
    assert s["unknown_page_rows"] == 1 and s["new"] == 0


def test_resolve_ranks_candidate_pages_by_ad_count():
    def handler(url, params):
        term = params["search_terms"]
        rows = [{"page_id": "111", "page_name": "Alpha"}] * 5 + [{"page_id": "777", "page_name": "Alpha Reseller"}]
        return FakeResponse(200, {"data": rows if term == "Alpha" else []})
    c = AdLibraryClient("TOKEN1234567890", session=FakeSession(handler), sleep=lambda s: None, pause=0)
    out = collect.resolve(c, reg_with_pages())
    assert out["houses"]["alpha"][0] == {"page_id": "111", "page_name": "Alpha", "n_ads": 5}
    assert out["houses"]["beta"] == []


def test_a_failure_mid_backfill_keeps_what_arrived_and_does_not_advance_the_window(tmp_data):
    import pytest
    from adtone.adlib import GraphError

    def handler(url, params):
        if params is None:
            return FakeResponse(500, {"error": {"code": 2, "message": "Service temporarily unavailable"}})
        return FakeResponse(200, {"data": [api_ad("1", "111", "2026-01-05T00:00:00+0000")],
                                  "paging": {"next": "https://graph.facebook.com/next"}})
    c = AdLibraryClient("TOKEN1234567890", session=FakeSession(handler), sleep=lambda s: None, pause=0, max_retries=1)
    with pytest.raises(GraphError):
        collect.run(c, reg_with_pages(), "backfill", "r1", today=date(2026, 10, 5))
    assert "1" in store.ShardedTable(config.ADS_DIR, "ad_id")
    assert "last_success" not in store.read_state(config.STATE_DIR / "collect.json")
    assert store.read_jsonl(config.PROV_DIR / "collect.jsonl")[-1]["partial"] is True
