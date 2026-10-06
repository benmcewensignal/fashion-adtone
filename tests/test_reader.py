import io
import json

import pytest
from PIL import Image

from adtone import config, guard, reader_pilot
from adtone.score import FakeScorer, ModalScorer, ScoreError, gbnf, json_schema, load_rubric, make_scorer, validate

REV = "0123456789abcdef0123456789abcdef01234567"


def _jpeg(color=(120, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (64, 80), color).save(buf, "JPEG")
    return buf.getvalue()


def test_the_schema_is_the_rubric_exactly():
    r = load_rubric()
    s = json_schema(r)
    assert sorted(s["required"]) == sorted(r.keys) and s["additionalProperties"] is False
    for k, allowed in r.spec["enums"].items():
        assert s["properties"][k]["enum"] == list(allowed)
    sample = {}
    for k, p in s["properties"].items():
        if p["type"] == "string":
            sample[k] = p["enum"][-1]
        elif p["type"] == "array":
            sample[k] = p["items"]["enum"][:p["minItems"]]
        else:
            sample[k] = p["minimum"]
    sample["people"], sample["gaze"] = "one", sample["gaze"]
    assert validate(sample, r) == sample


def test_the_modal_reader_parses_good_replies_and_records_bad_ones_as_invalid():
    r = load_rubric()
    good = json.dumps(FakeScorer(r).score(_jpeg()))
    calls = []

    def remote(jpegs, system, schema, grammar):
        calls.append((len(jpegs), system == r.prompt, schema == json_schema(r), grammar == gbnf(r)))
        return [good, '{"light": "neon"}']
    s = ModalScorer(r, remote=remote, revision=REV)
    out = s.score_many([_jpeg(), _jpeg((0, 0, 0))])
    assert isinstance(out[0], dict) and isinstance(out[1], ScoreError)
    assert calls == [(2, True, True, True)] and s.instrument == f"{r.version}@qwen2.5-vl-7b-instruct@{REV[:12]}"
    with pytest.raises(ScoreError):
        ModalScorer(r, remote=lambda *a: ['{"light": "neon"}'], revision=REV).score(_jpeg())


def test_a_modal_outage_stops_the_pipeline_rather_than_marking_ads_invalid():
    def down(*a):
        raise ConnectionError("modal down")
    with pytest.raises(ScoreError, match="^API: Modal ConnectionError"):
        ModalScorer(load_rubric(), remote=down, revision=REV).score(_jpeg())


def test_an_unpinned_reader_refuses_to_run(monkeypatch, tmp_path):
    monkeypatch.delenv("ADTONE_OPEN_MODEL_REVISION", raising=False)
    monkeypatch.setattr(config, "READER_STATE", tmp_path / "reader.json")
    with pytest.raises(ScoreError, match="not pinned"):
        make_scorer(load_rubric(), "modal")
    assert config.instrument("modal").endswith("@unpinned")


def test_the_instrument_is_named_by_reader_and_weights(monkeypatch, tmp_path):
    st = tmp_path / "reader.json"
    st.write_text(json.dumps({"model": config.OPEN_MODEL, "revision": REV}))
    monkeypatch.delenv("ADTONE_OPEN_MODEL_REVISION", raising=False)
    monkeypatch.setattr(config, "READER_STATE", st)
    assert config.instrument("modal") == f"{config.RUBRIC_VERSION}@qwen2.5-vl-7b-instruct@{REV[:12]}"
    assert config.instrument("claude") == f"{config.RUBRIC_VERSION}@{config.CLAUDE_MODEL}"
    st.write_text(json.dumps({"model": "other/model", "revision": REV}))
    assert config.instrument("modal").endswith("@unpinned")   # a pin for different weights does not count


def test_a_recorded_pin_is_never_changed_without_force(tmp_path):
    path = tmp_path / "reader.json"
    log = f'noise\nADTONE_PIN {{"model": "{config.OPEN_MODEL}", "revision": "{REV}"}}\n'
    assert reader_pilot.record_pin(log, path=path)["revision"] == REV
    other = REV[::-1]
    with pytest.raises(SystemExit, match="needs --force"):
        reader_pilot.record_pin(log.replace(REV, other), path=path)
    assert reader_pilot.record_pin(log.replace(REV, other), force=True, path=path)["revision"] == other
    with pytest.raises(SystemExit, match="not a commit hash"):
        reader_pilot.record_pin('ADTONE_PIN {"model": "m", "revision": "main"}', path=path)


def test_pilot_cards_are_drawn_jpegs_at_ad_size():
    cards = reader_pilot.synthetic_jpegs(3)
    assert len(cards) == 3 and Image.open(io.BytesIO(cards[0])).size == (1080, 1350)


def test_the_pilot_separates_cold_start_and_prices_warm_throughput():
    r = load_rubric()
    good = json.dumps(FakeScorer(r).score(_jpeg()))
    ticks = iter([0.0, 90.0, 90.0, 98.0, 98.0, 106.0])   # a 90 s cold batch, then 8 s per batch of 8

    out = reader_pilot.pilot(lambda b, p, s, g: [good] * len(b), r, "L4", n=24, batch=8, clock=lambda: next(ticks))
    assert out["cold_start_s"] == 90.0 and out["warm_seconds_per_image"] == 1.0 and out["valid_share"] == 1.0
    assert out["cost_per_10k_images_usd"] == pytest.approx(1.0 * 0.000222 * 10_000, abs=0.01)


def test_the_guard_knows_the_reader_and_the_newer_results():
    assert guard.violations("reader", ["data/state/reader.json", "data/provenance/reader.jsonl"]) == []
    assert guard.violations("reader", ["data/obs/tone-v1.jsonl"])
    assert guard.violations("analyse", ["data/results/family.json", "data/results/runway.json"]) == []


def _sample(grammar: str, rng) -> str:
    """Generate one string from the grammar subset gbnf() writes: literals, alternation, nested optional
    groups and the character class [0-9]."""
    import re
    rules = {}
    for line in grammar.strip().splitlines():
        name, body = line.split(" ::= ", 1)
        rules[name] = body
    tok = re.compile(r'"(?:\\.|[^"\\])*"|\[0-9\]|\(|\)\?|\||[A-Za-z0-9_]+')

    def expand(body):
        toks = tok.findall(body)
        def seq(i, stop):
            alts, cur = [], []
            while i < len(toks) and toks[i] not in stop:
                t = toks[i]
                if t == "|":
                    alts.append(cur); cur = []; i += 1
                elif t == "(":
                    inner, i = seq(i + 1, (")?",))
                    i += 1
                    cur.append(inner if rng.random() < 0.5 else "")
                elif t == "[0-9]":
                    cur.append(str(rng.integers(0, 10))); i += 1
                elif t.startswith('"'):
                    cur.append(t[1:-1].replace('\\"', '"')); i += 1
                else:
                    cur.append(expand(rules[t])); i += 1
            alts.append(cur)
            return "".join(alts[int(rng.integers(0, len(alts)))]), i
        return seq(0, ())[0]
    return expand(rules["root"])


def test_every_reply_the_grammar_allows_passes_the_rubric():
    import numpy as np
    r = load_rubric()
    g = gbnf(r)
    rng = np.random.default_rng(5)
    for _ in range(300):
        text = _sample(g, rng)
        assert " " not in text and "\n" not in text
        obj = json.loads(text)
        if obj["people"] == "none":
            obj["gaze"] = "not_applicable"     # the one rule a grammar cannot see; validation enforces it
        ok = dict(obj)
        try:
            validate(ok, r)
        except ScoreError as e:
            assert "repeats a value" in str(e), e   # list uniqueness is also left to validation
            continue


def test_a_reader_that_will_not_start_still_leaves_a_record():
    def broken(*a):
        raise RuntimeError("no decoding mode worked: grammar: ValueError")
    out = reader_pilot.pilot(broken, load_rubric(), "L4", n=8, batch=8)
    assert out["failure"].startswith("RuntimeError") and out["valid"] == 0 and out["cost_per_10k_images_usd"] is None


def test_the_pilot_records_why_replies_stopped():
    r = load_rubric()
    good = json.dumps(FakeScorer(r).score(_jpeg()))
    meta = lambda b, p, s, g: [{"text": good, "finish_reason": "stop", "tokens": 210, "mode": "grammar",
                               "mode_errors": []}] * len(b)
    out = reader_pilot.pilot(meta, r, "L4", n=16, batch=8)
    assert out["finish_reasons"] == {"stop": 16} and out["modes"] == {"grammar": 16} and out["tokens_max"] == 210
    assert out["sample_replies"][0]["head"].startswith("{")
