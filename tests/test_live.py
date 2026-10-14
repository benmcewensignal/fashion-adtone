import json

import numpy as np

from adtone import forward_image, homepages, live, store
from adtone.backcat import Crawler
from adtone.embed import FakeEmbedder, VectorStore
from adtone.registry import House, Registry
from adtone.score import FakeScorer, ScoreError, load_rubric
from adtone.synth import FakeResponse, FakeSession, jpeg_bytes, toned_image

PAGE = """<html><head><meta property="og:image" content="https://cdn.house.com/hero.jpg"></head>
<body><img src="https://cdn.house.com/look-1.jpg"><img src="/media/look-2.jpg"><img src="/media/logo.png"></body></html>"""


def reg(*ids):
    return Registry(1, "FROZEN", [House(h, h, "control", "G", [h], [h], []) for h in ids])


def handler(robots=None, page=PAGE, status=200, cdn_robots="User-agent: *\nAllow: /\n"):
    imgs = {}

    def h(url, params):
        if url.endswith("/robots.txt"):
            if "cdn." in url:
                return FakeResponse(200, text=cdn_robots)
            return FakeResponse(200, text=robots or "User-agent: *\nAllow: /\n")
        if url.rstrip("/").endswith(("www.gucci.com", "www.loewe.com")):
            return FakeResponse(status, text=page)
        if url.endswith((".jpg", ".png")):
            if url not in imgs:
                imgs[url] = jpeg_bytes(toned_image(0.3 + 0.15 * len(imgs), seed=len(imgs)))
            return FakeResponse(200, content=imgs[url])
        return FakeResponse(404, text="")
    return h


def setup(tmp_path, monkeypatch, houses=("gucci",)):
    sites = tmp_path / "sites.csv"
    sites.write_text("house,domains,note\n" + "".join(f"{h},{h}.com,\n" for h in houses), encoding="utf-8")
    monkeypatch.setattr(homepages, "SITES_FILE", sites)


def test_a_homepage_is_read_once_a_day_and_no_pixels_are_kept(tmp_data, monkeypatch, tmp_path):
    setup(tmp_path, monkeypatch, ("gucci", "loewe"))
    c = Crawler(FakeSession(handler()), pause=0)
    scorer = FakeScorer(load_rubric())
    out = live.collect(c, reg("gucci", "loewe"), FakeEmbedder(), scorer, "r1", today="2026-10-15")
    assert out["resolved"] == 2 and out["houses"] == 2 and out["declined"] == {}
    P = live.paths()
    row = store.read_jsonl(P["captures"] / "gucci.jsonl")[0]
    assert row["key"] == "gucci:2026-10-15" and row["month"] == "2026-10" and len(row["images"]) == 3   # no logo
    # the two houses share two pictures on one image server, so four distinct pictures are read, each once
    assert len(store.read_jsonl(P["obs"] / "tone-v1.jsonl")) == scorer.calls == 4
    assert len(VectorStore("fake", root=P["vectors"]).vecs) == 4
    assert live.probe("r1") == []
    again = live.collect(c, reg("gucci", "loewe"), FakeEmbedder(), scorer, "r2", today="2026-10-15")
    assert again["houses"] == 0 and scorer.calls == 4
    assert not [p for p in tmp_data.rglob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]


def test_a_site_that_declines_is_recorded_and_left(tmp_data, monkeypatch, tmp_path):
    setup(tmp_path, monkeypatch)
    for h, why in ((handler(robots="User-agent: *\nDisallow: /\n"), "robots.txt"), (handler(status=403), "HTTP 403"),
                   (handler(page="<html><title>Access Denied</title></html>"), "a challenge page instead of the homepage")):
        s = FakeSession(h)
        row, got = live.read_page(Crawler(s, pause=0), "https://www.gucci.com/")
        assert row["status"] == "declined" and row["why"] == why and got == []
        assert not any(u.endswith(".jpg") for u, _ in s.calls)
    s = FakeSession(handler(robots="User-agent: *\nDisallow: /\n"))
    live.read_page(Crawler(s, pause=0), "https://www.gucci.com/")
    assert [u for u, _ in s.calls] == ["https://www.gucci.com/robots.txt"]       # not even the page is asked for


def test_pictures_a_picture_host_disallows_are_not_fetched():
    s = FakeSession(handler(cdn_robots="User-agent: *\nDisallow: /\n"))
    row, got = live.read_page(Crawler(s, pause=0), "https://www.gucci.com/")
    assert row["status"] == "resolved" and len(got) == 1                         # only /media/look-2.jpg, on the site
    assert not any("cdn.house.com/" in u and u.endswith(".jpg") for u, _ in s.calls)


def test_a_reader_failure_files_nothing(tmp_data, monkeypatch, tmp_path):
    setup(tmp_path, monkeypatch)

    class Down(FakeScorer):
        def score_many(self, jpegs):
            raise ScoreError("API: Modal ConnectionError")
    out = live.collect(Crawler(FakeSession(handler()), pause=0), reg("gucci"), FakeEmbedder(), Down(load_rubric()), "r1",
                       today="2026-10-15")
    assert out["stopped"].startswith("reader: API")
    assert not store.read_jsonl(live.paths()["captures"] / "gucci.jsonl")


def test_the_forward_template_never_reads_the_live_record(tmp_path):
    hp = tmp_path / "data" / "homepages"
    for sub in ("captures", "obs", "vectors/openclip-vitb32-laion2b", "live/captures", "live/obs", "live/vectors/openclip-vitb32-laion2b"):
        (hp / sub).mkdir(parents=True)
    out = {"creative_type": "brand_image"}
    for base, sha in ((hp, "a" * 64), (hp / "live", "b" * 64)):
        (base / "captures" / "gucci.jsonl").write_text(json.dumps(
            {"house_id": "gucci", "month": "2026-10", "status": "resolved", "images": [{"sha": sha}]}) + "\n")
        (base / "obs" / "tone-v1.jsonl").write_text(json.dumps(
            {"sha": sha, "instrument": forward_image.INSTRUMENT, "status": "ok", "output": out}) + "\n")
        np.savez_compressed(base / "vectors" / "openclip-vitb32-laion2b" / "2026.npz",
                            shas=np.array([sha], dtype="U64"), vecs=np.ones((1, 4), dtype=np.float16))
    pics = forward_image.load_pictures(tmp_path)
    assert [p["sha"] for p in pics["gucci"]] == ["a" * 64]


def test_the_guard_lets_the_live_workflow_write_only_its_own_files():
    from adtone import guard
    ok = ["data/homepages/live/captures/gucci.jsonl", "data/homepages/live/obs/tone-v1.jsonl",
          "data/homepages/live/vectors/openclip-vitb32-laion2b/2026.npz", "data/state/live.json", "data/provenance/live.jsonl"]
    assert guard.violations("live", ok) == []
    assert guard.violations("live", ["data/homepages/captures/gucci.jsonl"])


def test_a_run_out_of_time_keeps_the_houses_it_read(tmp_data, monkeypatch, tmp_path):
    setup(tmp_path, monkeypatch, ("gucci", "loewe"))
    ticks = iter([0, 0, 10_000, 10_000])
    out = live.collect(Crawler(FakeSession(handler()), pause=0), reg("gucci", "loewe"), FakeEmbedder(),
                       FakeScorer(load_rubric()), "r1", today="2026-10-15", budget_s=60, clock=lambda: next(ticks))
    assert out["stopped"] == "budget" and out["houses"] == 1
    assert [r["key"] for r in store.read_jsonl(live.paths()["captures"] / "gucci.jsonl")] == ["gucci:2026-10-15"]


def test_a_stalled_site_is_given_seconds_not_minutes():
    seen = []

    class S:
        headers = {}

        def get(self, url, **kw):
            seen.append(kw.get("timeout"))
            return FakeResponse(404, text="")
    t = live.Timed(S(), timeout=20)
    t.get("https://www.gucci.com/", timeout=60)
    t.get("https://www.gucci.com/a.jpg", timeout=45, stream=True)
    assert seen == [20, 20]
