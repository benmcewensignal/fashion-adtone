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
    caps = {"a": {"2026-09": ("t1", "u"), "2026-08": ("t2", "u")}, "b": {"2026-09": ("t3", "u")}}
    p = homepages.plan(caps, done={"b:2026-09"})
    assert [(h, m) for h, m, _, _ in p] == [("a", "2026-09"), ("a", "2026-08")]


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
