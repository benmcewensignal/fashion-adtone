from datetime import date

from adtone import config, store
from adtone.embed import FakeEmbedder, VectorStore
from adtone.media import MediaError, to_fetched
from adtone.pipeline import pending, process
from adtone.score import FakeScorer, ScoreError, load_rubric
from adtone.synth import jpeg_bytes, toned_image

TODAY = date(2026, 10, 5)


class Resolver:
    name = "static"

    def __init__(self):
        self.calls = []

    def resolve(self, ad_id):
        self.calls.append(ad_id)
        if ad_id == "none":
            return []
        if ad_id == "boom":
            raise MediaError("render page HTTP 500")
        return [f"img/{ad_id}"]

    def close(self):
        pass


IMAGES = {}


def fetch(urls):
    out = []
    for u in urls:
        key = "shared" if u in ("img/a1", "img/a2") else u
        if key not in IMAGES:
            IMAGES[key] = jpeg_bytes(toned_image(0.2 + 0.07 * (len(IMAGES) % 10), seed=len(IMAGES)))
        out.append(to_fetched(IMAGES[key]))
    return out


def seed_ads(rows):
    t = store.ShardedTable(config.ADS_DIR, "ad_id")
    for r in rows:
        t.upsert({"house_id": "alpha", "start": "2026-01-01T00:00:00+0000", **r}, shard="2026-01")
    t.save()
    return store.ShardedTable(config.ADS_DIR, "ad_id")


def run(resolver=None, scorer=None, max_ads=100, instrument_rubric=None):
    rubric = instrument_rubric or load_rubric()
    ads = store.ShardedTable(config.ADS_DIR, "ad_id")
    media = store.ShardedTable(config.MEDIA_DIR, "ad_id")
    vectors = VectorStore("fake", root=config.VEC_DIR)
    return process(ads, media, config.OBS_DIR / "tone-v1.jsonl", vectors, resolver or Resolver(), fetch,
                   FakeEmbedder(), scorer or FakeScorer(rubric), "run1", max_ads=max_ads, today=TODAY)


def test_end_to_end_records_derived_rows_and_never_writes_pixels(tmp_data):
    seed_ads([{"ad_id": "a1", "stop": "2026-02-01"}, {"ad_id": "a2", "stop": "2026-03-01"},
              {"ad_id": "a3"}, {"ad_id": "none", "stop": "2026-01-15"}, {"ad_id": "boom", "stop": "2026-01-20"}])
    scorer = FakeScorer(load_rubric())
    counts = run(scorer=scorer)
    assert counts["processed"] == 5
    assert counts["resolved"] == 3 and counts["no_candidates"] == 1 and counts["error"] == 1
    assert scorer.calls == 2, "a1 and a2 share an image, so it is scored once"
    media = store.ShardedTable(config.MEDIA_DIR, "ad_id").rows
    assert media["a1"]["images"][0]["sha"] == media["a2"]["images"][0]["sha"]
    assert media["boom"]["status"] == "error" and media["boom"]["attempts"] == 1
    obs = store.read_jsonl(config.OBS_DIR / "tone-v1.jsonl")
    assert len(obs) == 2 and all(o["status"] == "ok" and o["instrument"] == "tone-v1@fake" for o in obs)
    assert len(VectorStore("fake", root=config.VEC_DIR).vecs) == 2
    leaked = [p for p in tmp_data.rglob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
    assert leaked == []


def test_rerun_is_idempotent_except_for_retryable_errors(tmp_data):
    seed_ads([{"ad_id": "a1"}, {"ad_id": "none"}, {"ad_id": "boom"}])
    run()
    r = Resolver()
    scorer = FakeScorer(load_rubric())
    counts = run(resolver=r, scorer=scorer)
    assert r.calls == ["boom"] and scorer.calls == 0
    assert store.ShardedTable(config.MEDIA_DIR, "ad_id").get("boom")["attempts"] == 2


def test_ads_closest_to_leaving_the_repository_go_first(tmp_data):
    seed_ads([{"ad_id": "running"}, {"ad_id": "late", "stop": "2026-06-01"}, {"ad_id": "early", "stop": "2025-11-01"},
              {"ad_id": "mid", "stop": "2026-02-01"}])
    r = Resolver()
    run(resolver=r, max_ads=2)
    assert r.calls == ["early", "mid"]


def test_scoring_api_failure_stops_the_run_and_keeps_progress(tmp_data):
    seed_ads([{"ad_id": f"x{i}", "stop": f"2026-0{i + 1}-01"} for i in range(5)])
    rubric = load_rubric()

    class Flaky(FakeScorer):
        def score(self, jpeg):
            if self.calls >= 2:
                raise ScoreError("API: AuthenticationError 401")
            return super().score(jpeg)

    counts = run(scorer=Flaky(rubric))
    assert counts["stopped_on"].startswith("API:")
    media = store.ShardedTable(config.MEDIA_DIR, "ad_id").rows
    assert sorted(media) == ["x0", "x1"]
    assert len(store.read_jsonl(config.OBS_DIR / "tone-v1.jsonl")) == 2
    assert counts["pending_after"] == 3


def test_a_new_instrument_makes_resolved_ads_pending_again(tmp_data):
    seed_ads([{"ad_id": "a3"}])
    run()
    ads = store.ShardedTable(config.ADS_DIR, "ad_id")
    media = store.ShardedTable(config.MEDIA_DIR, "ad_id")
    scored = {(o["sha"], o["instrument"]) for o in store.read_jsonl(config.OBS_DIR / "tone-v1.jsonl")}
    vecs = set(VectorStore("fake", root=config.VEC_DIR).vecs)
    assert pending(ads, media, scored, vecs, "tone-v1@fake", "static", TODAY) == []
    assert [a["ad_id"] for a in pending(ads, media, scored, vecs, "tone-v2@fake", "static", TODAY)] == ["a3"]
