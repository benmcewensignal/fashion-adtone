"""YouTube films by house: channels checked, films found and refreshed, thumbnails read. Network faked."""
from datetime import date

from PIL import Image

from adtone import store, youtube
from adtone.embed import FakeEmbedder, VectorStore
from adtone.registry import House, Registry
from adtone.score import FakeScorer, load_rubric
from adtone.synth import FakeResponse, FakeSession, jpeg_bytes, toned_image

GUCCI = House("gucci", "Gucci", "treated", "Kering", ["Gucci"], [], [])
LOEWE = House("loewe", "Loewe", "treated", "LVMH", ["LOEWE"], [], [])


def _reg(*h):
    return Registry(1, "T", list(h))


def _films(prefix, n, start=date(2026, 9, 30)):
    from datetime import timedelta
    return [(f"{prefix}{i:03d}", (start - timedelta(days=3 * i)).isoformat()) for i in range(n)]


def _api(films_by_playlist, titles, quota_after=None):
    calls = {"n": 0}

    def h(url, params):
        calls["n"] += 1
        if quota_after is not None and calls["n"] > quota_after and "googleapis" in url:
            return FakeResponse(403, payload={"error": {"errors": [{"reason": "quotaExceeded"}]}})
        if url.endswith("/channels"):
            items = [{"id": cid, "snippet": {"title": titles[cid]},
                      "contentDetails": {"relatedPlaylists": {"uploads": "UU" + cid[2:]}},
                      "statistics": {"subscriberCount": "1000", "viewCount": "50000", "videoCount": "7"}}
                     for cid in params["id"].split(",") if cid in titles]
            return FakeResponse(200, payload={"items": items})
        if url.endswith("/playlistItems"):
            films = films_by_playlist[params["playlistId"]]
            start = int(params.get("pageToken") or 0)
            page = films[start:start + 50]
            body = {"items": [{"contentDetails": {"videoId": v}} for v, _ in page]}
            if start + 50 < len(films):
                body["nextPageToken"] = str(start + 50)
            return FakeResponse(200, payload=body)
        if url.endswith("/videos"):
            pub = {v: p for films in films_by_playlist.values() for v, p in films}
            items = [{"id": v, "snippet": {"publishedAt": pub[v] + "T10:00:00Z", "title": f"Film {v}",
                                           "description": "Directed by Someone. Starring A and B.",
                                           "thumbnails": {"high": {"url": f"https://i.ytimg.com/vi/{v}/hqdefault.jpg"},
                                                          "maxres": {"url": f"https://i.ytimg.com/vi/{v}/maxresdefault.jpg"}}},
                      "contentDetails": {"duration": "PT1M30S"},
                      "statistics": {"viewCount": "1234", "likeCount": "56"}} for v in params["id"].split(",")]
            return FakeResponse(200, payload={"items": items})
        if url.startswith("https://i.ytimg.com/vi/"):
            vid = url.split("/")[4]
            return FakeResponse(200, content=jpeg_bytes(toned_image(0.3 + 0.01 * (hash(vid) % 40), seed=hash(vid) % 1000)))
        return FakeResponse(404, text="")
    return h, calls


def _setup(tmp_data, monkeypatch, tmp_path):
    f = tmp_path / "yt.csv"
    f.write_text("house,handle,channel_url,channel_id,evidence_url,notes\n"
                 "gucci,@gucci,u,UCgucci,e,\nloewe,@Loewe,u,UCloewe,e,\n", encoding="utf-8")
    monkeypatch.setattr(youtube, "CHANNELS_FILE", f)


def test_films_are_found_refreshed_and_their_thumbnails_read(tmp_data, monkeypatch, tmp_path):
    _setup(tmp_data, monkeypatch, tmp_path)
    films = {"UUgucci": _films("g", 120), "UUloewe": _films("l", 3)}
    h, calls = _api(films, {"UCgucci": "GUCCI", "UCloewe": "Some Other Brand"})
    sess = FakeSession(h)
    scorer = FakeScorer(load_rubric())
    out = youtube.collect(sess, _reg(GUCCI, LOEWE), "r1", "KEY", FakeEmbedder(), scorer, read_thumbs=20,
                          today=date(2026, 10, 7), sleep=lambda s: None)
    assert out["channels"] == 2 and out["new_films"] == 123 and out["thumbs_read"] == 20
    assert "loewe" in out["problems"] and "does not name the house" in out["problems"]["loewe"]
    P = youtube.paths()
    g = store.read_jsonl(P["videos"] / "gucci.jsonl")
    assert len(g) == 120 and g[0]["published"] == "2026-09-30" and g[0]["duration_s"] == 90
    assert g[0]["thumb"].endswith("maxresdefault.jpg") and "Directed by" in g[0]["description"]
    young = store.read_jsonl(P["stats"])
    assert all((date(2026, 10, 7) - date.fromisoformat(next(r for r in g + store.read_jsonl(P["videos"] / "loewe.jsonl")
                                                              if r["video_id"] == s["video_id"])["published"])).days <= 120
               for s in young)
    obs = store.read_jsonl(P["obs"] / "tone-v1.jsonl")
    assert len(obs) == 20 and obs[0]["published"] == "2026-09-30" and len(VectorStore("fake", root=P["vectors"]).vecs) == 20
    assert out["units"] == 1 + 3 + 3 + 1 + 1    # channels, gucci pages and stats, loewe page and stats
    assert youtube.probe("r1") == ["loewe: channel title 'Some Other Brand' does not name the house"]
    # a week on: one new film; the refresh stops at the first page of known films
    films["UUgucci"] = [("gnew", "2026-10-06")] + films["UUgucci"]
    before = len([c for c in sess.calls if c[0].endswith("/playlistItems")])
    out2 = youtube.collect(sess, _reg(GUCCI, LOEWE), "r2", "KEY", FakeEmbedder(), scorer, read_thumbs=5,
                           today=date(2026, 10, 14), sleep=lambda s: None)
    pages = len([c for c in sess.calls if c[0].endswith("/playlistItems")]) - before
    assert out2["new_films"] == 1 and out2["films_refreshed"] == 123
    assert pages == 3    # gucci: the page with the new film, then one page of known films; loewe: its one page
    assert out2["thumbs_read"] == 5 and store.read_jsonl(P["obs"] / "tone-v1.jsonl")[20]["video_id"] == "gnew"


def test_a_spent_quota_stops_the_run_and_keeps_what_it_has(tmp_data, monkeypatch, tmp_path):
    _setup(tmp_data, monkeypatch, tmp_path)
    h, _ = _api({"UUgucci": _films("g", 60), "UUloewe": _films("l", 3)}, {"UCgucci": "Gucci", "UCloewe": "LOEWE"},
                quota_after=4)
    out = youtube.collect(FakeSession(h), _reg(GUCCI, LOEWE), "r1", "KEY", today=date(2026, 10, 7), sleep=lambda s: None)
    assert out["stopped"].startswith("quota") and out["new_films"] == 50     # the first batch of fifty came back
    assert len(store.read_jsonl(youtube.paths()["videos"] / "gucci.jsonl")) == 50
    assert not store.read_state(youtube.paths()["state"])["houses"]["gucci"].get("complete")   # read in full next run


def test_durations_titles_and_letterboxes():
    assert youtube.seconds("PT1H2M3S") == 3723 and youtube.seconds("PT45S") == 45 and youtube.seconds(None) is None
    assert youtube.title_matches(House("dolce_gabbana", "Dolce&Gabbana", "c", "D", ["Dolce & Gabbana"], [], []),
                                 "Dolce&Gabbana")
    assert youtube.title_matches(House("dior", "Dior", "t", "LVMH", ["Dior"], [], []), "Christian Dior")
    assert youtube.title_matches(House("alaia", "Alaïa", "w", "Richemont", ["ALAÏA"], [], []), "Maison Alaïa")
    assert not youtube.title_matches(House("loewe", "Loewe", "t", "LVMH", ["LOEWE"], [], []), "Some Other Brand")
    img = Image.new("RGB", (480, 360), (0, 0, 0))
    img.paste(Image.new("RGB", (480, 270), (200, 120, 90)), (0, 45))
    out = youtube.unletterbox(img)
    assert out.size == (480, 270)
    assert youtube.unletterbox(Image.new("RGB", (480, 360), (200, 200, 200))).size == (480, 360)
