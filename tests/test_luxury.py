"""The luxury reading: the corpus of every homepage picture, and fetching it again."""
import hashlib
import json
import io
import json

import numpy as np
from PIL import Image

from adtone import bakeoff as B
from adtone import homepages, luxury as L
from adtone import store


def _capture(house, month, ts, shas, page=None):
    return {"house_id": house, "month": month, "capture": ts, "status": "resolved", "url": f"https://{house}.com/",
            "page": page or f"https://{house}.com/en", "images": [{"sha": s, "w": 10, "h": 10} for s in shas]}


def test_the_corpus_takes_each_picture_once_where_it_was_first_shown(tmp_path, monkeypatch):
    cap, obs = tmp_path / "captures", tmp_path / "obs"
    cap.mkdir()
    obs.mkdir()
    store.write_jsonl(cap / "a.jsonl", [_capture("a", "2024-01", "20240105", ["x", "y"]),
                                         _capture("a", "2024-02", "20240203", ["x"]),
                                         _capture("a", "2024-03", "20240301", ["x", "z"]),
                                         _capture("a", "2024-04", "20240402", ["x"]),
                                         {"house_id": "a", "month": "2024-05", "status": "no_images"}])
    store.write_jsonl(obs / "tone-v1.jsonl", [{"sha": "x", "status": "ok", "output": {"creative_type": "brand_image"}},
                                              {"sha": "y", "status": "ok", "output": {"creative_type": "product_packshot"}}])
    monkeypatch.setattr(homepages, "paths", lambda: {"captures": cap, "obs": obs})
    monkeypatch.setattr(L, "DIR", tmp_path / "luxury")
    monkeypatch.setattr(L, "PROV", tmp_path / "prov.jsonl")
    c = L.corpus()
    by = {p["sha"]: p for p in c["pictures"]}
    assert set(by) == {"x", "y", "z"} and c["pages"] == 2
    assert by["x"]["month"] == "2024-01" and by["x"]["capture"] == "20240105" and by["x"]["shown"] == 4
    assert by["x"]["also"] == [["20240203", "https://a.com/en"], ["20240301", "https://a.com/en"]]
    assert by["x"]["kind"] == "campaign" and by["y"]["kind"] == "packshot" and by["z"]["kind"] is None
    assert json.loads((tmp_path / "luxury" / "corpus.json").read_text())["brands"] == ["a"]


def _jpg(rgb):
    b = io.BytesIO()
    Image.new("RGB", (300, 400), rgb).save(b, format="JPEG")
    return b.getvalue()


def test_a_picture_gone_where_it_was_first_shown_is_sought_where_it_was_shown_later(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "PROV", tmp_path / "prov.jsonl")
    img = _jpg((200, 30, 30))
    sha = hashlib.sha256(img).hexdigest()
    pages = {"/web/1/": "<html><body><img src=\"https://x.com/other.jpg\"></body></html>",
             "/web/2/": "<html><body><img src=\"https://x.com/pic.jpg\"></body></html>"}

    class R:
        def __init__(self, code, content=b"", text="", url=""):
            self.status_code, self.content, self.text, self.url = code, content, text, url

    class S:
        headers = {}

        def get(self, url, timeout=0):
            for k, html in pages.items():
                if k in url and not url.endswith(".jpg"):
                    return R(200, text=html, url=url)
            if url.endswith("pic.jpg"):
                return R(200, content=img)
            if url.endswith("other.jpg"):
                return R(200, content=_jpg((0, 0, 0)))
            return R(404)
    s = {"pictures": [{"sha": sha, "capture": "1", "page": "https://x.com/", "also": [["2", "https://x.com/"]]}]}
    rows = B.fetch(s, pause=0, workers=1, session_factory=S, out=tmp_path / "p.jsonl", local=tmp_path / "l",
                   thumbs=False)
    assert rows[0]["found"] and rows[0]["capture"] == "2"
    assert (tmp_path / "l" / "read" / f"{sha}.jpg").exists() and not (tmp_path / "l" / "thumb").exists()
    one = B.fetch(s, pause=0, workers=1, session_factory=S, out=tmp_path / "p.jsonl", local=tmp_path / "l",
                  thumbs=False, rounds=1, present=set())
    assert not one[0]["found"]       # its copy is not known to be kept, and the first place no longer has it


def test_fetching_the_corpus_copies_the_bakeoffs_pictures_and_keeps_what_it_finds(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "DIR", tmp_path / "luxury")
    monkeypatch.setattr(L, "LOCAL", tmp_path / "local")
    monkeypatch.setattr(L, "PROV", tmp_path / "prov.jsonl")
    monkeypatch.setattr(B, "DIR", tmp_path / "bakeoff")
    (tmp_path / "luxury").mkdir()
    (tmp_path / "bakeoff").mkdir()
    corpus = {"pictures": [{"sha": s, "capture": "1", "page": "p"} for s in ("a", "b", "c")], "brands": [], "pages": 1}
    (tmp_path / "luxury" / "corpus.json").write_text(json.dumps(corpus))
    store.write_jsonl(tmp_path / "bakeoff" / "pictures.jsonl", [{"sha": "a", "found": True, "pixel": {}},
                                                                {"sha": "z", "found": True, "pixel": {}}])
    uploaded = []
    monkeypatch.setattr(B, "on_volume", lambda vol_dir: {"c"})
    monkeypatch.setattr(B, "from_volume", lambda shas, vol_dir=B.VOL_DIR: {s: b"jpg" for s in shas})
    monkeypatch.setattr(B, "to_volume", lambda vol_dir, files, local=None: uploaded.append((vol_dir, sorted(f.stem for f in files))) or len(files))
    seen = {}

    def fake_fetch(c, **kw):
        seen.update(kw)
        prev = {r["sha"]: r for r in store.read_jsonl(kw["out"])}
        (kw["local"] / "read" / "b.jpg").write_bytes(b"jpg")
        return [prev.get(p["sha"]) or {"sha": p["sha"], "found": p["sha"] == "b"} for p in c["pictures"]]
    monkeypatch.setattr(B, "fetch", fake_fetch)
    rows = L.fetch()
    assert [r["found"] for r in rows] == [True, True, False]
    assert uploaded == [("/pictures-v1", ["a"]), ("/pictures-v1", ["b"])]
    assert seen["present"] == {"a", "c"} and seen["thumbs"] is False


def _corpus(tmp_path, monkeypatch, n_brands=4, per=12):
    monkeypatch.setattr(L, "DIR", tmp_path / "luxury")
    monkeypatch.setattr(L, "PROV", tmp_path / "prov.jsonl")
    (tmp_path / "luxury").mkdir()
    pics = [{"sha": f"{h:02x}{i:02x}" * 8, "house": f"h{h}", "month": "2024-01", "capture": "1", "page": "p"}
            for h in range(n_brands) for i in range(per)]
    (tmp_path / "luxury" / "corpus.json").write_text(json.dumps({"pictures": pics}))
    store.write_jsonl(tmp_path / "luxury" / "pictures.jsonl", [{"sha": p["sha"], "found": i % 7 != 3, "pixel": {}}
                                                               for i, p in enumerate(pics)])
    return pics


def test_the_design_and_the_check_set(tmp_path, monkeypatch):
    pics = _corpus(tmp_path, monkeypatch)
    got = L.found()
    assert len(got) == sum(1 for i in range(len(pics)) if i % 7 != 3)
    d = L.design()
    assert set(d["reader"]) == {"opulent", "intimate", "staged", "contemporary", "provocative"}
    deg = {}
    for a, b in d["reader"]["opulent"]:
        deg[a] = deg.get(a, 0) + 1
        deg[b] = deg.get(b, 0) + 1
    assert set(deg) == set(got) and min(deg.values()) == max(deg.values()) == B.DEGREE
    assert L.design() == d                      # made once and kept
    c = L.check_set()
    by = {}
    for s in c["pictures"]:
        by[got[s]["house"]] = by.get(got[s]["house"], 0) + 1
    assert set(by) == {"h0", "h1", "h2", "h3"} and all(v >= 1 for v in by.values())
    assert all(len(c["pairs"][ax]) == len(d["reader"][ax]) // L.CHECK_PAIRS for ax in d["reader"])
    assert L.check_set() == c                   # the same set every time


def test_reading_on_modal_resumes_and_keeps_what_came_back(tmp_path, monkeypatch):
    import modal
    _corpus(tmp_path, monkeypatch)
    hidden = {s: i for i, s in enumerate(sorted(L.found()))}
    calls = []

    fail = {"first": {1}, "again": set()}     # batches lost on the first pass and on the pass that asks again
    passes = []

    class Method:
        def __init__(self, fn):
            self.fn = fn

        def starmap(self, gen, return_exceptions=False):
            key = "again" if passes else "first"
            passes.append(key)
            for k, args in enumerate(gen):
                calls.append(args)
                yield RuntimeError("lost") if k in fail[key] else self.fn(*args)

    class Fake:
        compare_from = Method(lambda folder, pairs, system, prompts, choices:
                              ["first" if hidden[a] > hidden[b] else "second" for a, b in pairs])
    monkeypatch.setattr(modal.Cls, "from_name", lambda app, name: (lambda: Fake()))
    total = len(L._both_orders(L.design()["reader"]))
    r1 = L.read("qwen3", "pairs")
    assert passes == ["first", "again"]
    assert r1["pairs"]["asked"] == total and r1["pairs"]["rows"] == total          # the lost batch, asked again
    assert all(a[0] == "/pictures-v1" for a in calls)
    passes.clear()
    assert L.read("qwen3", "pairs")["pairs"]["asked"] == 0 and passes == []      # nothing read twice
    (L.DIR / "readings" / "qwen3-pairs.jsonl").write_text("")
    fail.update(first={1}, again={0})          # lost twice: logged, and left for the next run
    passes.clear()
    r2 = L.read("qwen3", "pairs")
    assert r2["pairs"]["rows"] == total - 64
    passes.clear()
    fail.update(first=set(), again=set())
    r3 = L.read("qwen3", "pairs")
    assert r3["pairs"]["asked"] == 64 and r3["pairs"]["rows"] == 64
    info = L.positions("qwen3")
    assert info["opulent"]["pictures"] == len(hidden)
    pos = {r["sha"]: r["opulent"] for r in store.read_jsonl(L.DIR / "positions-qwen3.jsonl")}
    xs = sorted(hidden, key=hidden.get)
    assert np.corrcoef([hidden[s] for s in xs], [pos[s] for s in xs])[0, 1] > 0.9


def test_the_new_instrument_is_loaded_as_readings_takes_it(tmp_path, monkeypatch):
    cap, obs = tmp_path / "captures", tmp_path / "obs"
    cap.mkdir()
    obs.mkdir()
    store.write_jsonl(cap / "a.jsonl", [_capture("a", "2024-01", "1", ["x", "y"]), _capture("a", "2024-02", "2", ["x", "z"])])
    monkeypatch.setattr(homepages, "paths", lambda: {"captures": cap, "obs": obs, "vectors": tmp_path / "none"})
    monkeypatch.setattr(L, "DIR", tmp_path / "luxury")
    (tmp_path / "luxury" / "readings").mkdir(parents=True)
    store.write_jsonl(tmp_path / "luxury" / "readings" / "qwen3-tone.jsonl",
                      [{"sha": "x", "error": "bad"}, {"sha": "x", "out": {"creative_type": "brand_image"}},
                       {"sha": "y", "out": {"creative_type": "product_on_model"}}])
    store.write_jsonl(tmp_path / "luxury" / "positions-qwen3.jsonl",
                      [{"sha": "x", "opulent": 1.0, "staged": 0.0}, {"sha": "y", "opulent": -1.0, "staged": 2.0},
                       {"sha": "z", "opulent": 0.0, "staged": None}])
    ims = L.load_images("qwen3", "positions")
    assert [(i["month"], i["sha"], i["type"]) for i in ims] == [("2024-01", "x", "campaign"), ("2024-01", "y", "on_model"),
                                                               ("2024-02", "x", "campaign")]
    assert abs(ims[0]["vec"][0] - 1.0) < 1e-9 and abs(ims[1]["vec"][0] + 1.0) < 1e-9       # standard units
    assert ims[0]["dup"] is None and ims[0]["period"] == "2024H1"
    assert len(L.load_images("qwen3", "positions", axes=["opulent"])) == 3


def test_the_standing_check_compares_the_two_readers_on_the_same_work(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch)
    rd = tmp_path / "luxury" / "readings"
    rd.mkdir()
    cs = L.check_set()
    shas = cs["pictures"]
    store.write_jsonl(rd / "qwen3-tone.jsonl", [{"sha": s, "out": {"creative_type": "brand_image", "light": "high_key"}} for s in shas])
    store.write_jsonl(rd / "claude-tone.jsonl", [{"sha": s, "out": {"creative_type": "brand_image", "light": "high_key" if i % 2 else "low_key"}}
                                                 for i, s in enumerate(shas)])
    rows_q, rows_c = [], []
    for ax, es in cs["pairs"].items():
        for i, (a, b) in enumerate(es):
            rows_q += [{"axis": ax, "first": a, "second": b, "answer": "first"}, {"axis": ax, "first": b, "second": a, "answer": "second"}]
            agree = i % 4 != 0
            rows_c += [{"axis": ax, "first": a, "second": b, "answer": "first" if agree else "second"},
                       {"axis": ax, "first": b, "second": a, "answer": "second" if agree else "first"}]
    store.write_jsonl(rd / "qwen3-pairs.jsonl", rows_q)
    store.write_jsonl(rd / "claude-pairs.jsonl", rows_c)
    r = L.check_report()
    assert r["pictures"] == len(shas)
    assert r["tone_kappa"]["light"] is None or r["tone_kappa"]["light"] <= 0.01
    for ax, v in r["pairs"].items():
        n = v["comparisons"]
        assert v["both_decided"] == n and abs(v["agree"] - sum(i % 4 != 0 for i in range(n)) / n) < 1e-3
        assert v["open_reader_split"] == 0 and v["claude_split"] == 0


def test_inside_brands_the_kind_of_picture_and_noise_are_told_apart():
    rng = np.random.default_rng(4)
    ims = []
    for h in range(6):
        base = rng.normal()
        for m in range(1, 13):
            month = f"2024-{m:02d}"
            for i, kind in enumerate(("campaign", "on_model", "packshot")):
                v = base + {"campaign": 1.0, "on_model": 0.0, "packshot": -1.0}[kind] + rng.normal(scale=0.3)
                ims.append({"house": f"b{h}", "month": month, "sha": f"{h}-{m}-{i}", "type": kind,
                            "period": "2024H1" if m <= 6 else "2024H2", "vec": np.array([v]), "dup": None})
    r = L.within_brands(ims, ["x"])
    x = r["measures"]["x"]
    assert x["inside_by_kind"] > 0.8 and x["inside_by_half_year"] < 0.05 and x["inside_brands"] > 0.3


def test_comparisons_read_as_probabilities_are_judged_with_the_lean_taken_out(tmp_path, monkeypatch):
    rng = np.random.default_rng(9)
    monkeypatch.setattr(L, "DIR", tmp_path / "luxury")
    monkeypatch.setattr(B, "DIR", tmp_path / "bakeoff")
    (tmp_path / "bakeoff").mkdir()
    (tmp_path / "luxury" / "bakeoff").mkdir(parents=True)
    pics = [{"sha": f"{h}x{i}", "house": f"h{h}", "kind": "campaign", "side": "early"} for h in range(6) for i in range(10)]
    crops = [[pics[0]["sha"], pics[1]["sha"]], [pics[10]["sha"], pics[11]["sha"]], [pics[20]["sha"], pics[21]["sha"]],
             [pics[30]["sha"], pics[31]["sha"]], [pics[40]["sha"], pics[41]["sha"]], [pics[50]["sha"], pics[51]["sha"]]]
    (tmp_path / "bakeoff" / "sample.json").write_text(json.dumps({"pictures": pics, "crop_pairs": crops}))
    store.write_jsonl(tmp_path / "bakeoff" / "pictures.jsonl", [{"sha": p["sha"], "found": True} for p in pics])
    truth = {p["sha"]: int(p["house"][1:]) * 0.5 + rng.normal(scale=0.5) for p in pics}
    for a, b in crops:
        truth[b] = truth[a] + rng.normal(scale=0.05)
    edges = [(pics[i]["sha"], pics[j]["sha"]) for i in range(60) for j in range(i + 1, 60) if (j - i) % 7 in (1, 3)]
    human = [{"id": f"opulent-{k:02d}", "axis": "opulent", "left": a, "right": b} for k, (a, b) in enumerate(edges[:30])]
    (tmp_path / "bakeoff" / "pairs.json").write_text(json.dumps({"human": human, "reader": {"opulent": [list(e) for e in edges]}}))
    (tmp_path / "bakeoff" / "human.json").write_text(json.dumps({h["id"]: ("left" if truth[h["left"]] > truth[h["right"]] else "right") for h in human}))
    rows = []
    for a, b in edges:
        for x, y in ((a, b), (b, a)):
            z = 2.0 * (truth[x] - truth[y]) - 1.5         # a strong lean to the second picture
            rows.append({"axis": "opulent", "first": x, "second": y, "p_first": float(1 / (1 + np.exp(-z)))})
    store.write_jsonl(tmp_path / "luxury" / "bakeoff" / "qwen3-probs.jsonl", rows)
    r = L.probs_report("qwen3")
    ax = r["axes"]["opulent"]
    assert r["lean_to_first"] < -0.1 and ax["orders_agree_after_lean"] > 0.95
    assert ax["with_person"]["agreement"] > 0.95 and ax["with_person_by_position"] > 0.9
    assert ax["crop_icc"] > 0.8 and ax["between_brands"] > 0.3


def test_nothing_to_read_never_reaches_modal(tmp_path, monkeypatch):
    import modal
    monkeypatch.setattr(modal.Cls, "from_name", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    assert L._on_modal("qwen3", "compare_from", [], None, None, tmp_path / "x.jsonl", 0) == 0


def test_the_plan_decides_which_axes_are_read_and_how(tmp_path, monkeypatch):
    import modal
    _corpus(tmp_path, monkeypatch)
    hidden = {s: i for i, s in enumerate(sorted(L.found()))}
    calls = []

    class Method:
        def __init__(self, fn):
            self.fn = fn

        def starmap(self, gen, return_exceptions=False):
            for args in gen:
                calls.append(args)
                yield self.fn(*args)

    def probs(folder, pairs, system, prompts):     # a reader that leans hard to the second picture
        return [float(1 / (1 + np.exp(-(0.3 * (hidden[a] - hidden[b]) - 2.0)))) for a, b in pairs]

    class Fake:
        compare_probs_from = Method(probs)
        compare_from = Method(lambda *a: (_ for _ in ()).throw(AssertionError("answers asked for")))
    monkeypatch.setattr(modal.Cls, "from_name", lambda app, name: (lambda: Fake()))
    (L.DIR / "plan.json").write_text(json.dumps({"comparisons": "probs", "axes": ["provocative"]}))
    r = L.read("qwen3", "comparisons")
    n = len(L.design()["reader"]["provocative"])
    assert r["probs"] == {"asked": 2 * n, "rows": 2 * n}
    rows = store.read_jsonl(L.DIR / "readings" / "qwen3-probs.jsonl")
    assert {x["axis"] for x in rows} == {"provocative"} and all(0 <= x["p_first"] <= 1 for x in rows)
    assert L.read("qwen3", "comparisons")["probs"] == {"asked": 0, "rows": 0}        # nothing read twice
    info = L.positions("qwen3")
    assert set(info) == {"provocative"} and info["provocative"]["first_bias"] < -1
    pos = {x["sha"]: x["provocative"] for x in store.read_jsonl(L.DIR / "positions-qwen3.jsonl")}
    xs = sorted(hidden, key=hidden.get)
    assert np.corrcoef([hidden[s] for s in xs], [pos[s] for s in xs])[0, 1] > 0.9
    w = L._winners("qwen3")
    decided = [(k, v) for k, v in w.items() if v is not None]
    assert len(decided) > 0.6 * len(w)
    assert all(v == max(k[1], key=hidden.get) for k, v in decided)               # the lean taken out, the right picture
    (L.DIR / "plan.json").write_text(json.dumps({"comparisons": "probs", "axes": []}))
    assert L.read("qwen3", "comparisons") == {"comparisons": "the plan keeps no axis"}


# ---------- the new instrument version on the bake-off's pictures ----------

def _v2_frame(tmp_path, monkeypatch, n_houses=6, per=10, crops_per_house=2):
    """A bake-off of n_houses x per pictures with crop pairs, as the v2 test reads it."""
    monkeypatch.setattr(L, "DIR", tmp_path / "luxury")
    monkeypatch.setattr(L, "PROV", tmp_path / "prov.jsonl")
    monkeypatch.setattr(B, "DIR", tmp_path / "bakeoff")
    (tmp_path / "bakeoff").mkdir()
    (tmp_path / "luxury" / "bakeoff").mkdir(parents=True)
    pics = [{"sha": f"{h}x{i}", "house": f"h{h}", "kind": "campaign", "side": "early"} for h in range(n_houses) for i in range(per)]
    crops = [[f"{h}x{2 * k}", f"{h}x{2 * k + 1}"] for h in range(n_houses) for k in range(crops_per_house)]
    (tmp_path / "bakeoff" / "sample.json").write_text(json.dumps({"pictures": pics, "crop_pairs": crops}))
    store.write_jsonl(tmp_path / "bakeoff" / "pictures.jsonl", [{"sha": p["sha"], "found": True} for p in pics])
    return pics, crops


def test_tone_v2_is_judged_by_its_own_rule_and_person_questions_on_pictures_of_a_person(tmp_path, monkeypatch):
    pics, crops = _v2_frame(tmp_path, monkeypatch)
    (tmp_path / "bakeoff" / "pairs.json").write_text(json.dumps({"human": [], "reader": {}}))
    rng = np.random.default_rng(3)
    partner = {b: a for a, b in crops}
    out = {}
    for p in pics:
        h, i = int(p["house"][1:]), int(p["sha"].split("x")[1])
        person = i < 8                                    # crop pairs are 0-1 and 2-3: both crops show a person
        o = {"creative_type": "product_on_model" if i == 9 else "brand_image",
             "people": "one" if person else "none",
             "light": ["high_key", "low_key", "natural_daylight"][h % 3],       # a brand's look, crops agree
             "rhetoric": "juxtaposition" if (h, i) == (0, 9) else "none",       # almost always one answer
             "gaze": ["to_camera", "away"][(i // 2 + h) % 2] if person else "not_applicable",
             "head_cant": str(rng.choice(["yes", "no"])) if person else "not_applicable",   # crops disagree by chance
             "mood": ["serene"], "street_couture_axis": 3}
        out[p["sha"]] = o
    for b, a in partner.items():                        # a crop is read as its picture, except for chance answers
        out[b] = {**out[a], "head_cant": out[b]["head_cant"]}
    store.write_jsonl(tmp_path / "luxury" / "bakeoff" / "qwen3-tone-v2.jsonl",
                      [{"sha": s, "version": "tone-v2", "out": o} for s, o in out.items()])
    r = L.tone_report("qwen3", "tone-v2")
    q = r["questions"]
    assert r["pictures"] == 60 and r["showing_a_person"] == 48
    assert q["light"]["keep"] and q["light"]["crop_kappa"] == 1.0
    assert not q["rhetoric"]["keep"] and q["rhetoric"]["top_share"] > 0.9
    assert q["gaze"]["on"] == "pictures showing a person" and q["gaze"]["pictures"] == 48 and q["gaze"]["keep"]
    assert q["gaze"]["crop_pairs"] == 12
    assert not q["head_cant"]["keep"]
    assert not q["mood"]["keep"] and not q["street_couture_axis"]["keep"]      # one answer for every picture
    assert not q["vertical_angle"]["keep"]                                   # never answered
    assert r["on_model_without_a_person"] == 6


def test_a_question_with_too_few_crop_pairs_is_not_shown_to_be_reliable(tmp_path, monkeypatch):
    pics, crops = _v2_frame(tmp_path, monkeypatch, crops_per_house=1)       # six crop pairs
    (tmp_path / "bakeoff" / "pairs.json").write_text(json.dumps({"human": [], "reader": {}}))
    store.write_jsonl(tmp_path / "luxury" / "bakeoff" / "qwen3-tone-v2.jsonl",
                      [{"sha": p["sha"], "out": {"people": "one", "light": ["high_key", "low_key"][int(p["house"][1:]) % 2]}}
                       for p in pics])
    q = L.tone_report("qwen3", "tone-v2")["questions"]["light"]
    assert q["crop_pairs"] == 6 and q["crop_kappa"] is None and not q["keep"]


def test_composition_measures_go_forward_when_brands_differ_and_the_whole_is_written(tmp_path, monkeypatch):
    pics, crops = _v2_frame(tmp_path, monkeypatch, per=12)
    rng = np.random.default_rng(5)
    partner = {b: a for a, b in crops}
    rows = {}
    for p in pics:
        h = int(p["house"][1:])
        rows[p["sha"]] = {"sha": p["sha"], "version": "composition-v1", "open_space": round(0.1 * h + 0.02 * rng.normal(), 4),
                          "symmetry": round(float(rng.normal()), 4), "thirds": 0.5, "figure_size": None}
    for b, a in partner.items():
        rows[b] = {**rows[a], "sha": b, "symmetry": rows[b]["symmetry"]}
    store.write_jsonl(tmp_path / "luxury" / "composition-v1.jsonl",
                      list(rows.values()) + [{"sha": "0x0", "error": "not on the volume"}])
    tone = {s: {"open_space": ["little", "some", "much"][min(2, int(r["open_space"] / 0.2))], "people": "none"}
            for s, r in rows.items()}
    store.write_jsonl(tmp_path / "luxury" / "bakeoff" / "qwen3-tone-v2.jsonl", [{"sha": s, "out": o} for s, o in tone.items()])
    # pairs-v2 on one axis, a strong and honest reader, and the person's pairs on it
    truth = {p["sha"]: int(p["house"][1:]) * 0.5 + rng.normal(scale=0.5) for p in pics}
    for b, a in partner.items():
        truth[b] = truth[a] + rng.normal(scale=0.05)
    edges = [(pics[i]["sha"], pics[j]["sha"]) for i in range(72) for j in range(i + 1, 72) if (j - i) % 7 in (1, 3)]
    human = [{"id": f"opulent-{k:02d}", "axis": "opulent", "left": a, "right": b} for k, (a, b) in enumerate(edges[:30])]
    (tmp_path / "bakeoff" / "pairs.json").write_text(json.dumps({"human": human, "reader": {"opulent": [list(e) for e in edges]}}))
    (tmp_path / "bakeoff" / "human.json").write_text(json.dumps({h["id"]: ("left" if truth[h["left"]] > truth[h["right"]] else "right") for h in human}))
    probs = [{"axis": "opulent", "first": x, "second": y, "p_first": float(1 / (1 + np.exp(-(2.0 * (truth[x] - truth[y]) - 1.0))))}
             for a, b in edges for x, y in ((a, b), (b, a))]
    store.write_jsonl(tmp_path / "luxury" / "bakeoff" / "qwen3-probs-v2.jsonl", probs)
    c = L.composition_report()["measures"]
    assert c["open_space"]["keep"] and c["open_space"]["p_adjusted"] < 0.05 and c["open_space"]["crop_r"] > 0.9
    assert c["open_space"]["with_reader"]["spearman"] > 0.8
    assert not c["symmetry"]["keep"] and not c["thirds"]["keep"] and c["thirds"]["iqr"] == 0
    assert not c["figure_size"]["keep"] and c["figure_size"]["pictures"] == 0
    rep = L.v2_report()
    assert rep["kept"]["axes"] == ["opulent"] and rep["pairs"]["axes"]["opulent"]["keep"]
    assert "open_space" in rep["kept"]["measures"] and "symmetry" not in rep["kept"]["measures"]
    assert rep["kept"]["questions"] == ["open_space"]                      # the only tone answer here that varies
    assert json.loads((tmp_path / "luxury" / "bakeoff_v2.json").read_text())["instrument"]["tone"] == "tone-v2"


def test_composition_is_measured_from_the_volume_bakeoff_first_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "DIR", tmp_path / "luxury")
    monkeypatch.setattr(L, "PROV", tmp_path / "prov.jsonl")
    monkeypatch.setattr(B, "DIR", tmp_path / "bakeoff")
    (tmp_path / "bakeoff").mkdir()
    (tmp_path / "luxury").mkdir()
    store.write_jsonl(tmp_path / "bakeoff" / "pictures.jsonl", [{"sha": "b1", "found": True}, {"sha": "gone", "found": True}])
    asked = []

    def from_volume(shas, vol_dir=B.VOL_DIR):
        asked.append(list(shas))
        out = {}
        for i, s in enumerate(shas):
            if s != "gone":
                b = io.BytesIO()
                Image.new("RGB", (300, 200), (40 * i, 100, 200)).save(b, format="JPEG")
                out[s] = b.getvalue()
        return out
    monkeypatch.setattr(B, "from_volume", from_volume)
    r = L.composition_read()
    assert r == {"asked": 2, "measured": 1, "errors": 1} and asked[0] == ["b1", "gone"]
    rows = store.read_jsonl(tmp_path / "luxury" / "composition-v1.jsonl")
    good = [x for x in rows if not x.get("error")]
    assert good[0]["sha"] == "b1" and good[0]["version"] == "composition-v1" and good[0]["open_space"] == 1.0
    assert L.composition_read() == {"asked": 1, "measured": 0, "errors": 1}      # only what failed is tried again


def test_the_answers_follow_the_rubric_they_were_read_with():
    from adtone import character, readings
    from adtone.score import load_rubric
    v1, v2 = load_rubric("tone-v1").spec, load_rubric("tone-v2").spec
    assert character.questions_of(v1) == character.QUESTIONS
    enc = character.Encoder(v2)
    assert "production" not in enc.qs and "modality" in enc.qs and "skin_shown" in enc.qs
    ans = readings.Answers(v2, {"modality": {"keep": True}, "production": {"keep": True}, "mood:serene": {"keep": True}})
    assert ans.qs == ["modality"] and ans.moods == ["serene"]
    assert ans.row({"modality": "reduced", "mood": ["serene"]}).tolist() == [0, 0, 1, 0, 1, 0]


def test_a_later_version_reads_only_what_its_plan_kept_into_its_own_files(tmp_path, monkeypatch):
    import modal
    _corpus(tmp_path, monkeypatch)
    hidden = {s: i for i, s in enumerate(sorted(L.found()))}
    asked = {"tone": [], "probs": []}

    class Method:
        def __init__(self, key, fn):
            self.key, self.fn = key, fn

        def starmap(self, gen, return_exceptions=False):
            for args in gen:
                asked[self.key].append(args)
                yield self.fn(*args)

    v2 = '{"creative_type": "brand_image", "category": "ready_to_wear", "light": "mixed", "colour_temperature": "warm", ' \
         '"saturation": "muted", "setting": "interior", "people": "one", "gaze": "away", "expression": "neutral", ' \
         '"pose": "posed_static", "framing": "medium", "vertical_angle": "eye_level", "horizontal_angle": "oblique", ' \
         '"placement": "centre", "modality": "reduced", "open_space": "much", "objects": "one", "arrangement": "single", ' \
         '"head_cant": "no", "self_touch": "no", "body_level": "upright", "withdrawal": "no", "skin_shown": "covered", ' \
         '"primary_subject": "garment", "styling_register": "casual", "rhetoric": "none", "text_in_image": "none", ' \
         '"mood": ["serene"], "street_couture_axis": 3, "confidence": 0.8}'

    class Fake:
        read_from = Method("tone", lambda folder, shas, system, schema: [v2 for _ in shas])
        compare_probs_from = Method("probs", lambda folder, pairs, system, prompts:
                                    [float(1 / (1 + np.exp(-(0.3 * (hidden[a] - hidden[b]))))) for a, b in pairs])
    monkeypatch.setattr(modal.Cls, "from_name", lambda app, name: (lambda: Fake()))
    assert "no plan" in L.read("qwen3", "tone", v="v2")["note"]               # nothing is read before the test decides
    assert asked["tone"] == []
    (L.DIR / "plan-v2.json").write_text(json.dumps({"comparisons": "probs", "axes": ["intimate"],
                                                    "questions": ["modality", "open_space"], "measures": ["open_space"]}))
    t = L.read("qwen3", "tone", v="v2")
    assert t["tone"]["rows"] == len(hidden)
    assert "open_space: how much of the frame" in asked["tone"][0][2]          # the v2 prompt was sent
    rows = store.read_jsonl(L.DIR / "readings" / "qwen3-tone-v2.jsonl")
    assert all(r["version"] == "tone-v2" and r["out"]["modality"] == "reduced" for r in rows)
    assert not (L.DIR / "readings" / "qwen3-tone.jsonl").exists()
    c = L.read("qwen3", "comparisons", v="v2")
    assert c["probs"]["rows"] == 2 * len(L.design()["reader"]["intimate"])
    assert "naturalistic" not in asked["probs"][0][3][0] and "brought close" in asked["probs"][0][3][0]   # pairs-v2's words
    assert {r["axis"] for r in store.read_jsonl(L.DIR / "readings" / "qwen3-probs-v2.jsonl")} == {"intimate"}
    assert set(L.positions("qwen3", "v2")) == {"intimate"}
    assert (L.DIR / "positions-qwen3-v2.jsonl").exists() and not (L.DIR / "positions-qwen3.jsonl").exists()
    spec = L._spec("v2")
    assert set(spec["enums"]) == {"creative_type", "category", "modality", "open_space"}
    assert spec["lists"]["mood"]["options"] == []                             # mood did not go forward
    from adtone import character
    enc = character.Encoder(spec)
    r = enc.row({"modality": "reduced", "open_space": "much"})
    assert enc.distance(r, r) == 0.0
    assert L.read("qwen3", "check", v="v2") == {"note": "the standing check is v1's"}
