import json
from datetime import date

from adtone import analysis, collect, config, store
from adtone.adlib import AdLibraryClient
from adtone.media import AutoResolver
from adtone.registry import Event, House, Registry
from adtone.synth import FakeResponse, FakeSession, api_ad


def house(hid, name, pages=(), group="control"):
    ev = [Event("designer_debut", "X", date(2026, 3, 1), True)] if group == "treated" else []
    return House(hid, name, group, "G", [name], list(pages), ev)


def test_select_page_prefers_exact_names_and_skips_beauty_and_resellers():
    ranked = [{"page_id": "2", "page_name": "Gucci Beauty", "n_ads": 80},
              {"page_id": "3", "page_name": "Second Hand Gucci Bags", "n_ads": 60},
              {"page_id": "1", "page_name": "Gucci", "n_ads": 50},
              {"page_id": "4", "page_name": "GUCCI", "n_ads": 30}]
    sel = collect.select_page(house("gucci", "Gucci"), ranked)
    assert sel["page_id"] == "1" and sel["rule"] == "exact"


def test_select_page_falls_back_to_whole_words_and_handles_accents():
    sl = collect.select_page(house("saint_laurent", "Saint Laurent"),
                             [{"page_id": "9", "page_name": "YSL Beauty", "n_ads": 100},
                              {"page_id": "8", "page_name": "Saint Laurent Paris", "n_ads": 40},
                              {"page_id": "7", "page_name": "Saint Laurentino Shop", "n_ads": 90}])
    assert sl["page_id"] == "8" and sl["rule"] == "contains"
    assert collect.select_page(house("hermes", "Hermès"), [{"page_id": "5", "page_name": "Hermès", "n_ads": 3}])["page_id"] == "5"
    assert collect.select_page(house("prada", "Prada"), [{"page_id": "6", "page_name": "Pradasphere Fans", "n_ads": 9}]) is None


def _handler(pages_by_term, ads_by_page):
    def handler(url, params):
        if params and "search_terms" in params:
            return FakeResponse(200, {"data": pages_by_term.get(params["search_terms"], [])})
        ids = json.loads(params["search_page_ids"])
        return FakeResponse(200, {"data": [a for p in ids for a in ads_by_page.get(p, [])]})
    return handler


def test_first_run_finds_pages_itself_then_backfills(tmp_data):
    reg = Registry(1, "DRAFT", [house("gucci", "Gucci", group="treated"), house("prada", "Prada")])
    terms = {"Gucci": [{"page_id": "11", "page_name": "Gucci"}] * 4 + [{"page_id": "12", "page_name": "Gucci Beauty"}] * 9}
    ads = {"11": [api_ad("a", "11", "2026-01-05T00:00:00+0000")], "12": [api_ad("b", "12", "2026-01-05T00:00:00+0000")]}
    c = AdLibraryClient("TOKEN1234567890", session=FakeSession(_handler(terms, ads)), sleep=lambda s: None, pause=0)
    s = collect.run(c, reg, "backfill", "r1", today=date(2026, 10, 5))
    assert s["page_status"] == {"gucci": "provisional"} and s["houses_without_pages"] == ["prada"]
    assert s["new"] == 1, "the beauty page has more ads but is never collected"
    assert json.loads(config.CANDIDATES_FILE.read_text())["selected"]["gucci"]["page_id"] == "11"


def test_confirmed_registry_pages_take_precedence(tmp_data):
    reg = Registry(1, "DRAFT", [house("gucci", "Gucci", pages=["99"], group="treated")])
    mapping, status = collect.page_map(reg, {"selected": {"gucci": {"page_id": "11"}}})
    assert mapping == {"99": "gucci"} and status == {"gucci": "confirmed"}


def test_analysis_waits_until_both_files_are_frozen(tmp_data, tmp_path, monkeypatch):
    pre = tmp_path / "PREREGISTRATION.md"
    pre.write_text("# x\n\nSTATUS: DRAFT.\n")
    draft = Registry(1, "DRAFT", [house("gucci", "Gucci", pages=["1"])])
    frozen = Registry(1, "FROZEN", [house("gucci", "Gucci", pages=["1"])])
    assert len(analysis.gate(draft, pre)) == 2
    pre.write_text("# x\n\nSTATUS: FROZEN on 5 October 2026.\n")
    assert analysis.gate(draft, pre) == ["registry/houses.yml is not frozen"]
    assert analysis.gate(frozen, pre) == []
    assert "no confirmed pages" in analysis.gate(Registry(1, "FROZEN", [house("g", "G")]), pre)[0]
    monkeypatch.setattr(config, "PREREG_FILE", pre)
    monkeypatch.setattr(analysis.registry, "load", lambda *a, **k: draft)
    assert analysis.main([]) == 0
    assert not (config.RESULTS_DIR / "summary.json").exists()


def test_unconfirmed_pages_never_reach_analysis():
    reg = Registry(1, "FROZEN", [house("gucci", "Gucci", pages=["11"])])
    ads = {"a": {"house_id": "gucci", "page_id": "11"}, "b": {"house_id": "gucci", "page_id": "12"},
           "c": {"house_id": "prada", "page_id": "13"}}
    assert list(analysis.confirmed_only(ads, reg)) == ["a"]


class FakeBrowser:
    def __init__(self):
        self.calls = []

    def resolve(self, ad_id):
        self.calls.append(ad_id)
        return [f"https://scontent.x.fbcdn.net/{ad_id}.jpg"]

    def close(self):
        pass


def test_auto_resolver_uses_the_browser_only_when_static_finds_nothing():
    pages = {"has": '<img src="https://scontent-a.xx.fbcdn.net/v/t39/1_n.jpg?oh=1">', "empty": "<html></html>"}
    s = FakeSession(lambda url, params: FakeResponse(200, text=pages["has" if "id=has" in url else "empty"]))
    b = FakeBrowser()
    r = AutoResolver("T", session=s, browser_factory=lambda: b)
    assert r.resolve("has") and b.calls == []
    assert r.resolve("empty") == ["https://scontent.x.fbcdn.net/empty.jpg"] and b.calls == ["empty"]
    assert r.used == {"static": 1, "browser": 1}


def test_auto_resolver_carries_on_without_playwright():
    s = FakeSession(lambda url, params: FakeResponse(200, text="<html></html>"))

    def broken():
        raise ImportError("no playwright")

    r = AutoResolver("T", session=s, browser_factory=broken)
    assert r.resolve("x") == [] and r.resolve("y") == []


def test_looser_matches_are_suggested_not_collected():
    reg = Registry(1, "DRAFT", [house("saint_laurent", "Saint Laurent"), house("gucci", "Gucci")])
    candidates = {"selected": {"saint_laurent": {"page_id": "8", "page_name": "Saint Laurent Paris", "rule": "contains"},
                               "gucci": {"page_id": "1", "page_name": "Gucci", "rule": "exact"}}}
    mapping, status = collect.page_map(reg, candidates)
    assert mapping == {"1": "gucci"} and status == {"gucci": "provisional"}
