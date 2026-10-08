import json

import pytest

from adtone.adlib import AdLibraryClient, ParamError, TokenError, scrub
from adtone.synth import FakeResponse, FakeSession, api_ad


def client(handler, **kw):
    sleeps = []
    c = AdLibraryClient("TOKEN1234567890", session=FakeSession(handler), sleep=sleeps.append, pause=0, **kw)
    return c, sleeps


def test_pagination_follows_next_until_exhausted():
    pages = {
        "first": {"data": [api_ad("1", "p", "2026-01-01")], "paging": {"next": "https://graph.facebook.com/next1"}},
        "https://graph.facebook.com/next1": {"data": [api_ad("2", "p", "2026-01-02")], "paging": {"next": "https://graph.facebook.com/next2"}},
        "https://graph.facebook.com/next2": {"data": []},
    }
    c, _ = client(lambda url, params: FakeResponse(200, pages["first" if params else url]))
    rows = list(c.search(countries=["FR", "IT"], fields=["id", "page_id"], page_ids=["p"]))
    assert [r["id"] for r in rows] == ["1", "2"]
    url, params = c.session.calls[0]
    assert url.endswith("/ads_archive")
    assert json.loads(params["ad_reached_countries"]) == ["FR", "IT"]
    assert json.loads(params["search_page_ids"]) == ["p"]
    assert params["fields"] == "id,page_id" and params["ad_type"] == "ALL" and params["ad_active_status"] == "ALL"
    assert c.session.calls[1][1] is None   # next URL carries its own parameters


def test_page_ids_are_chunked_by_ten():
    seen = []

    def handler(url, params):
        ids = json.loads(params["search_page_ids"])
        seen.append(ids)
        return FakeResponse(200, {"data": [api_ad(f"ad{i}", i, "2026-01-01") for i in ids]})

    c, _ = client(handler)
    rows = list(c.ads_for_pages([str(i) for i in range(23)], ["FR"], ["id"], [], None))
    assert [len(s) for s in seen] == [10, 10, 3]
    assert len(rows) == 23
    with pytest.raises(ValueError):
        list(c.search(countries=["FR"], fields=["id"], page_ids=[str(i) for i in range(11)]))


def test_rate_limit_backs_off_then_succeeds():
    state = {"n": 0}

    def handler(url, params):
        state["n"] += 1
        if state["n"] < 3:
            return FakeResponse(400, {"error": {"code": 613, "message": "Calls to this api have exceeded the rate limit."}})
        return FakeResponse(200, {"data": [api_ad("1", "p", "2026-01-01")]})

    c, sleeps = client(handler)
    assert len(list(c.search(countries=["FR"], fields=["id"], page_ids=["p"]))) == 1
    assert sleeps[:2] == [60, 120]


def test_expired_token_stops_immediately_without_retrying():
    c, sleeps = client(lambda url, params: FakeResponse(400, {"error": {"code": 190, "message": "Error validating access token"}}))
    with pytest.raises(TokenError):
        list(c.search(countries=["FR"], fields=["id"], page_ids=["p"]))
    assert sleeps == [] and len(c.session.calls) == 1


def test_rejected_optional_field_falls_back_to_core_fields():
    def handler(url, params):
        if "eu_total_reach" in params["fields"]:
            return FakeResponse(400, {"error": {"code": 100, "message": "(#100) Tried accessing nonexisting field (eu_total_reach)"}})
        return FakeResponse(200, {"data": [api_ad("1", "p", "2026-01-01")]})

    c, _ = client(handler)
    rows = list(c.ads_for_pages(["p"], ["FR"], ["id", "page_id"], ["eu_total_reach"], None))
    assert len(rows) == 1
    assert any("core fields" in n for n in c.notes)


def test_rejected_country_list_falls_back_to_one_country_at_a_time_without_duplicates():
    def handler(url, params):
        countries = json.loads(params["ad_reached_countries"])
        if len(countries) > 1:
            return FakeResponse(400, {"error": {"code": 100, "message": "Invalid parameter ad_reached_countries"}})
        return FakeResponse(200, {"data": [api_ad("shared", "p", "2026-01-01"), api_ad(f"only-{countries[0]}", "p", "2026-01-01")]})

    c, _ = client(handler)
    rows = list(c.ads_for_pages(["p"], ["FR", "IT"], ["id"], [], None))
    assert sorted(r["id"] for r in rows) == ["only-FR", "only-IT", "shared"]


def test_other_parameter_errors_are_raised():
    c, _ = client(lambda url, params: FakeResponse(400, {"error": {"code": 100, "message": "search_type invalid"}}))
    with pytest.raises(ParamError):
        list(c.ads_for_pages(["p"], ["FR"], ["id"], [], None))


def test_scrub_removes_tokens():
    assert "SECRET" not in scrub("GET https://x/render_ad/?id=1&access_token=SECRET99 failed")


def test_missing_token_is_refused():
    with pytest.raises(TokenError):
        AdLibraryClient("")


def test_a_refusal_carries_metas_code_and_its_advice_but_never_the_token():
    from adtone.adlib import GraphError, TokenError, classify
    body = {"error": {"message": "Application does not have permission for this action", "type": "OAuthException",
                      "code": 10, "error_subcode": 2332002, "error_user_title": "Permission needed",
                      "error_user_msg": "To access the API, you'll need to follow the steps at facebook.com/ads/library/api. "
                                        "See https://x/?access_token=SECRET1234"}}
    e = classify(400, body)
    assert isinstance(e, GraphError) and not isinstance(e, TokenError)
    text = str(e)
    assert "code 10, subcode 2332002" in text and "facebook.com/ads/library/api" in text and "Permission needed" in text
    assert "SECRET1234" not in text
    assert str(classify(500, None)) == "HTTP 500"
