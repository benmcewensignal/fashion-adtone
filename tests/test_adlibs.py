"""The shared ad-library loop, Snap's gallery and Microsoft's library: the house's own advertiser found, its
ads searched and kept, pictures read, the record's shape kept without its values. Network faked; the APIs'
shapes follow their documentation as read on 17 October 2026, with Snap's ad fields (undocumented) as
scrapers of the gallery show them."""
import json
from datetime import date

from adtone import adlibs, msads, snap, store
from adtone.embed import FakeEmbedder
from adtone.registry import House, Registry
from adtone.score import FakeScorer, load_rubric
from adtone.synth import FakeResponse, FakeSession, jpeg_bytes, toned_image

GUCCI = House("gucci", "Gucci", "treated", "Kering", ["Gucci"], [], [])


def test_a_records_shape_keeps_its_keys_and_types_but_no_values():
    s = adlibs.shape({"id": "123", "headline": "The new collection", "impressions": {"fr": 12, "de": 3}, "media": [{"url": "x"}]})
    assert s == {"id": "str", "headline": "str", "impressions": {"fr": "int", "de": "int"}, "media": [{"url": "str"}]}
    assert "collection" not in json.dumps(s)


def _snap_ad(i, payer, with_video=False):
    ad = {"id": f"snap-{i}", "adAccountName": f"{payer} EMEA", "payingAdvertiserName": payer, "status": "ACTIVE",
          "creativeType": "WEB_VIEW", "headline": "La nouvelle collection", "callToAction": "SHOP NOW",
          "startDate": f"2026-09-{10 + i:02d}T22:00:00.000Z", "impressionsTotal": 1000 * (i + 1),
          "impressionsByCountry": [{"country": "fr", "impressions": 600 * (i + 1)}, {"country": "de", "impressions": 400 * (i + 1)}],
          "targeting": {"minAge": "18"}, "mediaDownloadUrl": f"https://cf-st.sc-cdn.example/d/{i}.jpg"}
    if with_video:
        ad["mediaDownloadUrl"] = f"https://cf-st.sc-cdn.example/d/{i}.mp4"
        ad["topSnapThumbnailUrl"] = f"https://cf-st.sc-cdn.example/t/{i}.jpg"
    return {"sub_request_status": "SUCCESS", "ad_preview": ad}


def _snap_session(calls):
    def post(url, params, body, data):
        calls.append((url, body))
        if "cursor=" in url:
            return FakeResponse(200, payload={"request_status": "SUCCESS", "paging": {},
                                              "ad_previews": [_snap_ad(3, "Guccio Gucci S.p.A.")]})
        name, status = body["paying_advertiser_name"], body["status"]
        assert body["countries"][0] == "de" and body["start_date"].endswith("T00:00:00.000Z") and body["end_date"].endswith("T00:00:00.000Z")
        if name == "Gucci" and status == "ACTIVE":
            return FakeResponse(200, payload={"request_status": "SUCCESS",
                                              "paging": {"next_link": "https://adsapi.snapchat.com/v1/ads_library/ads/search?cursor=ABC"},
                                              "ad_previews": [_snap_ad(0, "Guccio Gucci S.p.A."), _snap_ad(1, "Gucci Beauty (Coty)"),
                                                              _snap_ad(2, "Guccio Gucci S.p.A.", with_video=True)]})
        return FakeResponse(200, payload={"request_status": "SUCCESS", "paging": {}, "ad_previews": []})

    def get(url, params):
        if url.endswith(".jpg"):
            n = int(url.rsplit("/", 1)[-1].split(".")[0])
            return FakeResponse(200, content=jpeg_bytes(toned_image(0.3 + 0.05 * n, seed=n)))
        if url.endswith(".mp4"):
            r = FakeResponse(200, content=b"not really a video")
            r.headers = {"Content-Type": "video/mp4"}
            return r
        return FakeResponse(404)
    sess = FakeSession(get, post)
    return sess


def test_snap_ads_are_searched_by_the_names_the_house_pays_under_and_kept_when_its_own(tmp_data):
    calls = []
    sess = _snap_session(calls)
    src = snap.Source(sess, sleep=lambda s: None, pace_s=0)
    out = adlibs.collect(src, Registry(1, "T", [GUCCI]), "r1", FakeEmbedder(), FakeScorer(load_rubric()), today=date(2026, 10, 17))
    assert out["problems"] == {} and out["new_ads"] == 3 and out["ads_found"] == 3
    rows = store.read_jsonl(adlibs.paths("snap")["ads"] / "gucci.jsonl")
    assert {r["ad_id"] for r in rows} == {"snap-0", "snap-2", "snap-3"}
    r0 = next(r for r in rows if r["ad_id"] == "snap-0")
    assert r0["advertiser"] == "Guccio Gucci S.p.A." and r0["first_shown"] == "2026-09-10" and r0["impressions"] == 1000
    assert r0["impressions_by_country"] == {"fr": 600, "de": 400} and r0["status"] == "ACTIVE" and len(r0["media"]) == 1
    assert "headline" not in r0 and "La nouvelle" not in json.dumps(rows)
    st = store.read_state(adlibs.paths("snap")["state"])
    assert st["names_answered"] == {"gucci": ["Gucci"]} and st["shape"]["headline"] == "str" and st["shape"]["impressionsByCountry"] == [{"country": "str", "impressions": "int"}]
    names = [b["paying_advertiser_name"] for u, b in calls if b]
    assert names[:2] == ["Gucci", "Gucci"] and "Guccio Gucci S.p.A." in names   # own name, both statuses, then legal names
    assert adlibs.probe("snap", "r1") == []


def test_snap_stops_after_repeated_refusals_and_keeps_what_came(tmp_data):
    n = {"posts": 0}

    def post(url, params, body, data):
        n["posts"] += 1
        return FakeResponse(429, payload={"request_status": "RATE_LIMITED"})
    src = snap.Source(FakeSession(lambda u, p: FakeResponse(404), post), sleep=lambda s: None, pace_s=0)
    out = adlibs.collect(src, Registry(1, "T", [GUCCI]), "r1", today=date(2026, 10, 17))
    assert out["stopped"].startswith("Snap refused 8 requests") and n["posts"] == 8
    assert adlibs.probe("snap", "r1") == ["stopped: Snap refused 8 requests in a row (HTTP 429)"]


def _ms_session():
    def get(url, params):
        if url.endswith("/Advertisers"):
            assert params["searchText"] == "Gucci"
            return FakeResponse(200, payload=[{"AdvertiserId": 501, "AdvertiserName": "Guccio Gucci S.p.A.", "AdvertiserCountry": "IT", "IsVerified": True},
                                              {"AdvertiserId": 502, "AdvertiserName": "Gucci Outlet Store", "AdvertiserCountry": "DE", "IsVerified": False}])
        if url.endswith("/Ads"):
            assert params["advertiserId"] == "501" and params["startDate"] == "2025-10-18"
            if params["skip"] == 0:
                return FakeResponse(200, payload=[{"AdId": 9001, "AdvertiserId": 501, "AdvertiserName": "Guccio Gucci S.p.A.", "Title": "Gucci Official Site",
                                                   "Description": "Discover the new collection", "DisplayUrl": "gucci.com", "DestinationUrl": "https://www.gucci.com/fr/fr/",
                                                   "AssetJson": json.dumps({"Images": [{"ImageUrl": "https://th.bing.example/a/9001.jpg"}]})},
                                                  {"AdId": 9002, "AdvertiserId": 501, "AdvertiserName": "Guccio Gucci S.p.A.", "Title": "Gucci Bags",
                                                   "Description": "Shop", "DisplayUrl": "gucci.com", "DestinationUrl": "https://www.gucci.com/it/it/", "AssetJson": None}])
            return FakeResponse(200, payload=[])
        if "/Ads/9001" in url or "/Ads/9002" in url:
            n = url.rsplit("/", 1)[-1]
            return FakeResponse(200, payload={"AdId": int(n), "AdDetails": {"PaidForByName": "Guccio Gucci S.p.A.", "StartDate": "2026-03-01T00:00:00Z",
                                                                             "EndDate": "2026-10-15T00:00:00Z", "TotalImpressionsRange": "10K-50K",
                                                                             "ImpressionsByCountry": [{"Country": 66, "ImpressionShare": "61.0%"}, {"Country": 93, "ImpressionShare": "39.0%"}],
                                                                             "Targets": [{"TargetType": 3, "UsedForExclusion": False}, {"TargetType": 2, "UsedForExclusion": True}]}})
        if url.endswith("9001.jpg"):
            return FakeResponse(200, content=jpeg_bytes(toned_image(0.5, seed=1)))
        return FakeResponse(404)
    return FakeSession(get)


def test_microsoft_ads_come_with_their_details_and_an_image_when_there_is_one(tmp_data):
    src = msads.Source(_ms_session(), sleep=lambda s: None, pace_s=0)
    out = adlibs.collect(src, Registry(1, "T", [GUCCI]), "r1", FakeEmbedder(), FakeScorer(load_rubric()), today=date(2026, 10, 17))
    assert out["problems"] == {} and out["new_ads"] == 2 and out["pictures"] == 1 and out["read_ok"] == 1
    rows = {r["ad_id"]: r for r in store.read_jsonl(adlibs.paths("msads")["ads"] / "gucci.jsonl")}
    r = rows["9001"]
    assert r["paid_by"] == "Guccio Gucci S.p.A." and r["first_shown"] == "2026-03-01" and r["last_shown"] == "2026-10-15"
    assert r["impressions_range"] == "10K-50K" and r["impression_share_by_country"] == {"66": "61.0%", "93": "39.0%"}
    assert r["targets"] == ["age!", "location"] and r["domain"] == "gucci.com" and r["has_image"] and len(r["media"]) == 1
    assert rows["9002"]["has_image"] is False and rows["9002"]["media"] == []
    assert "Discover" not in json.dumps(list(rows.values()))
    st = store.read_state(adlibs.paths("msads")["state"])
    assert [a["id"] for a in st["advertisers"]["gucci"]["keep"]] == ["501"]
    assert st["advertisers"]["gucci"]["refused"][0]["name"] == "Gucci Outlet Store"


def _vendor_item(i, page_id, page_name):
    return {"ad_archive_id": f"77{i}", "page_id": page_id, "page_name": page_name, "ad_creative_bodies": ["The new collection. Shop now."],
            "publisher_platforms": ["FACEBOOK", "INSTAGRAM"], "is_active": True, "ad_delivery_start_date": f"2026-09-0{1 + i % 9}",
            "snapshot_images": [{"original_image_url": f"https://scontent.fbcdn.example/o/{i}.jpg", "resized_image_url": f"https://scontent.fbcdn.example/r/{i}.jpg"}],
            "regional_regulation_data": {"eu_total_reach": 1500 * (i + 1), "payer_beneficiary_data": [{"payer": page_name, "beneficiary": page_name}],
                                         "age_country_gender_reach_breakdown": [{"country": "FR", "age_gender_breakdowns": [{"age_range": "25-34", "male": 100, "female": 300}]}],
                                         "location_audience": [{"name": "France", "type": "country", "excluded": False}],
                                         "age_audience": {"min": 18, "max": 65}, "gender_audience": "All"}}


def _apify_session(datasets):
    runs = {}

    def post(url, params, body, data):
        if "/runs" in url:
            rid = f"run{len(runs) + 1}"
            runs[rid] = {"id": rid, "status": "RUNNING", "defaultDatasetId": f"ds-{rid}", "input": body}
            return FakeResponse(201, payload={"data": dict(runs[rid])})
        raise AssertionError(url)

    def get(url, params):
        if "/actor-runs/" in url:
            rid = url.rsplit("/", 1)[-1]
            runs[rid]["status"], runs[rid]["usageTotalUsd"] = "SUCCEEDED", 0.12
            return FakeResponse(200, payload={"data": dict(runs[rid])})
        if "/datasets/" in url:
            rid = url.split("/datasets/ds-")[1].split("/")[0]
            inp = runs[rid]["input"]
            key = "search:" + inp["searchTerms"][0] if inp.get("searchTerms") else "pages:" + ",".join(inp["pageIds"])
            return FakeResponse(200, payload=datasets[key])
        if url.endswith(".jpg"):
            n = int(url.rsplit("/", 1)[-1].split(".")[0])
            return FakeResponse(200, content=jpeg_bytes(toned_image(0.4 + 0.03 * n, seed=100 + n)))
        return FakeResponse(404)

    class S(FakeSession):
        def request(self, method, url, params=None, json=None, timeout=None, **kw):
            return post(url, params, json, None) if method == "POST" else get(url, params)
    return S(get, post)


def test_meta_through_the_vendor_finds_the_houses_pages_then_their_ads_in_our_row_shape(tmp_data):
    from adtone import metaweb
    datasets = {"search:Gucci": [_vendor_item(0, "100", "Gucci"), _vendor_item(1, "100", "Gucci"), _vendor_item(2, "200", "Gucci Beauty"),
                                 _vendor_item(3, "300", "Gucci Outlet Resale")],
                "pages:100": [_vendor_item(i, "100", "Gucci") for i in range(5)]}
    src = metaweb.Source(_apify_session(datasets), token="t", actor="bovi~meta-ads-library-scraper", countries="ALL", sleep=lambda s: None)
    out = adlibs.collect(src, Registry(1, "T", [GUCCI]), "r1", FakeEmbedder(), FakeScorer(load_rubric()), today=date(2026, 10, 17))
    assert out["problems"] == {} and out["new_ads"] == 5 and out["pictures"] == 5 and out["read_ok"] == 5
    st = store.read_state(adlibs.paths("metaweb")["state"])
    assert [p["id"] for p in st["advertisers"]["gucci"]["keep"]] == ["100"]
    assert {p["name"] for p in st["advertisers"]["gucci"]["refused"]} == {"Gucci Beauty", "Gucci Outlet Resale"}
    assert st["actor"] == "bovi~meta-ads-library-scraper" and len(st["vendor_runs"]) == 2 and st["vendor_runs"][0]["usd"] == 0.12
    rows = store.read_jsonl(adlibs.paths("metaweb")["ads"] / "gucci.jsonl")
    r = next(x for x in rows if x["ad_id"] == "770")
    assert r["page_id"] == "100" and r["start"] == "2026-09-01" and r["platforms"] == ["FACEBOOK", "INSTAGRAM"] and r["is_active"] is True
    assert r["eu_total_reach"] == 1500 and r["reach"]["by_country"] == {"FR": 400} and r["reach"]["by_gender"] == {"female": 300, "male": 100}
    assert r["beneficiary_payers"] == [{"payer": "Gucci", "beneficiary": "Gucci"}] and r["target_locations"][0]["name"] == "France"
    assert r["line"] in ("collection", "other", "season") and "copy" in r and "Shop now" not in json.dumps(rows)
    assert len(r["media"]) == 1   # the original and the resized picture are one picture
    official = metaweb.Source(_apify_session({}), token="t", actor="apify~facebook-ads-scraper", countries="ALL", sleep=lambda s: None)
    inp = official._input(page_ids=["100"], max_results=50)
    assert inp["startUrls"][0]["url"].endswith("search_type=page&view_all_page_id=100") and inp["resultsLimit"] == 50 and inp["scrapeAdDetails"] is True
    assert "q=Gucci&search_type=keyword_unordered" in official._input(terms=["Gucci"])["startUrls"][0]["url"]
    cc = metaweb.Source(_apify_session({}), token="t", actor="curious_coder~facebook-ads-library-scraper", countries="FR", sleep=lambda s: None)
    inp = cc._input(page_ids=["100"], max_results=120)
    assert inp["urls"][0]["url"].endswith("country=FR&search_type=page&view_all_page_id=100") and inp["scrapeAdDetails"] is True
    assert inp["limitPerSource"] == 120 and inp["scrapePageAds"]["countryCode"] == "FR"


def _pin_item(i, names, country="FR"):
    return {"pin_id": f"9{i:03d}", "ad_details": {"advertiser_names": names, "age_buckets": ["25-34"], "content_commercial": True,
                                                "countries": [country], "devices": [], "end_date": "2026-10-10", "genders": ["female"],
                                                "image_link": f"https://i.pinimg.example/originals/{i}.jpg", "interests": ["fashion"],
                                                "keywords_used": False, "pin_data": {"title": "The new collection", "details": "Shop now.",
                                                                                     "image_link": f"https://i.pinimg.example/originals/{i}.jpg", "video_link": None},
                                                "pinner_list_types": [], "start_date": "2026-09-20", "user_count_by_country": {"France": "0 - 10000"},
                                                "user_count_eu": "0 - 10000", "review_status": None}}


class _FakePinterest:
    """The page-driving methods replaced: what the page's name search offers, and what one search answers."""

    def __init__(self, src, calls):
        self.src, self.calls = src, calls
        src._names = self.names
        src._search = self.search

    def names(self, term):
        return ["Gucci", "Gucci Beauty", "Gucci Outlet"]

    def search(self, name, country, start, end):
        self.calls.append((name, country, start.isoformat(), end.isoformat()))
        assert name == "Gucci" and country in ("FR", "DE")
        if country == "FR" and end.isoformat() == "2026-10-17":
            return [_pin_item(1, ["Gucci"]), _pin_item(2, ["Gucci"]), _pin_item(3, ["Gucci"])]
        if country == "DE" and end.isoformat() == "2026-10-17":
            return [_pin_item(1, ["Gucci"], "DE")]
        return []


def _pictures(url, params):
    if url.endswith(".jpg"):
        n = int(url.rsplit("/", 1)[-1].split(".")[0])
        return FakeResponse(200, content=jpeg_bytes(toned_image(0.35 + 0.05 * n, seed=200 + n)))
    return FakeResponse(404)


def test_pinterest_reads_the_repository_a_month_and_a_country_at_a_time_and_keeps_each_ad_once(tmp_data):
    from adtone import pinterest
    calls = []
    src = pinterest.Source(page=object(), sess=FakeSession(_pictures), countries=("FR", "DE"), sleep=lambda s: None)
    _FakePinterest(src, calls)
    out = adlibs.collect(src, Registry(1, "T", [GUCCI]), "r1", FakeEmbedder(), FakeScorer(load_rubric()), today=date(2026, 10, 17))
    assert out["problems"] == {} and out["new_ads"] == 3 and out["pictures"] == 3
    st = store.read_state(adlibs.paths("pinterest")["state"])
    assert [a["name"] for a in st["advertisers"]["gucci"]["keep"]] == ["Gucci"]
    rows = {r["ad_id"]: r for r in store.read_jsonl(adlibs.paths("pinterest")["ads"] / "gucci.jsonl")}
    assert set(rows) == {"9001", "9002", "9003"} and rows["9001"]["user_count_eu"] == "0 - 10000"
    assert rows["9001"]["first_shown"] == "2026-09-20" and rows["9001"]["countries"] == ["FR"] and rows["9001"]["genders"] == ["female"]
    assert "The new collection" not in json.dumps(list(rows.values())) and rows["9001"]["copy"]
    windows = sorted({(c[2], c[3]) for c in calls})
    assert windows[-1] == ("2026-09-17", "2026-10-17") and windows[0] == ("2025-10-18", "2025-11-10") and len(windows) == 12
