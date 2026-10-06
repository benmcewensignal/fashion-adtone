import json
from datetime import date

import numpy as np

from adtone import backcat, calibrate, config, store
from adtone.embed import FakeEmbedder, VectorStore
from adtone.registry import House, Registry
from adtone.score import FakeScorer, load_rubric
from adtone.synth import FakeResponse, FakeSession, jpeg_bytes, toned_image

WORK = """<html><head><title>Bottega Veneta Winter 25 Campaign</title>
<meta property="og:title" content="Bottega Veneta Winter 25 Campaign"></head><body><div>
<h1>Bottega Veneta Winter 25 Campaign</h1><p>Source: bottegaveneta.com</p><p>Published: September 2025</p>
<div>All people in this campaign:</div>
<ul><li>Juergen Teller - Photographer</li><li>Katie Shaw - Fashion Editor/Stylist</li>
<li>Anita Bitton - Casting Director</li><li>Juergen Teller - Photographer</li></ul>
<div>Credits for this picture: Juergen Teller (Photographer) Brands in this picture: Bottega Veneta</div>
<div>Credits for this picture: Juergen Teller (Photographer) Brands in this picture: Bottega Veneta</div>
<div>Credits for this picture: Juergen Teller (Photographer) Brands in this picture: Bottega Veneta</div>
<iframe src="https://player.vimeo.com/video/1"></iframe>
<a href="https://www.bottegaveneta.com/en-gb/stories/winter-25">View the complete story on bottegaveneta.com</a>
</div></body></html>"""
INSTAGRAM = (WORK.replace("bottegaveneta.com</p>", "instagram.com</p>")
             .replace("https://www.bottegaveneta.com/en-gb/stories/winter-25", "https://www.instagram.com/p/abc/")
             .replace("Winter 25", "Drop"))
COVER = WORK.replace("All people in this campaign", "All people in this magazine cover").replace(
    "Published: September 2025", "Published: 03/02/2026")
CLIENT = ('<a href="/work/bottega-veneta-bottega-veneta-winter-25-campaign">w</a>'
          '<a href="https://models.com/Work/bottega-veneta-bottega-veneta-winter-25-campaign/">dup</a>'
          '<a href="/work/bottega-veneta-bottega-veneta-drop">ig</a><a href="/people/x">p</a>')
ARCHIVED = """<html><head><meta property="og:image" content="https://www.bottegaveneta.com/img/hero.jpg"></head><body>
<img src="/img/look-1.jpg"><img srcset="/img/look-2-640.jpg 640w, /img/look-2-1600.jpg 1600w">
<img src="/assets/logo.svg"><img data-src="/img/look-3.webp">
<script type="application/ld+json">{"image": "https:\\/\\/www.bottegaveneta.com\\/img\\/hero.jpg"}</script></body></html>"""


def test_parse_work_reads_the_spine():
    w = backcat.parse_work(WORK, "https://models.com/work/bottega-veneta-bottega-veneta-winter-25-campaign")
    assert w["kind"] == "campaign" and w["published"] == "2025-09-01" and w["source_domain"] == "bottegaveneta.com"
    assert w["source_url"] == "https://www.bottegaveneta.com/en-gb/stories/winter-25"
    assert w["people"]["Photographer"] == ["Juergen Teller"] and w["people"]["Casting Director"] == ["Anita Bitton"]
    assert w["brands"] == ["Bottega Veneta"] and w["picture_credit_lines"] == 3 and w["films"] == 1
    assert w["campaign_id"] == "bottega-veneta-bottega-veneta-winter-25-campaign"


def test_us_style_dates_and_page_kinds():
    w = backcat.parse_work(COVER, "https://models.com/work/x")
    assert w["kind"] == "magazine cover" and w["published"] == "2026-03-02"


def test_client_page_links_are_normalised_and_deduplicated():
    assert backcat.parse_client(CLIENT) == ["https://models.com/work/bottega-veneta-bottega-veneta-winter-25-campaign",
                                            "https://models.com/work/bottega-veneta-bottega-veneta-drop"]


def test_archived_page_images_resolve_skip_logos_and_take_the_largest_srcset():
    urls = backcat.archived_image_urls(ARCHIVED, "https://www.bottegaveneta.com/en-gb/stories/winter-25", "20250915120000")
    names = [u.rsplit("/", 1)[1] for u in urls]
    assert names == ["hero.jpg", "look-1.jpg", "look-2-1600.jpg", "look-3.webp"]
    assert all(u.startswith("https://web.archive.org/web/20250915120000im_/https://www.bottegaveneta.com/") for u in urls)


def _handler():
    imgs = {}

    def h(url, params):
        if url.endswith("/robots.txt"):
            return FakeResponse(200, text="")
        if url == "https://models.com/client/bottega-veneta":
            return FakeResponse(200, text=CLIENT)
        if url == backcat.CDX and params.get("matchType") == "prefix":
            return FakeResponse(200, payload=[["original"], ["https://models.com/work/bottega-veneta-bottega-veneta-winter-25-campaign"]])
        if url.endswith("winter-25-campaign"):
            return FakeResponse(200, text=WORK)
        if url.endswith("bottega-veneta-drop"):
            return FakeResponse(200, text=INSTAGRAM)
        if url == backcat.CDX:
            return FakeResponse(200, payload=[["timestamp"], ["20250915120000"]])
        if "id_/" in url:
            return FakeResponse(200, text=ARCHIVED)
        if "im_/" in url:
            if url not in imgs:
                imgs[url] = jpeg_bytes(toned_image(0.2 + 0.15 * len(imgs), seed=len(imgs)))
            return FakeResponse(200, content=imgs[url])
        return FakeResponse(404, text="")
    return h


def test_discover_then_images_end_to_end_and_idempotent(tmp_data):
    reg = Registry(1, "DRAFT", [House("bottega_veneta", "Bottega Veneta", "treated", "Kering", ["Bottega Veneta"], [], [])])
    c = backcat.Crawler(FakeSession(_handler()), pause=0)
    d = backcat.discover(c, reg, "r1")
    assert d["campaigns_on_file"] == 2 and d["pages_read"] == 2
    scorer = FakeScorer(load_rubric())
    counts = backcat.images(c, FakeEmbedder(), scorer, "r1")
    assert counts["resolved"] == 1 and counts["no_pixel_venue"] == 1 and counts["images"] == 4 and scorer.calls == 4
    P = backcat.paths()
    assert len(store.read_jsonl(P["obs"] / "tone-v1.jsonl")) == 4
    assert len(VectorStore("fake", root=P["vectors"]).vecs) == 4
    assert backcat.probe("r1", "tone-v1@fake", "fake") == []
    again = backcat.images(c, FakeEmbedder(), scorer, "r2")
    assert again["campaigns"] == 0 and scorer.calls == 4
    assert not [p for p in tmp_data.rglob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]


def test_robots_disallow_is_honoured(tmp_data):
    def h(url, params):
        if url.endswith("/robots.txt"):
            return FakeResponse(200, text="User-agent: *\nDisallow: /work/\n")
        return _handler()(url, params)
    reg = Registry(1, "DRAFT", [House("bottega_veneta", "Bottega Veneta", "treated", "Kering", ["Bottega Veneta"], [], [])])
    d = backcat.discover(backcat.Crawler(FakeSession(h), pause=0), reg, "r1")
    assert d["pages_read"] == 0 and d["robots_skipped"] == 2


def _items(n, house="h", seed=0, shift=None, dim=64):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        v = rng.normal(size=dim)
        v /= np.linalg.norm(v)
        if shift is not None:
            v = v + shift
        out.append({"house": house, "date": date(2026, 1, 1 + i % 28), "phash": f"{rng.integers(0, 2**63):016x}", "vec": v})
    return out


def test_calibration_recovers_a_planted_channel_offset_and_refuses_thin_evidence():
    rng = np.random.default_rng(9)
    true = rng.normal(size=64)
    true = 0.3 * true / np.linalg.norm(true)
    back = _items(25, seed=1)
    meta = [{**b, "vec": b["vec"] + true} for b in back] + _items(30, seed=2)
    out = calibrate.calibrate(meta, back)
    assert out["n_pairs"] == 25 and out["calibrated"] is True
    est = np.array(out["offset"])
    assert est @ true / (np.linalg.norm(est) * np.linalg.norm(true)) > 0.9
    thin = calibrate.calibrate(meta[:5], back[:5])
    assert thin["n_pairs"] == 5 and thin["calibrated"] is False
    moved = calibrate.splice(back[0]["vec"], est)
    assert moved @ (meta[0]["vec"] / np.linalg.norm(meta[0]["vec"])) > back[0]["vec"] @ (meta[0]["vec"] / np.linalg.norm(meta[0]["vec"]))


def test_unrelated_images_never_pair_across_houses_or_long_gaps():
    back = _items(5, house="a", seed=3)
    meta = [{**b, "house": "b"} for b in back] + [{**b, "date": date(2025, 1, 1)} for b in back]
    assert calibrate.match(meta, back) == []


def _two_houses():
    return Registry(1, "DRAFT", [House("bottega_veneta", "Bottega Veneta", "treated", "Kering", ["Bottega Veneta"], [], []),
                                 House("prada", "Prada", "control", "Prada", ["Prada"], [], [])])


def test_a_busy_archive_or_a_refusing_site_is_noted_not_fatal(tmp_data):
    def h(url, params):
        if url == "https://models.com/client/prada":
            return FakeResponse(403, text="Forbidden")
        if url == backcat.CDX and "prada" in params.get("url", ""):
            return FakeResponse(200, text="<html>Service busy</html>")      # HTML where JSON should be
        return _handler()(url, params)
    d = backcat.discover(backcat.Crawler(FakeSession(h), pause=0), _two_houses(), "r1")
    assert d["campaigns_on_file"] == 2 and d["errors"] == {}
    assert d["statuses"].get("client 403") == 1 and d["new_urls"]["prada"] == 0


def test_one_house_breaking_does_not_lose_the_others(tmp_data):
    import requests

    def h(url, params):
        if "prada" in url or "prada" in str(params):
            raise requests.ConnectionError("reset by peer")
        return _handler()(url, params)
    d = backcat.discover(backcat.Crawler(FakeSession(h), pause=0), _two_houses(), "r1")
    assert "prada" in d["errors"] and d["errors"]["prada"].startswith("ConnectionError")
    assert d["campaigns_on_file"] == 2      # Bottega's campaigns were found and saved


def test_a_crash_still_leaves_a_record(tmp_data, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("something unforeseen")
    monkeypatch.setattr(backcat, "discover", boom)
    monkeypatch.setattr(backcat.registry, "load", lambda: _two_houses())
    assert backcat.main(["discover", "--run", "r9"]) == 1
    rows = store.read_jsonl(backcat.paths()["prov"])
    assert rows[-1]["crashed"].startswith("RuntimeError") and rows[-1]["run_id"] == "r9"


WALLED = WORK.replace("complete story", "log in to see the story").replace("Credits for this picture", "Log in")


def test_a_walled_page_takes_its_source_and_crew_from_an_archived_copy(tmp_data):
    def h(url, params):
        if url.endswith("winter-25-campaign") and "web.archive.org" not in url:
            return FakeResponse(200, text=WALLED)            # today's page: behind the login wall
        if url == backcat.CDX and params.get("url", "").startswith("models.com/work/") and "matchType" not in params:
            return FakeResponse(200, payload=[["timestamp"], ["20240301000000"]])
        if "web.archive.org/web/20240301000000id_/https://models.com/work/" in url:
            return FakeResponse(200, text=WORK)              # the archived copy from before the wall
        return _handler()(url, params)
    d = backcat.discover(backcat.Crawler(FakeSession(h), pause=0), Registry(1, "DRAFT", [
        House("bottega_veneta", "Bottega Veneta", "treated", "Kering", ["Bottega Veneta"], [], [])]), "r1")
    rows = store.ShardedTable(backcat.paths()["campaigns"], "campaign_id").rows
    walled = [r for r in rows.values() if r["campaign_id"].endswith("winter-25-campaign")][0]
    assert walled.get("source_url", "").startswith("http") and walled.get("archived_page") == "20240301000000"
    assert d["statuses"].get("archived copy used") == 1


def test_every_house_gets_a_share_of_the_reading_budget(tmp_data):
    many = [House(f"h{i}", f"House {i}", "control", "G", [f"House {i}"], [], []) for i in range(10)]
    pages = {f"https://models.com/client/house-{i}": "".join(
        f'<a href="/work/house-{i}-house-{i}-c{k}">c</a>' for k in range(20)) for i in range(10)}

    def h(url, params):
        if url.endswith("/robots.txt"):
            return FakeResponse(200, text="")
        if url in pages:
            return FakeResponse(200, text=pages[url])
        if url == backcat.CDX:
            return FakeResponse(200, payload=[["original"]])
        if "/work/" in url:
            return FakeResponse(200, text=WORK)
        return FakeResponse(404, text="")
    d = backcat.discover(backcat.Crawler(FakeSession(h), pause=0), Registry(1, "DRAFT", many), "r1", max_reads=50)
    by_house = {}
    for r in store.ShardedTable(backcat.paths()["campaigns"], "campaign_id").rows.values():
        by_house[r["house_id"]] = by_house.get(r["house_id"], 0) + 1
    assert len(by_house) == 10 and max(by_house.values()) <= 5 and d["pages_read"] == 50


def test_campaigns_read_before_the_fix_are_repaired_once(tmp_data):
    table = store.ShardedTable(backcat.paths()["campaigns"], "campaign_id")
    table.upsert({"campaign_id": "bottega-veneta-bottega-veneta-winter-25-campaign", "house_id": "bottega_veneta",
                  "url": "https://models.com/work/bottega-veneta-bottega-veneta-winter-25-campaign"}, shard="bottega_veneta")
    table.save()

    def h(url, params):
        if url == backcat.CDX and params.get("url", "").startswith("models.com/work/") and "matchType" not in params:
            return FakeResponse(200, payload=[["timestamp"], ["20240301000000"]])
        if "web.archive.org/web/20240301000000id_/https://models.com/work/" in url:
            return FakeResponse(200, text=WORK)
        if url == backcat.CDX and params.get("matchType") == "prefix":
            return FakeResponse(200, payload=[["original"]])
        if url.endswith("/robots.txt"):
            return FakeResponse(200, text="")
        return FakeResponse(404, text="")
    reg = Registry(1, "DRAFT", [House("bottega_veneta", "Bottega Veneta", "treated", "Kering", ["Bottega Veneta"], [], [])])
    d = backcat.discover(backcat.Crawler(FakeSession(h), pause=0), reg, "r2")
    row = store.ShardedTable(backcat.paths()["campaigns"], "campaign_id").rows["bottega-veneta-bottega-veneta-winter-25-campaign"]
    assert d["repaired"] == 1 and row["source_url"].startswith("http") and row["archive_checked"] == "r2"
    again = backcat.discover(backcat.Crawler(FakeSession(h), pause=0), reg, "r3")
    assert again["repaired"] == 0      # checked once, never again
