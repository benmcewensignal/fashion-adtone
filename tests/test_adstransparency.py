"""Google's ads: which advertiser accounts are the houses', that nobody else's name is kept, the ads made small,
and that nothing runs without a sign-in."""
import json
from datetime import date, timedelta

from adtone import adstransparency as A, registry, store


def test_names_match_the_house_not_its_licensees():
    reg = registry.load()
    assert A.match(reg, "CHANEL", "Chanel Limited") == ("chanel", "disclosed name")
    assert A.match(reg, "Christian Dior", "Christian Dior Couture SA")[0] == "dior"
    assert A.match(reg, "Hermès", "Hermès Sellier")[0] == "hermes"
    assert A.match(reg, "Saint Laurent", "Yves Saint Laurent SAS")[0] == "saint_laurent"
    assert A.match(reg, "Miu Miu", "Prada S.p.A.") == ("miu_miu", "disclosed name")
    assert A.match(reg, "Parfums Christian Dior", "Parfums Christian Dior")[0] is None
    assert A.match(reg, "YSL Beauty", "L'Oreal SA")[0] is None
    assert A.match(reg, "Acme", "Acme Ltd") == (None, "no house's name")


def test_a_chance_match_is_why_accounts_are_confirmed_by_hand():
    # "defending" carries "fendi": the loose reading matches it, the house's own rule does not
    assert A.match(registry.load(), "Defending Champions", "Defending Champions Ltd")[0] == "fendi"
    assert A.own_house("Defending Champions", "Defending Champions Ltd") is None


def test_own_accounts_are_the_houses_companies():
    assert A.own_house("Chanel SAS", "Chanel SAS") == "chanel"
    assert A.own_house("CHANEL (AUSTRALIA) PTY LTD", "") == "chanel"
    assert A.own_house("Miu Miu", "Prada S.p.A.") == "miu_miu"
    assert A.own_house("Prada Spa", "Prada Spa") == "prada"
    assert A.own_house("Hermès Sellier Sas", "") == "hermes" and A.own_house("HERMES SELLIER SAS", "") == "hermes"
    assert A.own_house("Hermes Germany GmbH", "Hermes Germany GmbH") is None        # the parcel firm
    assert A.own_house("Valentino S.p.A.", "") == "valentino"
    assert A.own_house("Mario Valentino S.p.A.", "") is None
    assert A.own_house("Parfums Christian Dior", "") is None and A.own_house("Fondazione Prada", "") is None
    assert A.own_house("Margiela SAS", "") == "margiela" and A.own_house("Maison Margiela", "") == "margiela"
    assert A.own_house("Louis Vuitton Österreich Ges.m.b.h.", "") == "louis_vuitton"
    assert A.own_house("Chloé Damien", "Chloé Damien") is None                      # a person
    assert A.group_of("Kering SA", "") == "kering" and A.group_of("Kering Eyewear SpA", "") is None
    assert A.group_of("Puig Ferrer Juan", "") is None


def test_only_company_names_are_kept():
    reg = registry.load()
    row = lambda i, d, v="VERIFIED": {"advertiser_id": i, "disclosed": d, "legal": d, "location": "FR",
                                      "verification": v, "ads": 3, "video": 1, "image": 1, "text": 1}
    kept, refused = A.sift(reg, [row("AR1", "Chloé SAS"), row("AR2", "Chloé Damien"), row("AR3", "Kering"),
                                 row("AR4", "Chanel", "UNVERIFIED"), row("AR5", "Studio Razola Dior")])
    assert [(k["advertiser_id"], k["house"], k["group"], k["status"]) for k in kept] == [
        ("AR4", "chanel", None, "doubt"), ("AR1", "chloe", None, "house"), ("AR3", None, "kering", "house")]
    assert refused == {"chloe": 1, "dior": 1}
    assert "Chloé Damien" not in json.dumps(kept, ensure_ascii=False)


def test_the_pattern_is_plain_lower_case_names():
    pat = A.name_pattern(registry.load())
    alts = pat.split("|")
    assert "hermes" in alts and "dolcegabbana" in alts and "margiela" in alts and "kering" in alts
    assert pat == pat.lower()


def test_an_ad_made_small():
    r = {"creative_id": "CR1", "advertiser_id": "AR1", "ad_format_type": "VIDEO", "topic": None, "ad_funded_by": None,
         "aud": {"demographic_info": "CRITERIA_INCLUDED", "geo_location": "CRITERIA_UNUSED", "customer_lists": None},
         "regions": [
             {"region_code": "FR", "first_shown": "2025-03-01", "last_shown": "2025-03-20", "lo": 1000, "hi": 2000,
              "since": "2025-03-01", "till": "2025-03-20",
              "surfaces": [{"surface": "YOUTUBE", "lo": 900, "hi": 1800}, {"surface": "SEARCH", "lo": 0, "hi": 100}]},
             {"region_code": "DE", "first_shown": "2025-02-27", "last_shown": "2025-03-10", "lo": 0, "hi": 1000,
              "since": "2025-02-27", "till": "2025-03-10", "surfaces": [{"surface": "YOUTUBE", "lo": 0, "hi": 1000}]},
             {"region_code": "EEA", "first_shown": "2025-02-27", "last_shown": "2025-03-20", "lo": 1000, "hi": 3000,
              "since": None, "till": None, "surfaces": []}]}
    ad = A.compact(r, "dior", "doubt")
    assert ad["first"] == "2025-02-27" and ad["last"] == "2025-03-20"
    assert (ad["lo"], ad["hi"]) == (1000, 3000)                                  # FR and DE; the EEA row not added
    assert ad["on"] == {"YOUTUBE": [900, 2800], "SEARCH": [0, 100]}
    assert ad["where"]["EEA"] == ["2025-02-27", "2025-03-20", 1000, 3000]
    assert ad["aud"] == {"demographic_info": "INCLUDED", "geo_location": "UNUSED"}
    assert ad["status"] == "doubt" and "topic" not in ad and A.shard(ad) == "dior-2025"
    assert "status" not in A.compact(r, "dior", "house")


def test_auto_discovers_when_the_rules_change_or_a_month_passes(tmp_data):
    assert A.due_discovery() == "the matching rules changed"
    t = A.paths()["table"]
    store.write_state(t, {"rules": A.RULES_VERSION, "read_at": date.today().isoformat() + "T06:20:00Z"})
    assert A.due_discovery() is None
    store.write_state(t, {"rules": A.RULES_VERSION, "read_at": (date.today() - timedelta(days=40)).isoformat()})
    assert A.due_discovery() == "40 days since the last discovery"


def test_no_sign_in_records_the_wait(tmp_data, monkeypatch):
    monkeypatch.delenv("GCP_SERVICE_ACCOUNT", raising=False)
    assert A.main(["auto", "--run", "t1"]) == 0
    rows = [json.loads(x) for x in A.paths()["prov"].read_text().splitlines()]
    assert rows[-1]["waiting"] == "no GCP_SERVICE_ACCOUNT"


def test_collect_needs_the_confirmed_list(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "CONFIRMED", tmp_path / "missing.csv")
    assert A.confirmed() == {}
    f = tmp_path / "google_advertisers.csv"
    f.write_text("house,advertiser_id,legal_name,status\nchanel,AR123,Chanel Limited,house\nchanel,AR9,,doubt\n"
                 "dior,AR7,Christian Dior Couture SAS,\n", encoding="utf-8")
    monkeypatch.setattr(A, "CONFIRMED", f)
    assert A.confirmed() == {"AR123": ("chanel", "house"), "AR9": ("chanel", "doubt"), "AR7": ("dior", "house")}


def test_the_checked_list_is_the_rules_own():
    """Every account in reference/google_advertisers.csv meets its house's rule; the list is read by eye, not
    widened by hand."""
    for aid, (house, status) in A.confirmed().items():
        assert status in ("house", "doubt"), aid
    import csv
    with A.CONFIRMED.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            assert A.own_house(r["disclosed_name"], r["legal_name"]) == r["house"], r["advertiser_id"]
