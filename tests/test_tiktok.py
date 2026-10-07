"""TikTok's ad library: the house's own advertisers found, its ads searched and kept, pictures read.
Network faked; the API's shapes follow its documentation as read on 7 October 2026."""
import io
from datetime import date

import pytest

from adtone import store, tiktok
from adtone.embed import FakeEmbedder, VectorStore
from adtone.registry import House, Registry
from adtone.score import FakeScorer, load_rubric
from adtone.synth import FakeResponse, FakeSession, jpeg_bytes, toned_image

GUCCI = House("gucci", "Gucci", "treated", "Kering", ["Gucci"], [], [])
SAINT_LAURENT = House("saint_laurent", "Saint Laurent", "treated", "Kering", ["Saint Laurent", "YSL"], [], [])


def test_the_house_is_its_own_advertiser_and_licensees_are_not():
    assert tiktok.is_house(GUCCI, "Guccio Gucci S.p.A.")
    assert not tiktok.is_house(GUCCI, "Gucci Beauty (Coty)")
    assert not tiktok.is_house(GUCCI, "Gucci Vintage Resale Ltd")
    assert tiktok.is_house(SAINT_LAURENT, "Yves Saint Laurent SAS")
    assert not tiktok.is_house(SAINT_LAURENT, "YSL Beauté")
    assert not tiktok.is_house(GUCCI, "")


def _ad(i, bid, name, images=1):
    return {"ad": {"id": 7000 + i, "first_shown_date": 20260901 + i, "last_shown_date": 20260930, "status": "active",
                   "image_urls": [f"https://p16.tiktokcdn.example/img/{i}-{k}.jpg" for k in range(images)],
                   "videos": [], "reach": {"unique_users_seen": "100K-1M"}},
            "advertiser": {"business_id": bid, "business_name": name, "paid_by": name}}


def _tiktok(pages, calls, fail_country_once=False, limit_after=None):
    state = {"country_refused": not fail_country_once}

    def post(url, params, body, data):
        calls.append((url, body))
        if url == tiktok.TOKEN:
            assert data["grant_type"] == "client_credentials"
            return FakeResponse(200, payload={"access_token": "clt.abc", "expires_in": 7200, "token_type": "Bearer"})
        if limit_after is not None and len([c for c in calls if c[0] != tiktok.TOKEN]) > limit_after:
            return FakeResponse(429, payload={"error": {"code": "rate_limit_exceeded", "message": "limit"}})
        if url == tiktok.ADVERTISERS:
            return FakeResponse(200, payload={"data": {"advertisers": [
                {"business_id": 11, "business_name": "Guccio Gucci S.p.A.", "country_code": "IT"},
                {"business_id": 12, "business_name": "Gucci Beauty (Coty)", "country_code": "FR"}]},
                "error": {"code": "ok", "message": ""}})
        if url == tiktok.ADS:
            if not state["country_refused"] and "country_code" in body["filters"]:
                state["country_refused"] = True
                return FakeResponse(400, payload={"error": {"code": "invalid_params", "message": "Invalid field: country_code"}})
            page = int(body.get("search_id") or 0)
            ads, more = pages[page]
            return FakeResponse(200, payload={"data": {"ads": ads, "has_more": more, "search_id": str(page + 1)},
                                              "error": {"code": "ok"}})
        raise AssertionError(url)

    def get(url, params):
        if "tiktokcdn" in url:
            n, k = url.rsplit("/", 1)[-1].split(".")[0].split("-")
            return FakeResponse(200, content=jpeg_bytes(toned_image(0.2 + 0.05 * (int(n) % 10), seed=int(n) * 10 + int(k))))
        return FakeResponse(404)
    return get, post


def test_ads_are_searched_kept_for_the_house_and_their_pictures_read(tmp_data):
    pages = [([_ad(i, 11, "Guccio Gucci S.p.A.") for i in range(8)] + [_ad(8, 12, "Gucci Beauty (Coty)"),
                                                                      _ad(9, 99, "Some Reseller")], True),
             ([_ad(10, 11, "Guccio Gucci S.p.A.", images=2)], "false")]
    calls = []
    get, post = _tiktok(pages, calls, fail_country_once=True)
    sess = FakeSession(get, post)
    scorer = FakeScorer(load_rubric())
    out = tiktok.collect(sess, Registry(1, "T", [GUCCI]), "r1", "key", "secret", FakeEmbedder(), scorer,
                         today=date(2026, 10, 7), sleep=lambda s: None)
    assert out["new_ads"] == 9 and out["pictures"] == 10 and out["read_ok"] == 10 and out["problems"] == {}
    rows = store.read_jsonl(tiktok.paths()["ads"] / "gucci.jsonl")
    assert len(rows) == 9 and {r["business_id"] for r in rows} == {"11"}
    assert rows[0]["first_shown"] == "2026-09-11" and rows[0]["reach"] == "100K-1M" and len(rows[0]["media"]) == 2
    st = store.read_state(tiktok.paths()["state"])
    assert st["country_filter"] == "country_code_list" and st["advertisers"]["gucci"]["keep"][0]["business_id"] == "11"
    assert st["advertisers"]["gucci"]["refused"][0]["business_name"] == "Gucci Beauty (Coty)"
    ad_bodies = [b for u, b in calls if u == tiktok.ADS]
    assert ad_bodies[-1]["search_id"] == "1" and ad_bodies[-1]["filters"]["ad_published_date_range"]["min"] == "20251008"
    assert len(VectorStore("fake", root=tiktok.paths()["vectors"]).vecs) == 10
    assert tiktok.probe("r1") == []
    # the next week: the same ads are updated, not fetched again
    out2 = tiktok.collect(sess, Registry(1, "T", [GUCCI]), "r2", "key", "secret", FakeEmbedder(), scorer,
                          today=date(2026, 10, 14), sleep=lambda s: None)
    assert out2["new_ads"] == 0 and out2["updated_ads"] == 9 and out2["pictures"] == 0
    assert not [u for u, _ in calls[len(calls) - 3:] if u == tiktok.ADVERTISERS]   # advertisers are checked monthly


def test_the_daily_limit_stops_the_run_and_keeps_what_came_back(tmp_data):
    pages = [([_ad(i, 11, "Guccio Gucci S.p.A.") for i in range(10)], True), ([_ad(10, 11, "Guccio Gucci S.p.A.")], False)]
    calls = []
    get, post = _tiktok(pages, calls, limit_after=2)
    out = tiktok.collect(FakeSession(get, post), Registry(1, "T", [GUCCI]), "r1", "key", "secret",
                         today=date(2026, 10, 7), sleep=lambda s: None)
    assert out["stopped"].startswith("TikTok: daily request limit") and out["new_ads"] == 10
    assert len(store.read_jsonl(tiktok.paths()["ads"] / "gucci.jsonl")) == 10


def test_three_frames_are_taken_from_a_video_in_memory():
    av = pytest.importorskip("av")
    import numpy as np
    buf = io.BytesIO()
    with av.open(buf, mode="w", format="mp4") as box:
        st = box.add_stream("mpeg4", rate=10)
        st.width, st.height, st.pix_fmt = 64, 48, "yuv420p"
        for i in range(30):   # three seconds, getting brighter
            frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), i * 8, dtype=np.uint8), format="rgb24")
            for p in st.encode(frame):
                box.mux(p)
        for p in st.encode():
            box.mux(p)
    frames = tiktok.video_frames(buf.getvalue())
    assert len(frames) == 3
    levels = [np.asarray(f.convert("L")).mean() for f in frames]
    assert levels[0] < levels[1] < levels[2]
