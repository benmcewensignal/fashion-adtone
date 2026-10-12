"""Google's ads: which advertiser accounts are the houses', and that nothing runs without a sign-in."""
import json

from adtone import adstransparency as A, registry


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
    # "defending" carries "fendi": discover matches it, so collect reads only the checked list
    assert A.match(registry.load(), "Defending Champions", "Defending Champions Ltd")[0] == "fendi"


def test_the_pattern_is_plain_lower_case_names():
    pat = A.name_pattern(registry.load())
    assert "hermes" in pat.split("|") and "dolcegabbana" in pat.split("|") and pat == pat.lower()


def test_no_sign_in_records_the_wait(tmp_data, monkeypatch):
    monkeypatch.delenv("GCP_SERVICE_ACCOUNT", raising=False)
    assert A.main(["auto", "--run", "t1"]) == 0
    rows = [json.loads(x) for x in A.paths()["prov"].read_text().splitlines()]
    assert rows[-1]["waiting"] == "no GCP_SERVICE_ACCOUNT"


def test_collect_needs_the_confirmed_list(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "CONFIRMED", tmp_path / "missing.csv")
    assert A.confirmed() == {}
    f = tmp_path / "google_advertisers.csv"
    f.write_text("house,advertiser_id,legal_name,checked\nchanel,AR123,Chanel Limited,yes\n", encoding="utf-8")
    monkeypatch.setattr(A, "CONFIRMED", f)
    assert A.confirmed() == {"AR123": "chanel"}
