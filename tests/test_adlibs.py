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
    assert inp["limitPerSource"] == 120 and inp["scrapePageAds"]["countryCode"] == "FR" and inp["scrapePageAds"]["period"] == ""
    cc.period = "last30d"     # the weekly top-up asks for the last thirty days only
    assert cc._input(page_ids=["100"])["scrapePageAds"]["period"] == "last30d"


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


def test_without_the_reader_the_pictures_wait_and_the_read_stage_takes_them_later(tmp_data):
    calls = []
    sess = _snap_session(calls)
    src = snap.Source(sess, sleep=lambda s: None, pace_s=0)
    out = adlibs.collect(src, Registry(1, "T", [GUCCI]), "r1", today=date(2026, 10, 17))
    assert out["new_ads"] == 3 and out["deferred"] == 3 and out["pictures"] == 0
    assert not [u for u, b in calls if u.endswith(".jpg")]          # nothing fetched: no reader to read it
    rows = store.read_jsonl(adlibs.paths("snap")["ads"] / "gucci.jsonl")
    assert all(r["media"] == [] and r["media_pending"] for r in rows)
    pend = store.read_jsonl(adlibs.paths("snap")["pending"] / "gucci.jsonl")
    assert {p["ad_id"] for p in pend} == {"snap-0", "snap-2", "snap-3"} and all(p["urls"] for p in pend)
    assert "La nouvelle" not in json.dumps(pend)
    # the read stage, within a budget that lets two ads through
    clock = iter([0, 0, 0, 1, 9999, 9999, 9999])
    out = adlibs.read_pending("snap", Registry(1, "T", [GUCCI]), "r2", FakeEmbedder(), FakeScorer(load_rubric()), budget_s=100,
                              sess=sess, today=date(2026, 10, 17), clock=lambda: next(clock), chunk_ads=1, fetch_workers=1)
    assert out["ads_read"] == 2 and out["ads_left"] == 1 and out["stopped"] == "budget" and out["pictures"] >= 1
    rows = {r["ad_id"]: r for r in store.read_jsonl(adlibs.paths("snap")["ads"] / "gucci.jsonl")}
    done = [r for r in rows.values() if "media_pending" not in r]
    assert len(done) == 2 and any(r["media"] for r in done)
    left = store.read_jsonl(adlibs.paths("snap")["pending"] / "gucci.jsonl")
    assert len(left) == 1 and rows[left[0]["ad_id"]]["media_pending"]
    obs = store.read_jsonl(adlibs.paths("snap")["obs"] / f"{load_rubric().version}.jsonl")
    assert len(obs) >= 1 and all(o["status"] == "ok" for o in obs)
    # a dynamic unit is left for later when asked, and read when not
    path = adlibs.paths("snap")["ads"] / "gucci.jsonl"
    rows = store.read_jsonl(path)
    for r in rows:
        if r.get("media_pending"):
            r["display_format"] = "DCO"
    store.write_jsonl(path, rows)
    out = adlibs.read_pending("snap", Registry(1, "T", [GUCCI]), "r3", FakeEmbedder(), FakeScorer(load_rubric()), budget_s=100,
                              sess=sess, today=date(2026, 10, 17), skip_dynamic=True)
    assert out["ads_read"] == 0 and out["dynamic_left"] == 1 and len(store.read_jsonl(adlibs.paths("snap")["pending"] / "gucci.jsonl")) == 1
    # the rest on the next run
    out = adlibs.read_pending("snap", Registry(1, "T", [GUCCI]), "r4", FakeEmbedder(), FakeScorer(load_rubric()), budget_s=100,
                              sess=sess, today=date(2026, 10, 17))
    assert out["ads_read"] == 1 and out["ads_left"] == 0 and out["stopped"] is None
    assert not store.read_jsonl(adlibs.paths("snap")["pending"] / "gucci.jsonl")


class _FakeClothes:
    """A second reader, the clothes rubric, answering with one fixed dict per picture."""

    def __init__(self):
        self.rubric = load_rubric("clothes-v1")
        self.instrument = "clothes-v1@fake"
        self.calls = 0

    def score_many(self, jpegs):
        self.calls += 1
        return [{"garments": ["dress"], "n": len(jpegs)} for _ in jpegs]


def test_each_picture_goes_to_every_reader_and_each_reader_keeps_its_own_observations(tmp_data):
    sess = _snap_session([])
    src = snap.Source(sess, sleep=lambda s: None, pace_s=0)
    clothes = _FakeClothes()
    out = adlibs.collect(src, Registry(1, "T", [GUCCI]), "r1", FakeEmbedder(), [FakeScorer(load_rubric()), clothes],
                         today=date(2026, 10, 17))
    assert out["new_ads"] == 3 and out["pictures"] >= 2 and out["ok_clothes-v1"] == out["pictures"] and clothes.calls >= 1
    tone = store.read_jsonl(adlibs.paths("snap")["obs"] / "tone-v1.jsonl")
    clo = store.read_jsonl(adlibs.paths("snap")["obs"] / "clothes-v1.jsonl")
    assert {o["sha"] for o in tone} == {o["sha"] for o in clo} and all(o["instrument"] == "clothes-v1@fake" for o in clo)
    assert all(o["status"] == "ok" and o["output"]["garments"] == ["dress"] for o in clo)
    # a second collection reads nothing again
    before = clothes.calls
    src2 = snap.Source(_snap_session([]), sleep=lambda s: None, pace_s=0)
    src2.house_refresh_days = 0
    out = adlibs.collect(src2, Registry(1, "T", [GUCCI]), "r2", FakeEmbedder(), [FakeScorer(load_rubric()), clothes],
                         today=date(2026, 10, 17))
    assert out["new_ads"] == 0 and clothes.calls == before and not out.get("reread")
    # a reader added later: the pictures are fetched again and read by it alone
    other = _FakeClothes()
    other.rubric = type("R", (), {"version": "clothes-v2"})()
    other.instrument = "clothes-v2@fake"
    tone = FakeScorer(load_rubric())
    src3 = snap.Source(_snap_session([]), sleep=lambda s: None, pace_s=0)
    src3.house_refresh_days = 0
    out = adlibs.collect(src3, Registry(1, "T", [GUCCI]), "r3", FakeEmbedder(), [tone, other], today=date(2026, 10, 17))
    assert out["reread"] >= 2 and other.calls >= 1 and tone.calls == 0       # the ad without a picture is not fetched again
    assert len(store.read_jsonl(adlibs.paths("snap")["obs"] / "clothes-v2.jsonl")) == len(clo)


def test_meta_keeps_the_houses_own_page_and_a_rematch_drops_a_licensees_rows(tmp_data):
    from adtone import metaweb
    from adtone.registry import House
    loewe = House("loewe", "Loewe", "treated", "LVMH", ["Loewe"], [], [])
    assert metaweb.is_page(loewe, {"name": "LOEWE", "ads": 76})
    assert not metaweb.is_page(loewe, {"name": "LOEWE Perfumes", "ads": 60})          # Puig's licence, not the house
    assert not metaweb.is_page(loewe, {"name": "Loewe Greece", "ads": 1})             # one ad: noise
    assert not metaweb.is_page(loewe, {"name": "Loewe", "ads": 9, "categories": ["Personal blog"]})
    celine = House("celine", "Celine", "treated", "LVMH", ["Celine"], [], [])
    assert not metaweb.is_page(celine, {"name": "Céline Rousseau - Blog Santé", "ads": 1})
    assert not metaweb.is_page(celine, {"name": "celine_maureen", "ads": 1})
    # the actor's memory: the largest power of two within one address per 512 MB
    for n, want in ((1, 512), (2, 1024), (3, 1024), (5, 2048), (8, 4096), (20, 4096)):
        memory = 512
        while memory * 2 <= 512 * n and memory * 2 <= 4096:
            memory *= 2
        assert memory == want, (n, memory)
    # rows of a page no longer kept go when the house is matched again
    src = metaweb.Source(sess=None, token="t")
    keep = [{"id": "184360374941311", "name": "LOEWE"}]
    assert src.row_is_kept({"ad_id": "1", "page_id": "184360374941311"}, keep)
    assert not src.row_is_kept({"ad_id": "2", "page_id": "351058035420618"}, keep)
    assert src.row_is_kept({"ad_id": "3"}, keep)
    # the pages on file are judged by the rule as it stands, even before the vendor is asked again
    on_file = [{"id": "1", "name": "LOEWE", "ads": 76}, {"id": "2", "name": "LOEWE Perfumes", "ads": 60}, {"id": "3", "name": "Loewe Greece", "ads": 1}]
    assert [p["id"] for p in src.filter_keep(loewe, on_file)] == ["1"]


def test_an_advertiser_is_the_house_itself_not_a_person_or_a_shop_with_its_name(tmp_data):
    from adtone.registry import House
    from adtone.snap import division, is_own
    H = lambda i, n: House(i, n, "treated", "x", [n], [], [])
    assert is_own(H("valentino", "Valentino"), "Valentino") and not is_own(H("valentino", "Valentino"), "𝑉𝑎𝑙𝑒𝑛𝑡𝑖𝑛𝑜 𝑅𝑎𝑘𝑖𝑐")
    assert not is_own(H("celine", "Celine"), "Celine Anders") and not is_own(H("celine", "Celine"), "Celine ✌🏼")
    assert not is_own(H("chloe", "Chloé"), "Chloe Kate") and not is_own(H("chloe", "Chloé"), "Maison Chloe")
    assert is_own(H("gucci", "Gucci"), "Guccio Gucci S.p.A.") and is_own(H("prada", "Prada"), "Prada spa")
    assert is_own(H("loewe", "Loewe"), "Loewe Greece", ads=5) and not is_own(H("loewe", "Loewe"), "Loewe Greece", ads=1)
    assert not is_own(H("loewe", "Loewe"), "LOEWE Perfumes", ads=60)
    assert division("PCD_PC_FRA_EMEA_SOCIAL-SNAPCHAT", "Dior") == "beauty" and division("CDC_FLG_HUB-FRA", "Dior") == "fashion"
    assert division("Prada Spa Eyewear", "Prada spa") == "eyewear" and division("Parfums Christian Dior") == "beauty"
    # Snap: the phone app's self-serve accounts are people; Prada's Miu Miu accounts are Miu Miu's
    assert not snap.Source.own(H("gucci", "Gucci"), "Gucci.Interpol", "Promotions iOS in-app")
    assert snap.Source.own(H("miu_miu", "Miu Miu"), "Prada spa", "Miu Miu Spa") and not snap.Source.own(H("prada", "Prada"), "Prada spa", "Miu Miu Spa")
    assert snap.Source.own(H("prada", "Prada"), "Prada spa", "Prada spa Self Service")


def test_the_runway_looks_get_the_same_fingerprint_as_every_other_picture(tmp_data, monkeypatch):
    import numpy as np
    from adtone import clothes
    from adtone.synth import jpeg_bytes, toned_image
    d = tmp_data / "clothes"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(clothes, "DIR", d)
    monkeypatch.setattr(clothes, "VECTORS", d / "vectors")
    monkeypatch.setattr(clothes, "PROV", d / "prov.jsonl")
    store.write_jsonl(d / "runway.jsonl", [{"sha": f"s{i}", "house": "gucci"} for i in range(5)])
    copies = lambda shas, vol: {s: jpeg_bytes(toned_image(0.2 + 0.1 * int(s[1:]), seed=int(s[1:]))) for s in shas if s != "s4"}
    fe = FakeEmbedder()
    out = clothes.fingerprint(embedder=fe, read_copy=copies, batch=2)
    assert out["done"] == 4 and out["missing_on_volume"] == 1 and out["left"] == 0
    with np.load(d / "vectors" / f"{fe.tag}.npz", allow_pickle=False) as z:
        assert sorted(str(s) for s in z["shas"]) == ["s0", "s1", "s2", "s3"] and z["vecs"].shape[0] == 4
    assert clothes.fingerprint(embedder=fe, read_copy=copies, batch=2)["done"] == 0      # resumed: nothing made again


class InvalidError(Exception):
    """Modal's name for a call the service refuses (gRPC INVALID_ARGUMENT)."""


def _fake_remote(bad: bytes | None = None, down: bool = False, calls: list | None = None):
    """The reader's remote call: a valid reply per picture, refusing any batch holding `bad`, or every call."""
    from PIL import Image
    import io
    rub = load_rubric()
    fake = FakeScorer(rub)

    def remote(jpegs, prompt, schema, grammar):
        if calls is not None:
            calls.append(len(jpegs))
        if down or (bad is not None and bad in jpegs):
            raise InvalidError("the service refused the call")
        return [json.dumps(fake.score_image(Image.open(io.BytesIO(j)))) for j in jpegs]
    return remote


def test_a_picture_the_service_refuses_costs_only_itself():
    from adtone.score import ModalScorer, ScoreError
    good = [jpeg_bytes(toned_image(0.2 + 0.1 * i, seed=i)) for i in range(4)]
    bad = jpeg_bytes(toned_image(0.9, seed=99))
    calls = []
    sc = ModalScorer(load_rubric(), remote=_fake_remote(bad=bad, calls=calls), revision="abc123")
    got = sc.score_batches([[good[0], good[1], bad, good[2]], [good[3]]])
    first, second = got
    assert [isinstance(a, dict) for a in first] == [True, True, False, True] and isinstance(second[0], dict)
    assert isinstance(first[2], ScoreError) and str(first[2]).startswith("refused by the reader")
    assert len(calls) <= 8                     # the batch halved, not read picture by picture
    # a service that refuses everything: every answer is its error, to be read later
    down = ModalScorer(load_rubric(), remote=_fake_remote(down=True), revision="abc123")
    got = down.score_batches([good[:3]])[0]
    assert all(isinstance(a, ScoreError) and str(a).startswith("API:") for a in got)


def test_a_failing_reader_leaves_the_pictures_for_the_read_stage_and_the_run_goes_on(tmp_data):
    from adtone.score import ModalScorer
    sess = _snap_session([])
    src = snap.Source(sess, sleep=lambda s: None, pace_s=0)
    down = ModalScorer(load_rubric(), remote=_fake_remote(down=True), revision="abc123")
    out = adlibs.collect(src, Registry(1, "T", [GUCCI]), "r1", FakeEmbedder(), down, today=date(2026, 10, 17))
    assert out["new_ads"] == 3 and out["problems"] == {} and out["read_ok"] == 0 and out["unread_tone-v1"] >= 2
    assert "left unread" in out["stopped"] and "refused the call" in out["stopped"]
    assert not store.read_jsonl(adlibs.paths("snap")["obs"] / "tone-v1.jsonl")      # nothing recorded as read
    rows = {r["ad_id"]: r for r in store.read_jsonl(adlibs.paths("snap")["ads"] / "gucci.jsonl")}
    pend = {p["ad_id"] for p in store.read_jsonl(adlibs.paths("snap")["pending"] / "gucci.jsonl")}
    assert pend and all(rows[a]["media_pending"] for a in pend)
    # the read stage, with the reader back, reads them
    out = adlibs.read_pending("snap", Registry(1, "T", [GUCCI]), "r2", FakeEmbedder(), FakeScorer(load_rubric()),
                              sess=sess, today=date(2026, 10, 17))
    assert out["ads_read"] == len(pend) and out["ads_left"] == 0 and out["read_ok"] >= 2 and out["stopped"] is None
    rows = {r["ad_id"]: r for r in store.read_jsonl(adlibs.paths("snap")["ads"] / "gucci.jsonl")}
    assert not any(r.get("media_pending") for r in rows.values())


def test_the_read_stage_keeps_an_ad_pending_when_the_service_fails_on_it(tmp_data):
    from adtone.score import ModalScorer
    sess = _snap_session([])
    src = snap.Source(sess, sleep=lambda s: None, pace_s=0)
    adlibs.collect(src, Registry(1, "T", [GUCCI]), "r1", today=date(2026, 10, 17))           # deferred: no reader
    before = {p["ad_id"] for p in store.read_jsonl(adlibs.paths("snap")["pending"] / "gucci.jsonl")}
    down = ModalScorer(load_rubric(), remote=_fake_remote(down=True), revision="abc123")
    out = adlibs.read_pending("snap", Registry(1, "T", [GUCCI]), "r2", FakeEmbedder(), down, sess=sess, today=date(2026, 10, 17))
    after = {p["ad_id"] for p in store.read_jsonl(adlibs.paths("snap")["pending"] / "gucci.jsonl")}
    # the ads whose pictures came are kept pending; snap-2's video is not a video and it has no picture to read
    assert {"snap-0", "snap-3"} <= after <= before and out["read_ok"] == 0 and "left unread" in out["stopped"]


class _FakeMap:
    """Modal's method handle: starmap refuses on the main thread, as it does where Playwright keeps an event loop
    running there; or refuses everywhere."""

    def __init__(self, remote, refuse_everywhere=False):
        self.remote, self.refuse_everywhere, self.threads = remote, refuse_everywhere, []

    def starmap(self, args, return_exceptions=False):
        import threading
        self.threads.append(threading.current_thread() is threading.main_thread())
        if self.refuse_everywhere or threading.current_thread() is threading.main_thread():
            raise InvalidError("You can't `iter(Function.starmap())` from an async function.")
        out = []
        for a in args:
            try:
                out.append(self.remote(*a))
            except Exception as e:
                if not return_exceptions:
                    raise
                out.append(e)
        return out


def test_the_batches_are_read_off_the_main_thread_and_a_failing_map_falls_back_to_single_calls():
    from adtone.score import ModalScorer
    good = [jpeg_bytes(toned_image(0.2 + 0.1 * i, seed=i)) for i in range(5)]
    remote = _fake_remote()
    sc = ModalScorer(load_rubric(), remote=remote, revision="abc123")
    sc.fn = _FakeMap(remote)
    got = sc.score_batches([good[:2], good[2:4], good[4:]])
    assert all(isinstance(a, dict) for b in got for a in b) and sc.fn.threads == [False]   # the map ran off the main thread
    sc.fn = _FakeMap(remote, refuse_everywhere=True)
    got = sc.score_batches([good[:2], good[2:4], good[4:]])
    assert all(isinstance(a, dict) for b in got for a in b)                                # read with single calls instead


PRADA = House("prada", "Prada", "treated", "Prada Group", ["Prada"], [], [])


def _waiting(house_id: str, ads: list[tuple[str, list[str]]], fmt: str = "IMAGE") -> None:
    """Rows and waiting addresses on file for a house, as a run without the reader leaves them."""
    P = adlibs.paths("snap")
    store.write_jsonl(P["ads"] / f"{house_id}.jsonl", [{"ad_id": a, "house_id": house_id, "first_shown": "2026-09-01", "media": [],
                                                        "media_pending": True, "display_format": fmt} for a, _ in ads])
    store.write_jsonl(P["pending"] / f"{house_id}.jsonl", [{"ad_id": a, "house_id": house_id, "urls": urls, "kw": {},
                                                            "first_shown": "2026-09-01", "found": "2026-10-17"} for a, urls in ads])


def _cdn(url, params):
    """Pictures at addresses that may carry a query, as Meta's do."""
    base = url.split("?")[0]
    if base.endswith(".jpg"):
        n = int(base.rsplit("/", 1)[-1].split(".")[0])
        return FakeResponse(200, content=jpeg_bytes(toned_image(0.3 + 0.04 * n, seed=300 + n)))
    return FakeResponse(404)


def test_the_read_stage_shares_its_budget_between_the_houses(tmp_data):
    _waiting("gucci", [(f"g{i}", [f"https://cdn.example/{i}.jpg"]) for i in range(8)])
    _waiting("prada", [(f"p{i}", [f"https://cdn.example/{10 + i}.jpg"]) for i in range(8)])
    ticks = iter(range(0, 10_000, 10))
    out = adlibs.read_pending("snap", Registry(1, "T", [GUCCI, PRADA]), "r1", FakeEmbedder(), FakeScorer(load_rubric()),
                              budget_s=100, sess=FakeSession(_cdn), today=date(2026, 10, 17), clock=lambda: next(ticks),
                              chunk_ads=1, fetch_workers=1)
    left = {h: len(store.read_jsonl(adlibs.paths("snap")["pending"] / f"{h}.jsonl")) for h in ("gucci", "prada")}
    # the first house reads for half the time, the second for what is left: both are read, neither to the end
    assert out["houses"] == 2 and out["stopped"] == "budget" and 0 < left["gucci"] < 8 and 0 < left["prada"] < 8
    assert out["ads_read"] == 16 - left["gucci"] - left["prada"] and out["ads_left"] == left["gucci"] + left["prada"]
    # a house finishing early leaves its time to the others, round after round, until nothing waits
    out = adlibs.read_pending("snap", Registry(1, "T", [GUCCI, PRADA]), "r2", FakeEmbedder(), FakeScorer(load_rubric()),
                              sess=FakeSession(_cdn), today=date(2026, 10, 17), chunk_ads=1, fetch_workers=1)
    assert out["ads_left"] == 0 and out["stopped"] is None and out["ads_read"] == left["gucci"] + left["prada"]


def test_addresses_that_lapse_first_are_read_first_and_lapsed_ones_wait_for_new_ones(tmp_data):
    now = 1_790_000_000
    oe = lambda t: f"?_nc_ht=x&oe={t:X}"                                    # Meta's addresses carry their lapse in hex
    _waiting("gucci", [("late", [f"https://cdn.example/1.jpg{oe(now + 4 * 86400)}"]),
                       ("soon", [f"https://cdn.example/2.jpg{oe(now + 3 * 3600)}"]),
                       ("gone", [f"https://cdn.example/3.jpg{oe(now - 60)}"]),
                       ("plain", ["https://cdn.example/4.jpg"])])
    assert adlibs.lapses({"urls": [f"https://x/1.jpg{oe(now)}", f"https://x/2.mp4{oe(now - 5)}"]}) == now - 5
    assert adlibs.lapses({"urls": ["https://x/1.jpg"]}) == adlibs.NO_LAPSE
    clock = iter([0, 0, 0, 9999, 9999, 9999])
    out = adlibs.read_pending("snap", Registry(1, "T", [GUCCI]), "r1", FakeEmbedder(), FakeScorer(load_rubric()), budget_s=100,
                              sess=FakeSession(_cdn), today=date(2026, 10, 17), clock=lambda: next(clock), chunk_ads=1,
                              fetch_workers=1, wall=lambda: now)
    rows = {r["ad_id"]: r for r in store.read_jsonl(adlibs.paths("snap")["ads"] / "gucci.jsonl")}
    assert out["ads_read"] == 1 and rows["soon"]["media"] and "media_pending" not in rows["soon"]
    assert rows["gone"]["media_pending"] and rows["gone"]["media_lapsed"] and out["lapsed"] == 1
    assert not rows["gone"]["media"] and "media_error" not in rows["gone"]                   # not fetched at all
    out = adlibs.read_pending("snap", Registry(1, "T", [GUCCI]), "r2", FakeEmbedder(), FakeScorer(load_rubric()),
                              sess=FakeSession(_cdn), today=date(2026, 10, 17), chunk_ads=1, fetch_workers=1, wall=lambda: now)
    assert out["ads_read"] == 2 and out["ads_left"] == 1 and out["lapsed"] == 1 and out["stopped"] is None
    assert [p["ad_id"] for p in store.read_jsonl(adlibs.paths("snap")["pending"] / "gucci.jsonl")] == ["gone"]
    # the order within a house is fixed by the ad's id, not its date
    a = sorted([("x1", {"urls": []}), ("x2", {"urls": []}), ("x3", {"urls": []})], key=adlibs.read_order)
    assert [k for k, _ in a] == [k for k, _ in sorted(a, key=adlibs.read_order)]


def test_a_new_collection_renews_the_addresses_of_pictures_still_waiting(tmp_data):
    sess = _snap_session([])
    src = snap.Source(sess, sleep=lambda s: None, pace_s=0)
    adlibs.collect(src, Registry(1, "T", [GUCCI]), "r1", today=date(2026, 10, 17))                # deferred: no reader
    P = adlibs.paths("snap")
    pend = store.read_jsonl(P["pending"] / "gucci.jsonl")
    fresh = {p["ad_id"]: p["urls"] for p in pend}
    store.write_jsonl(P["pending"] / "gucci.jsonl", [{**p, "urls": ["https://old.example/1.jpg?oe=1"]} for p in pend])
    rows = store.read_jsonl(P["ads"] / "gucci.jsonl")
    store.write_jsonl(P["ads"] / "gucci.jsonl", [{**r, "media_lapsed": True} for r in rows])
    src = snap.Source(sess, sleep=lambda s: None, pace_s=0)
    src.house_refresh_days = 0
    out = adlibs.collect(src, Registry(1, "T", [GUCCI]), "r2", today=date(2026, 10, 18))
    assert out["addresses_renewed"] == len(pend) and out["new_ads"] == 0
    assert {p["ad_id"]: p["urls"] for p in store.read_jsonl(P["pending"] / "gucci.jsonl")} == fresh
    assert not any(r.get("media_lapsed") for r in store.read_jsonl(P["ads"] / "gucci.jsonl"))


def test_a_house_never_searched_to_the_end_is_searched_for_its_whole_year(tmp_data, monkeypatch):
    from adtone import metaweb
    monkeypatch.setenv("META_PERIOD", "last30d")
    cc = metaweb.Source(_apify_session({}), token="t", actor="curious_coder~facebook-ads-library-scraper", countries="FR", sleep=lambda s: None)
    assert cc._input(page_ids=["1"])["scrapePageAds"]["period"] == "last30d"
    assert cc._input(terms=["Gucci"])["scrapePageAds"]["period"] == ""          # the page lookup by name: the whole library
    cc.first_search = True
    assert cc._input(page_ids=["1"])["scrapePageAds"]["period"] == ""
    # the loop says which: true for a house with no searched marker, false once it has one
    seen = []
    src = snap.Source(_snap_session([]), sleep=lambda s: None, pace_s=0)
    src.first_search = None
    real = src.search_ads

    def spy(h, keep, start, end):
        seen.append(src.first_search)
        yield from real(h, keep, start, end)
    src.search_ads = spy
    adlibs.collect(src, Registry(1, "T", [GUCCI]), "r1", today=date(2026, 10, 17))
    src.house_refresh_days = 0
    adlibs.collect(src, Registry(1, "T", [GUCCI]), "r2", today=date(2026, 10, 18))
    assert seen == [True, False]


def test_pictures_fetched_but_never_read_are_kept_aside_by_the_next_collection(tmp_data):
    from adtone.score import ModalScorer
    sess = _snap_session([])
    P = adlibs.paths("snap")
    adlibs.collect(snap.Source(sess, sleep=lambda s: None, pace_s=0), Registry(1, "T", [GUCCI]), "r1", FakeEmbedder(),
                   FakeScorer(load_rubric()), today=date(2026, 10, 17))
    with_pictures = {r["ad_id"] for r in store.read_jsonl(P["ads"] / "gucci.jsonl") if r.get("media")}
    assert with_pictures
    (P["obs"] / f"{load_rubric().version}.jsonl").unlink()            # as if the reader had answered nothing
    # a run with the reader that fails again: the ads go to the read stage instead of waiting for next week
    src = snap.Source(sess, sleep=lambda s: None, pace_s=0)
    src.house_refresh_days = 0
    down = ModalScorer(load_rubric(), remote=_fake_remote(down=True), revision="abc123")
    out = adlibs.collect(src, Registry(1, "T", [GUCCI]), "r2", FakeEmbedder(), down, today=date(2026, 10, 18))
    assert out["reread"] == len(with_pictures)
    assert {p["ad_id"] for p in store.read_jsonl(P["pending"] / "gucci.jsonl")} == with_pictures
    rows = {r["ad_id"]: r for r in store.read_jsonl(P["ads"] / "gucci.jsonl")}
    assert all(rows[a]["media_pending"] and rows[a]["media"] for a in with_pictures)    # the pictures as first recorded stay
    # a run without the reader does the same from the readings on file
    store.write_jsonl(P["pending"] / "gucci.jsonl", [])
    store.write_jsonl(P["ads"] / "gucci.jsonl", [{k: v for k, v in r.items() if k != "media_pending"} for r in rows.values()])
    src = snap.Source(sess, sleep=lambda s: None, pace_s=0)
    src.house_refresh_days = 0
    out = adlibs.collect(src, Registry(1, "T", [GUCCI]), "r3", today=date(2026, 10, 19))
    assert out["never_read_kept"] == len(with_pictures) and out.get("addresses_renewed", 0) == 0
    assert {p["ad_id"] for p in store.read_jsonl(P["pending"] / "gucci.jsonl")} == with_pictures
    # and the read stage reads them
    out = adlibs.read_pending("snap", Registry(1, "T", [GUCCI]), "r4", FakeEmbedder(), FakeScorer(load_rubric()),
                              sess=sess, today=date(2026, 10, 19))
    assert out["ads_read"] == len(with_pictures) and out["ads_left"] == 0 and out["read_ok"] >= 1
    rows = store.read_jsonl(P["ads"] / "gucci.jsonl")
    assert not any(r.get("media_pending") for r in rows)
    # with every picture read, a further run without the reader keeps nothing aside
    src = snap.Source(sess, sleep=lambda s: None, pace_s=0)
    src.house_refresh_days = 0
    out = adlibs.collect(src, Registry(1, "T", [GUCCI]), "r5", today=date(2026, 10, 20))
    assert out.get("never_read_kept", 0) == 0 and not store.read_jsonl(P["pending"] / "gucci.jsonl")


def test_tidy_applies_the_rules_as_they_stand_to_the_rows_on_file(tmp_data):
    from adtone import metaweb
    alaia = House("alaia", "Alaïa", "treated", "Richemont", ["Alaïa", "Alaia"], [], [])
    versace = House("versace", "Versace", "treated", "Capri", ["Versace"], [], [])
    P = adlibs.paths("metaweb")
    row = lambda i, pid, name, payer="": {"ad_id": f"{pid}-{i}", "house_id": "x", "page_id": pid, "page_name": name, "media": [],
                                          "beneficiary_payers": [{"payer": payer, "beneficiary": payer}] if payer else None}
    store.write_jsonl(P["ads"] / "alaia.jsonl", [row(1, "1", "Alaïa Bay"), row(2, "1", "Alaïa Bay"), row(3, "2", "Alaia"),
                                                 row(4, "3", "Alaia Muse")])
    store.write_jsonl(P["pending"] / "alaia.jsonl", [{"ad_id": "1-1", "urls": ["https://x/1.jpg"]}, {"ad_id": "2-3", "urls": ["https://x/2.jpg"]}])
    store.write_jsonl(P["ads"] / "versace.jsonl", [row(1, "9", "Versace", "Gianni Versace S.r.l."), row(2, "9", "Versace", "Luxottica Group S.p.A."),
                                                   row(3, "9", "Versace", "EuroItalia S.r.l."), row(4, "8", "Hermes Logistik")])
    # Versace's lookup is on file and keeps its page; Alaïa's is missing, so its rows are judged by their pages' names
    store.write_state(P["state"], {"advertisers": {"versace": {"checked": "2026-10-17", "keep": [{"id": "9", "name": "Versace", "ads": 115}]}}})
    out = adlibs.tidy(object.__new__(metaweb.Source), Registry(1, "T", [alaia, versace]), "t1")
    rows = {h: {r["ad_id"]: r for r in store.read_jsonl(P["ads"] / f"{h}.jsonl")} for h in ("alaia", "versace")}
    assert set(rows["alaia"]) == {"2-3"}                                 # the surf park and the shop are not the house
    assert [p["ad_id"] for p in store.read_jsonl(P["pending"] / "alaia.jsonl")] == ["2-3"]
    assert {k: r["division"] for k, r in rows["versace"].items()} == {"9-1": "fashion", "9-2": "eyewear", "9-3": "beauty"}
    assert out["dropped"] == 4 and out["pending_dropped"] == 1 and out["division_changed"] == 4
    # run again, nothing changes
    out = adlibs.tidy(object.__new__(metaweb.Source), Registry(1, "T", [alaia, versace]), "t2")
    assert out["dropped"] == 0 and out["division_changed"] == 0


def test_a_page_with_the_house_name_that_is_another_company_is_not_the_house(tmp_data):
    from adtone import metaweb
    loewe = House("loewe", "Loewe", "treated", "LVMH", ["Loewe"], [], [])
    assert metaweb.is_page(loewe, {"id": "184360374941311", "name": "LOEWE", "ads": 76})
    assert not metaweb.is_page(loewe, {"id": "106889830893927", "name": "Loewe", "ads": 3})     # the television maker
    P = adlibs.paths("metaweb")
    store.write_jsonl(P["ads"] / "loewe.jsonl", [{"ad_id": "a", "page_id": "184360374941311", "page_name": "LOEWE", "media": []},
                                                 {"ad_id": "b", "page_id": "106889830893927", "page_name": "Loewe", "media": []}])
    store.write_state(P["state"], {"advertisers": {"loewe": {"keep": [{"id": "184360374941311", "name": "LOEWE", "ads": 76},
                                                                      {"id": "106889830893927", "name": "Loewe", "ads": 3}]}}})
    adlibs.tidy(object.__new__(metaweb.Source), Registry(1, "T", [loewe]), "t1")
    assert [r["ad_id"] for r in store.read_jsonl(P["ads"] / "loewe.jsonl")] == ["a"]


def test_tidy_keeps_a_page_refused_before_that_is_the_house_and_has_the_house_searched_again(tmp_data):
    from adtone import metaweb
    alaia = House("alaia", "Alaïa", "treated", "Richemont", ["Alaïa", "Alaia"], [], [])
    P = adlibs.paths("metaweb")
    store.write_jsonl(P["ads"] / "alaia.jsonl", [{"ad_id": "b1", "page_id": "1", "page_name": "Alaïa Bay", "media": []}])
    store.write_state(P["state"], {"searched": {"alaia": "2026-10-17"}, "advertisers": {"alaia": {
        "keep": [{"id": "1", "name": "Alaïa Bay", "ads": 17, "categories": ["UNKNOWN", "Surfing Spot"]}],
        "refused": [{"id": "2", "name": "Maison Alaïa", "ads": 132, "categories": ["UNKNOWN", "Clothing (brand)"]},
                    {"id": "3", "name": "Villa Alaia", "ads": 5, "categories": ["UNKNOWN", "Vacation Home Rental"]}]}}})
    out = adlibs.tidy(object.__new__(metaweb.Source), Registry(1, "T", [alaia]), "t1")
    st = store.read_state(P["state"])
    assert [p["name"] for p in st["advertisers"]["alaia"]["keep"]] == ["Maison Alaïa"]
    assert {p["name"] for p in st["advertisers"]["alaia"]["refused"]} == {"Alaïa Bay", "Villa Alaia"}
    assert "alaia" not in st["searched"] and out["pages_revived"] == 1 and out["dropped"] == 1
    assert not store.read_jsonl(P["ads"] / "alaia.jsonl")


def test_the_read_stage_can_leave_other_divisions_for_later(tmp_data):
    _waiting("gucci", [("f1", ["https://cdn.example/1.jpg"]), ("b1", ["https://cdn.example/2.jpg"])])
    P = adlibs.paths("snap")
    rows = store.read_jsonl(P["ads"] / "gucci.jsonl")
    store.write_jsonl(P["ads"] / "gucci.jsonl", [{**r, "division": "beauty" if r["ad_id"] == "b1" else "fashion"} for r in rows])
    out = adlibs.read_pending("snap", Registry(1, "T", [GUCCI]), "r1", FakeEmbedder(), FakeScorer(load_rubric()),
                              sess=FakeSession(_cdn), today=date(2026, 10, 17), fashion_only=True, fetch_workers=1)
    assert out["ads_read"] == 1 and out["dynamic_left"] == 1
    assert [p["ad_id"] for p in store.read_jsonl(P["pending"] / "gucci.jsonl")] == ["b1"]
