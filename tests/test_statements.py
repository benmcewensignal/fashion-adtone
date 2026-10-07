"""The words reader and the Alignment reading: same questions as the images, checked the same way."""
import json
import re
from datetime import date

import numpy as np
import pytest

from adtone import config, statements, store
from adtone.score import ScoreError, load_rubric


def _words(**kw):
    spec = load_rubric("words-v1").spec
    out = {q: "not_said" for q in spec["enums"]}
    out.update({"mood": [], "street_couture_axis": 0})
    out.update(kw)
    return out


def test_the_words_rubric_is_frozen_and_speaks_the_image_rubric_language():
    w, img = load_rubric("words-v1"), load_rubric("tone-v1")
    for q, answers in w.spec["enums"].items():
        assert answers[-1] == "not_said" and set(answers[:-1]) <= set(img.spec["enums"][q])
        assert all(re.search(rf"\b{a}\b", w.prompt) for a in answers), q
    assert w.spec["lists"]["mood"]["options"] == img.spec["lists"]["mood"]["options"]


class _Client:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return type("R", (), {"content": [type("B", (), {"text": r})()]})()


def test_a_reply_outside_the_rubric_is_retried_once_then_kept_or_refused(tmp_data, monkeypatch):
    good = json.dumps(_words(setting="interior", mood=["austere"], street_couture_axis=4))
    bad = json.dumps({**_words(), "setting": "a cathedral"})
    r = statements.WordsReader(client=_Client([bad, good]), sleep=lambda s: None)
    assert r.read("Gothic saints and humble materials.")["setting"] == "interior" and r.calls == 2
    r2 = statements.WordsReader(client=_Client([bad, bad]), sleep=lambda s: None)
    with pytest.raises(ScoreError):
        r2.read("text")
    assert r.instrument.startswith("words-v1@") and len(r.instrument.split("@")[-1]) == 12


def test_statements_are_read_once_and_a_failing_service_stops_the_run(tmp_data):
    rows = [{"house": "celine", "show_date": "2025-07-06", "season": "SS26", "excerpt": "Quality, timelessness, style."},
            {"house": "margiela", "show_date": "2025-07-09", "season": "AW25", "excerpt": "Flemish architecture."}]
    for r in rows:
        r["key"] = statements.key_of(r)
    ok = json.dumps(_words(styling_register="formal_tailored"))
    reader = statements.WordsReader(client=_Client([ok, ok]), sleep=lambda s: None)
    out = statements.read_all(reader, rows, "r1")
    assert out["read"] == 2 and out["stopped"] is None
    again = statements.read_all(reader, rows, "r2")
    assert again["already"] == 2 and reader.calls == 2

    class Down(Exception):
        status_code = 401
    rows.append({"house": "loewe", "show_date": "2025-10-03", "season": "SS26", "excerpt": "New."})
    rows[-1]["key"] = statements.key_of(rows[-1])
    r3 = statements.WordsReader(client=_Client([Down()]), sleep=lambda s: None)
    out3 = statements.read_all(r3, rows, "r3")
    assert out3["stopped"].startswith("API") and out3["already"] == 2


def _img(house, month, i, **ans):
    base = {"setting": "studio_plain", "people": "one", "mood": ["serene"], "street_couture_axis": 3}
    base.update(ans)
    return {"house": house, "month": month, "sha": f"{house}{month}{i}", "out": base}


def test_images_that_lean_the_way_the_words_do_show_positive_agreement():
    ims = []
    for m in ("2025-07", "2025-09", "2025-12"):
        ims += [_img("celine", m, i, setting="interior", mood=["austere"], street_couture_axis=5) for i in range(4)]
        ims += [_img("gucci", m, i) for i in range(5)] + [_img("prada", m, i) for i in range(5)]
    ims += [_img("celine", "2026-02", i, setting="interior") for i in range(9)]    # after the window
    reading = {"key": "k", "house": "celine", "show_date": "2025-07-06", "season": "SS26",
               "output": _words(setting="interior", mood=["austere"], street_couture_axis=5)}
    row = statements.align([reading], ims, n_perm=199)[0]
    assert row["months"] == "2025-07 to 2025-12" and row["n_house"] == 12 and row["n_peers"] == 30
    lifts = {(q["question"], q["stated"]): q["lift"] for q in row["questions"]}
    assert lifts[("setting", "interior")] == 1.0 and lifts[("mood", "austere")] == 1.0
    assert lifts[("street_couture_axis", 5)] == 2.0
    assert row["agreement"] == 1.0 and row["p_two_sided"] < 0.01
    thin = statements.align([{**reading, "house": "loewe"}], ims, n_perm=9)[0]
    assert "needed" in thin["note"]


def test_the_open_model_reads_words_under_the_same_grammar(tmp_data):
    from adtone.score import gbnf
    g = gbnf(load_rubric("words-v1"))
    assert '"[" (' in g and "not_said" in g          # the mood list may be empty
    good = json.dumps(_words(production="editorial_art", mood=[]))
    seen = []

    def remote(texts, system, schema, grammar):
        seen.append((texts, grammar))
        return [good, '{"setting": "nowhere"}'][:len(texts)]
    r = statements.ModalWordsReader(remote=remote, revision="cc594898137f0000")
    assert r.instrument == "words-v1@qwen2.5-vl-7b-instruct@cc594898137f"
    out = r.read_many(["first text", "second text"])
    assert out[0]["production"] == "editorial_art" and isinstance(out[1], ScoreError)
    assert seen[0][1] == g

    def down(*a):
        raise ConnectionError("x")
    with pytest.raises(ScoreError, match="API"):
        statements.ModalWordsReader(remote=down, revision="cc594898137f").read("t")
