"""Tests for the sources added while Meta access is pending: page views by language, Wikidata facts,
and homepage history from the Wayback Machine. Every network call is faked."""
import json
from datetime import date
from urllib.parse import unquote

from adtone import config, homepages, store, wikidata, wikiviews
from adtone.embed import FakeEmbedder, VectorStore
from adtone.registry import Event, House, Registry
from adtone.score import FakeScorer, load_rubric
from adtone.synth import FakeResponse, FakeSession, jpeg_bytes, toned_image


def _reg(*houses):
    return Registry(1, "DRAFT", list(houses))


GUCCI = House("gucci", "Gucci", "treated", "Kering", ["Gucci"], [], [Event("designer_debut", "Demna", date(2025, 9, 23), True)])
LOEWE = House("loewe", "Loewe", "treated", "LVMH", ["LOEWE"], [],
              [Event("designer_debut", "Jack McCollough and Lazaro Hernandez", date(2025, 10, 3), True)])


# ---------- page views by language ----------

def test_counted_titles_keep_former_names_and_drop_the_long_tail():
    own = {"2024-01": 9000, "2024-02": 11000}
    reds = {"Old Name": {"2016-01": 5000, "2016-02": 4000}, "Typo": {"2020-01": 3}, "Empty": {}}
    assert wikiviews.counted_titles(own, reds) == ["Old Name"]


def test_rescans_are_due_monthly():
    assert wikiviews._due(None, date(2026, 10, 7))
    assert not wikiviews._due("2026-10-01T00:00:00Z", date(2026, 10, 7))
    assert wikiviews._due("2026-09-01T00:00:00Z", date(2026, 10, 7))


def _wiki_handler(calls):
    def h(url, params):
        calls.append(url)
        if url == wikiviews.API.format(lang="en") and params.get("prop") == "pageprops":
            return FakeResponse(200, payload={"query": {"pages": [{"title": "Gucci", "pageprops": {"wikibase_item": "Q178516"}}]}})
        if url == wikiviews.WIKIDATA:
            return FakeResponse(200, payload={"entities": {"Q178516": {"sitelinks": {
                "enwiki": {"title": "Gucci"}, "frwiki": {"title": "Gucci (entreprise)"}}}}})
        if url.endswith("/w/api.php") and params.get("prop") == "redirects":
            if "en.wikipedia" in url:
                return FakeResponse(200, payload={"query": {"pages": [{"title": "Gucci", "redirects": [
                    {"title": "Gucci Group"}, {"title": "GUCI"}]}]}})
            return FakeResponse(200, payload={"query": {"pages": [{"title": "Gucci (entreprise)"}]}})
        if "/per-article/" in url:
            title = unquote(url.split("/user/")[1].split("/")[0])
            grain = url.split("/user/")[1].split("/")[1]
            if grain == "monthly":
                big = {"Gucci": 100000, "Gucci_Group": 30000, "GUCI": 2, "Gucci_(entreprise)": 4000}[title]
                return FakeResponse(200, payload={"items": [{"timestamp": "2016010100", "views": big}]})
            per_day = {"Gucci": 100, "Gucci_Group": 20, "Gucci_(entreprise)": 7}.get(title)
            if per_day is None:
                return FakeResponse(404, text="")
            return FakeResponse(200, payload={"items": [{"timestamp": f"202610{d:02d}00", "views": per_day} for d in (1, 2, 3)]})
        return FakeResponse(404, text="")
    return h


def test_views_are_summed_across_the_article_and_its_former_title(tmp_data, monkeypatch):
    monkeypatch.setattr(wikiviews, "LANGS", ("en", "fr", "ja"))
    store.write_state(config.STATE_DIR / "attention.json", {"titles": {"gucci": "Gucci"}})
    calls = []
    out = wikiviews.collect(FakeSession(_wiki_handler(calls)), _reg(GUCCI), "r1", end=date(2026, 10, 3),
                            sleep=lambda s: None)
    assert out["series_written"] == 2 and out["failed"] == 0
    en = store.read_jsonl(wikiviews.paths()["dir"] / "en" / "gucci.jsonl")
    assert [r["views"] for r in en] == [120, 120, 120] and en[0]["titles"] == 2     # Gucci + Gucci Group, not the typo
    fr = store.read_jsonl(wikiviews.paths()["dir"] / "fr" / "gucci.jsonl")
    assert [r["views"] for r in fr] == [7, 7, 7]
    st = store.read_state(wikiviews.paths()["state"])
    assert st["articles"]["gucci:ja"] == {"article": None} and st["articles"]["gucci:en"]["counted"] == ["Gucci Group"]
    assert wikiviews.probe("r1") == []
    # a week later: no rescan, only the daily refresh
    n = len(calls)
    wikiviews.collect(FakeSession(_wiki_handler(calls)), _reg(GUCCI), "r2", end=date(2026, 10, 10), sleep=lambda s: None)
    assert not any("monthly" in u or "redirects" in u for u in calls[n:] if "per-article" in u)


def test_articles_worked_in_parallel_give_the_same_series(tmp_data, monkeypatch):
    monkeypatch.setattr(wikiviews, "LANGS", ("en", "fr", "ja"))
    store.write_state(config.STATE_DIR / "attention.json", {"titles": {"gucci": "Gucci", "loewe": "Loewe"}})
    calls = []
    out = wikiviews.collect(FakeSession(_wiki_handler(calls)), _reg(GUCCI), "r1", end=date(2026, 10, 3),
                            sleep=lambda s: None, workers=4, make_session=lambda: FakeSession(_wiki_handler(calls)))
    assert out["series_written"] == 2 and out["workers"] == 4
    en = store.read_jsonl(wikiviews.paths()["dir"] / "en" / "gucci.jsonl")
    assert [r["views"] for r in en] == [120, 120, 120]


# ---------- Wikidata ----------

ENTITY = {"labels": {"en": {"value": "Gucci"}}, "claims": {
    "P127": [{"rank": "normal", "mainsnak": {"datavalue": {"type": "wikibase-entityid", "value": {"id": "Q1"}}},
              "qualifiers": {"P580": [{"datavalue": {"type": "time", "value": {"time": "+1999-03-19T00:00:00Z"}}}]}}],
    "P1037": [
        {"rank": "normal", "mainsnak": {"datavalue": {"type": "wikibase-entityid", "value": {"id": "Q2"}}},
         "qualifiers": {"P2868": [{"datavalue": {"type": "wikibase-entityid", "value": {"id": "Q3"}}}],
                        "P580": [{"datavalue": {"type": "time", "value": {"time": "+2023-01-28T00:00:00Z"}}}],
                        "P582": [{"datavalue": {"type": "time", "value": {"time": "+2025-02-06T00:00:00Z"}}}]}},
        {"rank": "normal", "mainsnak": {"datavalue": {"type": "wikibase-entityid", "value": {"id": "Q4"}}},
         "qualifiers": {"P2868": [{"datavalue": {"type": "wikibase-entityid", "value": {"id": "Q3"}}}]}},
        {"rank": "deprecated", "mainsnak": {"datavalue": {"type": "wikibase-entityid", "value": {"id": "Q9"}}}}]}}


def test_statements_keep_values_dates_and_roles_and_skip_deprecated():
    st = wikidata.statements(ENTITY)
    assert st["owned_by"] == [{"value": "Q1", "rank": "normal", "start": "1999-03-19"}]
    assert [r["value"] for r in st["director"]] == ["Q2", "Q4"]
    assert st["director"][0]["end"] == "2025-02-06" and st["director"][0]["role"] == ["Q3"]
    assert wikidata.referenced({"gucci": st}) == {"Q1", "Q2", "Q3", "Q4"}


def test_disagreements_compare_owner_and_current_creative_lead():
    labels = {"Q1": "Kering", "Q2": "Sabato De Sarno", "Q3": "creative director", "Q4": "Demna"}
    facts = wikidata.resolve_labels({"gucci": wikidata.statements(ENTITY)}, labels)
    assert wikidata.disagreements(_reg(GUCCI), facts) == []
    facts2 = wikidata.resolve_labels({"gucci": wikidata.statements(ENTITY)}, {**labels, "Q1": "LVMH", "Q4": "Someone Else"})
    issues = wikidata.disagreements(_reg(GUCCI, LOEWE), facts2)
    assert any("owner" in i for i in issues) and any("Demna" in i for i in issues) and "loewe: no Wikidata facts" in issues


# ---------- homepage history ----------

HOME = """<html><head><meta property="og:image" content="https://web.archive.org/web/20190105000000im_/https://www.gucci.com/img/share.jpg">
</head><body><img src="/web/20190105000000im_/https://www.gucci.com/img/hero-1.jpg">
<img src="https://web.archive.org/_static/images/toolbar/wayback-toolbar-logo.png"><img src="/img/hero-2.jpg"></body></html>"""


def test_unrewrite_restores_original_addresses():
    out = homepages.unrewrite(HOME)
    assert 'content="https://www.gucci.com/img/share.jpg"' in out and 'src="https://www.gucci.com/img/hero-1.jpg"' in out
    urls = homepages.page_images(HOME, "https://www.gucci.com/uk/en_gb/", "20190105000000")
    names = [u.rsplit("/", 1)[-1] for u in urls]
    assert names == ["share.jpg", "hero-1.jpg", "hero-2.jpg"]
    assert all(u.startswith("https://web.archive.org/web/20190105000000im_/https://www.gucci.com/img/") for u in urls)


def test_final_url_follows_the_archive_redirect():
    assert homepages.final_url("https://web.archive.org/web/20190105000001/https://www.gucci.com/uk/en_gb/",
                               "gucci.com/") == "https://www.gucci.com/uk/en_gb/"
    assert homepages.final_url("", "https://gucci.com/") == "https://gucci.com/"


def test_plan_goes_newest_month_first_across_every_brand_and_skips_done():
    idx = {"a": {"2026-09": [["t1", "u", "200"]], "2026-08": [["t2", "u", "200"]]},
           "b": {"2026-09": [["t3", "u", "200"]], "2026-08": [["t4", "u", "200"], ["t5", "u", "200"]]}}
    rows = {"b:2026-09": {"status": "resolved"},
            "b:2026-08": {"status": "error", "attempts": 1},     # a second capture is left to try
            "a:2026-08": {"status": "no_images", "attempts": 1}}  # its only capture is spent
    assert homepages.plan(idx, rows) == [("a", "2026-09"), ("b", "2026-08")]


def _home_handler(imgs):
    def h(url, params):
        if url == homepages.CDX:
            if params["url"] == "gucci.com":
                return FakeResponse(200, payload=[["timestamp", "original", "statuscode"],
                                                  ["20190105000000", "http://www.gucci.com/", "302"],
                                                  ["20190120000000", "http://www.gucci.com/", "200"],
                                                  ["20190203000000", "http://www.gucci.com/", "200"]])
            return FakeResponse(200, payload=[])
        if url.startswith("https://web.archive.org/web/2019") and "im_/" not in url:
            if url.startswith("https://web.archive.org/web/20190203"):
                return FakeResponse(200, text="<html><body>nothing here</body></html>")
            return FakeResponse(200, text=HOME)
        if "im_/" in url:
            if url not in imgs:
                imgs[url] = jpeg_bytes(toned_image(0.2 + 0.2 * len(imgs), seed=len(imgs)))
            return FakeResponse(200, content=imgs[url])
        return FakeResponse(404, text="")
    return h


def test_collect_reads_each_month_once_and_keeps_no_pixels(tmp_data, monkeypatch, tmp_path):
    sites = tmp_path / "sites.csv"
    sites.write_text("house,domains,note\ngucci,gucci.com,\nloewe,loewe.com,\n", encoding="utf-8")
    monkeypatch.setattr(homepages, "SITES_FILE", sites)

    class C(homepages.Crawler):
        pass
    imgs = {}
    c = C(FakeSession(_home_handler(imgs)), pause=0)
    monkeypatch.setattr(homepages, "download", _fake_download(c))
    scorer = FakeScorer(load_rubric())
    out = homepages.collect(c, _reg(GUCCI, LOEWE), FakeEmbedder(), scorer, "r1")
    assert out["captures"] == 2 and out["resolved"] == 1 and out["no_images"] == 1
    assert out["months_in_archive"] == {"gucci": 2, "loewe": 0}
    P = homepages.paths()
    rows = {r["key"]: r for r in store.read_jsonl(P["captures"] / "gucci.jsonl")}
    assert rows["gucci:2019-01"]["status"] == "resolved" and len(rows["gucci:2019-01"]["images"]) == 3
    assert rows["gucci:2019-02"]["status"] == "no_images"
    assert len(store.read_jsonl(P["obs"] / "tone-v1.jsonl")) == 3 and scorer.calls == 3
    assert len(VectorStore("fake", root=P["vectors"]).vecs) == 3
    assert homepages.probe("r1") == []
    again = homepages.collect(c, _reg(GUCCI, LOEWE), FakeEmbedder(), scorer, "r2")
    assert again["captures"] == 0 and scorer.calls == 3
    assert not [p for p in tmp_data.rglob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]


def _fake_download(c):
    from adtone.media import to_fetched

    def dl(urls, session=None, keep=4, **k):
        got = {}
        for u in urls:
            r = c.s.get(u)
            if r.status_code == 200 and r.content:
                f = to_fetched(r.content)
                got.setdefault(f.sha, f)
        return list(got.values())[:keep]
    return dl


def test_spread_keeps_the_first_capture_and_spaces_the_rest():
    rows = [[f"201901{d:02d}000000", "u", "200"] for d in range(1, 31)]
    got = homepages._spread(rows, 4)
    assert len(got) == 4 and got[0] == rows[0] and got[-1] == rows[-1]
    assert homepages._spread(rows[:3], 4) == rows[:3]


def test_more_pictures_are_found_in_backgrounds_preloads_and_inline_data():
    html = """<html><head><link rel="preload" as="image" href="/media/hero-preload.jpg">
    <script>window.__DATA__={"slides":[{"src":"https:\\/\\/cdn.house.com\\/campaign\\/look-01.jpg?w=2000"}]}</script></head>
    <body><div style="background-image: url('/media/hero-bg.webp')"></div><img src="/media/logo.png"></body></html>"""
    urls = homepages.page_images(html, "https://www.house.com/gb/", "20240105000000")
    names = [u.rsplit("/", 1)[-1] for u in urls]
    assert "hero-bg.webp" in names and "hero-preload.jpg" in names and "look-01.jpg?w=2000" in names
    assert not any("logo" in n for n in names)
    assert all(u.startswith("https://web.archive.org/web/20240105000000im_/") for u in urls)


def test_a_country_chooser_leads_to_the_british_page_and_a_refresh_is_followed():
    chooser = """<html><body><a href="/fr-fr/">France</a><a href="/en-us/">United States</a>
    <a href="/en-gb/">United Kingdom</a><a href="https://www.other.com/en-gb/">elsewhere</a>
    <a href="/en-gb/stores">stores</a></body></html>"""
    assert homepages.next_page(chooser, "https://www.house.com/") == ("https://www.house.com/en-gb/", "country page")
    lv = '<a href="/eng-us/homepage">US</a><a href="/eng-e1/homepage">International</a>'
    assert homepages.next_page(lv, "https://www.house.com/")[0] == "https://www.house.com/eng-us/homepage"
    refresh = '<meta http-equiv="refresh" content="0; url=/gb/home">'
    assert homepages.next_page(refresh, "https://www.house.com/") == ("https://www.house.com/gb/home", "refresh")
    script = "<script>window.location.href = 'https://www.house.com/us/';</script>"
    assert homepages.next_page(script, "https://www.house.com/") == ("https://www.house.com/us/", "script redirect")
    assert homepages.next_page("<html><body>nothing</body></html>", "https://www.house.com/") is None


def _hp_handler(imgs, pages, cdx_rows, cdx_calls):
    def h(url, params):
        if url == homepages.CDX:
            cdx_calls.append(dict(params))
            return FakeResponse(200, payload=[["timestamp", "original", "statuscode"]] + cdx_rows.get(params["url"], []))
        if "im_/" in url:
            if url not in imgs:
                imgs[url] = jpeg_bytes(toned_image(0.2 + 0.1 * (len(imgs) % 7), seed=len(imgs)))
            return FakeResponse(200, content=imgs[url])
        for prefix, resp in pages.items():
            if url.startswith(prefix):
                return resp
        return FakeResponse(404, text="")
    return h


def test_a_failed_capture_falls_back_to_the_next_and_a_chooser_is_stepped_through(tmp_data, monkeypatch, tmp_path):
    sites = tmp_path / "sites.csv"
    sites.write_text("house,domains,note\ngucci,gucci.com,\nloewe,loewe.com;oldloewe.com,\n", encoding="utf-8")
    monkeypatch.setattr(homepages, "SITES_FILE", sites)
    chooser = FakeResponse(200, text='<html><body><a href="/en-gb/">UK</a><a href="/fr-fr/">FR</a></body></html>')
    pages = {
        "https://web.archive.org/web/20240102000000/": FakeResponse(403, text="blocked"),   # the bot wall, captured
        "https://web.archive.org/web/20240110000000/": FakeResponse(200, text=HOME),
        "https://web.archive.org/web/20240203000000/https://www.loewe.com/en-gb/": FakeResponse(200, text=HOME),
        "https://web.archive.org/web/20240203000000/": chooser,
    }
    cdx_rows = {"gucci.com": [["20240102000000", "https://www.gucci.com/", "302"],
                              ["20240110000000", "https://www.gucci.com/", "200"]],
                "loewe.com": [["20240203000000", "https://www.loewe.com/", "200"]],
                "oldloewe.com": [["20140203000000", "https://www.oldloewe.com/", "200"]]}
    imgs, cdx_calls = {}, []
    sess = FakeSession(_hp_handler(imgs, pages, cdx_rows, cdx_calls))
    c = homepages.Crawler(sess, pause=0)
    monkeypatch.setattr(homepages, "download", _fake_download(c))
    scorer = FakeScorer(load_rubric())
    out = homepages.collect(c, _reg(GUCCI, LOEWE), FakeEmbedder(), scorer, "r1", workers=3,
                            make_crawler=lambda: homepages.Crawler(sess, pause=0))
    P = homepages.paths()
    g = {r["key"]: r for r in store.read_jsonl(P["captures"] / "gucci.jsonl")}["gucci:2024-01"]
    assert g["status"] == "resolved" and g["capture"] == "20240110000000" and g["attempts"] == 2
    assert g["tried"] == ["20240102000000", "20240110000000"]
    lo = {r["key"]: r for r in store.read_jsonl(P["captures"] / "loewe.jsonl")}
    assert lo["loewe:2024-02"]["status"] == "resolved" and lo["loewe:2024-02"]["via"] == "country page"
    assert lo["loewe:2024-02"]["page"] == "https://www.loewe.com/en-gb/"
    assert "loewe:2014-02" in lo     # the older domain fills the months the current one lacks
    assert out["later_capture"] == 1 and out["stepped"] == 1 and out["months_in_archive"] == {"gucci": 1, "loewe": 2}
    assert scorer.calls == len(store.read_jsonl(P["obs"] / "tone-v1.jsonl")) == len(VectorStore("fake", root=P["vectors"]).vecs)
    # the index is kept: the next run asks only from the newest month on file
    cdx_calls.clear()
    homepages.collect(c, _reg(GUCCI, LOEWE), FakeEmbedder(), scorer, "r2")
    assert {p["url"]: p["from"] for p in cdx_calls} == {"gucci.com": "202401", "loewe.com": "202402", "oldloewe.com": "202402"}
    assert json.loads((P["index"] / "loewe.json").read_text())["2014-02"][0][0] == "20140203000000"


def test_a_reader_failure_files_nothing_from_the_chunk(tmp_data, monkeypatch, tmp_path):
    from adtone.score import ScoreError
    sites = tmp_path / "sites.csv"
    sites.write_text("house,domains,note\ngucci,gucci.com,\n", encoding="utf-8")
    monkeypatch.setattr(homepages, "SITES_FILE", sites)
    pages = {"https://web.archive.org/web/2024": FakeResponse(200, text=HOME)}
    cdx_rows = {"gucci.com": [["20240110000000", "https://www.gucci.com/", "200"]]}
    imgs = {}
    c = homepages.Crawler(FakeSession(_hp_handler(imgs, pages, cdx_rows, [])), pause=0)
    monkeypatch.setattr(homepages, "download", _fake_download(c))

    class Down(FakeScorer):
        def score_many(self, jpegs):
            raise ScoreError("API: Modal ConnectionError")
    out = homepages.collect(c, _reg(GUCCI), FakeEmbedder(), Down(load_rubric()), "r1")
    assert out["stopped"].startswith("reader: API") and out["captures"] == 0
    assert not store.read_jsonl(homepages.paths()["captures"] / "gucci.jsonl")


def test_an_archive_that_does_not_answer_leaves_the_capture_for_a_later_run(tmp_data, monkeypatch, tmp_path):
    import requests
    sites = tmp_path / "sites.csv"
    sites.write_text("house,domains,note\ngucci,gucci.com,\n", encoding="utf-8")
    monkeypatch.setattr(homepages, "SITES_FILE", sites)
    state = {"down": True}
    cdx_rows = {"gucci.com": [["20240110000000", "https://www.gucci.com/", "200"],
                              ["20240120000000", "https://www.gucci.com/", "200"]]}
    inner = _hp_handler({}, {"https://web.archive.org/web/2024": FakeResponse(200, text=HOME)}, cdx_rows, [])

    def h(url, params):
        if state["down"] and "im_/" in url:
            raise requests.ConnectionError("connection reset by peer")
        if state["down"] and url.startswith("https://web.archive.org/web/2024"):
            return FakeResponse(503, text="Temporarily Offline")
        return inner(url, params)
    c = homepages.Crawler(FakeSession(h), pause=0)
    scorer = FakeScorer(load_rubric())
    homepages.collect(c, _reg(GUCCI), FakeEmbedder(), scorer, "r1")
    row = store.read_jsonl(homepages.paths()["captures"] / "gucci.jsonl")[0]
    assert row["status"] == "error" and row["attempts"] == 0 and row["transient"] == 1 and row["tried"] == []
    state["down"] = False
    homepages.collect(c, _reg(GUCCI), FakeEmbedder(), scorer, "r2")     # the real download, now answered
    row = store.read_jsonl(homepages.paths()["captures"] / "gucci.jsonl")[0]
    assert row["status"] == "resolved" and row["capture"] == "20240110000000" and row["attempts"] == 1
    assert homepages._done({"status": "error", "attempts": 0, "transient": homepages.MAX_TRANSIENT}, 4)


def test_pictures_that_never_arrive_are_transient_not_missing():
    import requests

    def h(url, params):
        if "im_/" in url:
            raise requests.ConnectionError("reset")
        return FakeResponse(200, text=HOME)
    c = homepages.Crawler(FakeSession(h), pause=0)
    res = homepages.read_capture(c, "20240110000000", "https://www.gucci.com/")
    assert res["status"] == "error" and res["transient"] is True
    blank = homepages.read_capture(homepages.Crawler(FakeSession(lambda u, p: FakeResponse(200, text="<html><title>Choose</title></html>")),
                                                     pause=0), "20240110000000", "https://www.gucci.com/")
    assert blank["status"] == "no_images" and blank["seen"]["title"] == "Choose" and blank["seen"]["candidates"] == 0


def test_a_failed_index_query_keeps_the_months_on_file():
    idx = {"2024-01": [["20240110000000", "u", "200"]], "2024-02": [["20240203000000", "u", "200"]]}
    down = homepages.Crawler(FakeSession(lambda u, p: FakeResponse(503, text="<title>Internet Archive: Temporarily Offline</title>")), pause=0)
    out, diag = homepages.refresh_index(down, ["gucci.com"], idx)
    assert out == idx and diag["gucci.com"]["http"] == 503
    empty = homepages.Crawler(FakeSession(lambda u, p: FakeResponse(200, payload=[["timestamp", "original", "statuscode"]])), pause=0)
    out2, diag2 = homepages.refresh_index(empty, ["gucci.com"], {})
    assert out2 == {} and diag2 == {}


def test_the_gate_holds_every_worker_after_a_refusal_and_choosers_prefer_the_uk_page():
    now = {"t": 0.0}
    waits = []
    g = homepages.Gate(clock=lambda: now["t"], sleep=waits.append)
    g.wait()
    g.trip(60)
    now["t"] = 10
    g.wait()
    assert waits == [50] and g.trips == 1
    html = '<a href="/be/en_gb/">Belgium</a><a href="/uk/en_gb/">United Kingdom</a><a href="/us/en/">US</a>'
    assert homepages.next_page(html, "https://www.gucci.com/")[0] == "https://www.gucci.com/uk/en_gb/"


def test_extensionless_image_servers_and_a_redirect_weighed_against_the_uk_page():
    html = """<script>window.__STATE__={"hero":{"src":"https:\\/\\/assets.house.com\\/is\\/image\\/Houseltd\\/AW24_HERO?$BBY_V2$&wid=1920"}}</script>"""
    urls = homepages.page_images(html, "https://www.house.com/", "20240105000000")
    assert any("/is/image/Houseltd/AW24_HERO" in u for u in urls)
    page = "<script>location.href='/de-de/home';</script><a href='/en-gb/home'>United Kingdom</a>"
    assert homepages.next_page(page, "https://www.house.com/") == ("https://www.house.com/en-gb/home", "country page")
    only = "<script>location.href='/de-de/home';</script>"
    assert homepages.next_page(only, "https://www.house.com/") == ("https://www.house.com/de-de/home", "script redirect")


def test_a_history_too_long_for_one_index_query_is_asked_for_year_by_year():
    def h(url, params):
        if "to" not in params:
            return FakeResponse(503, text="<title>Internet Archive: Temporarily Offline</title>")
        y = params["from"][:4]
        return FakeResponse(200, payload=[["timestamp", "original", "statuscode"], [f"{y}0105000000", "https://x.com/", "200"]])
    c = homepages.Crawler(FakeSession(h), pause=0)
    out, diag = homepages.refresh_index(c, ["x.com"], {}, first="2024-01")
    assert sorted(out) == [f"{y}-01" for y in range(2024, date.today().year + 1)] and diag == {}
