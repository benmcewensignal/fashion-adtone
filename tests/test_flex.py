"""The site's illustration of the clothes reader: masks, what a step changed in the reader's answers, which
version of a step is kept, how the steps chain and resume, and the houses' shares. Pictures are made in
memory; the image models and the reader are stood in for."""
import io
import itertools
import json
import threading

import numpy as np
from PIL import Image

from adtone import clothes as C
from adtone import flex as F

ANSWER = {"subject": "worn_full", "garments": ["dress"], "accessories": ["shoes"], "colour_main": "grey",
          "colour_second": "black", "pattern": "plain", "skin_shown": "some", "hemline": "knee",
          "layers": "one", "silhouette": "straight", "construction": "soft_cut", "finishing": ["none"],
          "materials": ["plain_woven"], "formality": "casual", "street_couture_axis": 3, "confidence": 0.8}
USE = ["accessories", "colour_main", "colour_second", "construction", "finishing", "formality", "garments", "hemline",
       "layers", "materials", "pattern", "silhouette", "skin_shown", "street_couture_axis", "subject"]
OPTIONS = {"accessories": ["bag", "shoes", "eyewear", "belt", "jewellery", "hosiery", "none"],
           "finishing": ["none", "beaded", "pleated"],
           "garments": ["shirt_blouse", "top", "sweater", "jacket", "coat", "trousers", "shorts", "skirt", "dress", "none"],
           "materials": ["leather", "knit", "sheer_lace", "satin_silk", "metallic", "plain_woven", "not_distinguishable"]}


def _png(colour, size=(64, 96)):
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


def test_a_mask_is_the_union_of_its_shapes_in_fractions_of_the_picture():
    m = np.asarray(Image.open(io.BytesIO(F.mask_png([{"box": [0, 0, 0.5, 0.25]}, {"ellipse": [0.5, 0.5, 1, 1]}], (64, 96)))))
    assert m.shape == (96, 64)
    assert m[5, 5] == 255 and m[5, 40] == 0          # inside the box, beside it
    assert m[72, 48] == 255 and m[50, 33] == 0       # the ellipse's middle, its corner
    assert set(np.unique(m)) == {0, 255}


def test_a_cut_takes_out_every_square_it_touches_so_what_it_covers_is_never_redrawn():
    m = np.asarray(Image.open(io.BytesIO(F.mask_png([{"box": [0, 0, 1, 1]}, {"cut": [20 / 64, 20 / 96, 30 / 64, 40 / 96]}],
                                                    (64, 96)))))
    assert (m[16:48, 16:32] == 0).all()               # the squares the cut touches, whole
    assert m[15, 20] == 255 and m[20, 32] == 255 and m[48, 20] == 255


def test_a_step_is_judged_by_the_changes_it_meant_and_those_it_did_not():
    after = {**ANSWER, "accessories": ["shoes", "jewellery", "hat"], "formality": "eveningwear"}
    j = F.judge_step(ANSWER, after, ["accessories+jewellery"], USE, OPTIONS)
    assert j["met"] == ["accessories+jewellery"] and j["missed"] == []
    assert j["unmeant"] == ["formality=eveningwear"]          # a hat is not an option in use, so not a change
    assert j["score"] == 3 - 1
    skirt = {**ANSWER, "garments": ["top", "skirt"], "hemline": "midi", "street_couture_axis": 4}
    j = F.judge_step(ANSWER, skirt, ["garments-dress", "garments+top", "garments+skirt", "hemline=midi", "silhouette=flared"],
                     USE, OPTIONS)
    assert j["missed"] == ["silhouette=flared"] and j["unmeant"] == ["street_couture_axis=4"]
    assert j["score"] == 3 * 4 - 2 - 0.5                       # the street-couture score's wobble counts half
    assert F.judge_step(ANSWER, None, ["accessories+jewellery"], USE, OPTIONS)["score"] < -50


def test_the_version_kept_is_the_one_named_else_the_best_read_else_the_first():
    c = [{"name": "A1-1", "score": 1, "unmeant": ["x"]}, {"name": "A1-2", "score": 3, "unmeant": ["x"]},
         {"name": "A1-3", "score": 3, "unmeant": []}]
    assert F.pick(c) == 2
    assert F.pick(c, keep="A1-1") == 0
    assert F.pick([{"name": "a", "score": 0, "unmeant": []}, {"name": "b", "score": 0, "unmeant": []}]) == 0


class _Remote:
    def __init__(self, fn):
        self.remote = fn


class _World:
    """The image model and the reader stood in for: each version is a picture of one flat colour, and the
    reader's answer for it is set when it is drawn."""

    def __init__(self):
        self.answers = {}          # read copy's hash -> answer
        self.edits = []
        self.volume = {}
        self.edit = _Remote(self._edit)
        self.make = _Remote(self._make)
        self.read = _Remote(self._read)
        self.plan = {}             # (step prompt, seed) -> answer
        self._n, self._lock = itertools.count(1), threading.Lock()

    def _colour(self):
        with self._lock:          # the two directions are drawn at once: every version its own colour
            n = next(self._n)
        return (n * 37 % 256, n * 91 % 256, (n * 53 + n // 7) % 256)

    def _make(self, prompt, negative, seeds, w, h, steps, cfg):
        out = []
        for s in seeds:
            png = _png(self._colour(), (w, h))
            self.answers[F.sha_of(F.read_copy(F.to_size(png)))] = ANSWER
            out.append(png)
        return out

    def _edit(self, picture, mask, prompt, negative, seeds, steps, cfg, feather):
        self.edits.append((F.sha_of(picture), prompt, list(seeds)))
        out = []
        for s in seeds:
            png = _png(self._colour(), F.SIZE)
            self.answers[F.sha_of(F.read_copy(png))] = self.plan.get((prompt, s), ANSWER)
            out.append(png)
        return out

    def _read(self, jpegs, system, schema):
        assert "Do not identify any person" in system
        return [json.dumps(self.answers[F.sha_of(j)]) for j in jpegs]

    def put(self, files):
        self.volume.update(files)

    def get(self, path):
        return self.volume[path]


def _paths(tmp_path, monkeypatch):
    for name in ("PLAN", "MODELS", "START", "FRAMES", "SHARES"):
        monkeypatch.setattr(F, name, tmp_path / f"{name.lower()}.json")
    monkeypatch.setattr(F, "PROV", tmp_path / "prov.jsonl")
    monkeypatch.setattr(F, "LOCAL", tmp_path / "local")
    monkeypatch.setattr(F, "standing", lambda: (USE, OPTIONS))


PLAN = {"version": "flex-test", "negative": " ", "edit_steps": 4, "cfg": 4.0, "feather": 6, "draws": 3, "draw_seed": 1,
        "start": {"prompt": "a look", "negative": "", "seeds": [7, 8], "draw_size": [96, 144], "steps": 4, "cfg": 4.0,
                  "chosen": "start-8"},
        "arms": {"decorated": [{"id": "A1", "prompt": "add jewellery", "mask": [{"box": [0.4, 0.1, 0.6, 0.3]}],
                                "expect": ["accessories+jewellery"]},
                               {"id": "A2", "prompt": "a pleated skirt", "mask": [{"box": [0.2, 0.4, 0.8, 0.8]}],
                                "expect": ["garments-dress", "garments+skirt"]}],
                 "plainer": [{"id": "B1", "prompt": "make it black", "mask": [{"box": [0.2, 0.2, 0.8, 0.7]}],
                              "expect": ["colour_main=black"]}]}}


def test_the_start_picture_is_drawn_for_each_seed_brought_to_size_read_and_sealed(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    w = _World()
    out = F.start(PLAN, starter=w, reader=w, put=w.put)
    assert [c["name"] for c in out["candidates"]] == ["start-7", "start-8"]
    assert all(c["reading"]["garments"] == ["dress"] for c in out["candidates"])
    png = w.volume[f"{F.VOL_DIR}/png/start-8.png"]
    assert Image.open(io.BytesIO(png)).size == F.SIZE                       # drawn larger, kept at the working size
    jpg = w.volume[f"{F.VOL_DIR}/{out['candidates'][1]['sha']}.jpg"]
    assert max(Image.open(io.BytesIO(jpg)).size) == 896                     # the reader's copy, as a runway look's
    sealed = (tmp_path / "local" / "flex.tar.sealed").read_bytes()
    assert b"JFIF" not in sealed and b"PNG" not in sealed                   # nothing readable leaves sealed
    assert json.loads(F.START.read_text())["candidates"][0]["reading"]


def test_steps_keep_the_version_read_as_meant_draw_on_from_it_and_never_draw_a_step_twice(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    w = _World()
    F.start(PLAN, starter=w, reader=w, put=w.put)
    jewel = {**ANSWER, "accessories": ["shoes", "jewellery"]}
    w.plan[("add jewellery", 1)] = {**jewel, "formality": "eveningwear"}       # met, with a change not meant
    w.plan[("add jewellery", 2)] = jewel                                        # met, nothing else: kept
    w.plan[("a pleated skirt", 3)] = {**jewel, "garments": ["skirt"]}
    w.plan[("make it black", 1)] = {**ANSWER, "colour_main": "black"}
    frames = F.steps(PLAN, editor=w, reader=w, put=w.put, get=w.get)
    a = frames["arms"]["decorated"]
    assert a["A1"]["chosen"] == "A1-2" and a["A2"]["chosen"] == "A2-3" and frames["arms"]["plainer"]["B1"]["chosen"] == "B1-1"
    drawn_from = {prompt: prev for prev, prompt, _ in w.edits}
    assert drawn_from["a pleated skirt"] == F.sha_of(w.volume[f"{F.VOL_DIR}/png/A1-2.png"])   # the kept version
    assert drawn_from["add jewellery"] == F.sha_of(w.volume[f"{F.VOL_DIR}/png/start-8.png"])
    assert a["A1"]["candidates"][0]["unmeant"] == ["formality=eveningwear"]
    n = len(w.edits)
    F.steps(PLAN, editor=w, reader=w, put=w.put, get=w.get)
    assert len(w.edits) == n                                                    # nothing drawn twice
    keep = json.loads(json.dumps(PLAN))
    keep["arms"]["decorated"][0]["keep"] = "A1-1"                               # the plan names another version
    frames = F.steps(keep, editor=w, reader=w, put=w.put, get=w.get)
    assert frames["arms"]["decorated"]["A1"]["chosen"] == "A1-1"
    assert [e[1] for e in w.edits[n:]] == ["a pleated skirt"]                    # only the step after it drawn again
    assert json.loads(F.FRAMES.read_text())["arms"]["decorated"]["A2"]["from"] == "A1-1"


def test_a_house_is_read_from_its_designers_seasons_of_its_own_line(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    monkeypatch.setattr(C, "DIR", tmp_path / "clothes")
    monkeypatch.setattr(C, "READINGS", tmp_path / "clothes" / "readings.jsonl")
    (tmp_path / "clothes").mkdir()
    looks = [("a1", "x", "rtw", "2019-02-26", "https://x.com/women/show-aw19"),
             ("a2", "x", "rtw", "2019-02-26", "https://x.com/women/show-aw19"),
             ("a3", "x", "rtw", "2015-02-26", "https://x.com/women/show-aw15"),     # before the designer
             ("a4", "x", "men", "2019-01-18", "https://x.com/men/show-aw19"),       # another line
             ("a5", "x", "rtw", "2019-02-26", "https://x.com/men/show-aw19")]       # a men's page for a women's show
    (tmp_path / "clothes" / "runway.jsonl").write_text("".join(
        json.dumps({"sha": s, "house": h, "category": c, "date": d, "page": p, "season": "2019 AW rtw"}) + "\n"
        for s, h, c, d, p in looks))
    rows = [{"sha": s, "version": C.VERSION, "out": {**ANSWER, "accessories": ["shoes", "jewellery"] if s == "a1" else ["shoes"]}}
            for s, *_ in looks]
    (tmp_path / "clothes" / "readings.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    plan = {"version": "t", "houses": [{"house": "x", "designer": "D", "category": "rtw", "from": "2016-01-01", "to": "2025-06-30"}]}
    out = F.shares(plan, write=False)["houses"]["x"]
    assert out["looks"] == 2 and out["shows"] == 1
    assert out["shares"]["accessories+jewellery"] == [1, 2] and out["shares"]["hemline=knee"] == [2, 2]
    assert "subject=worn_full" not in out["shares"]


def test_a_tidying_step_comes_first_and_both_directions_start_from_it(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    w = _World()
    F.start(PLAN, starter=w, reader=w, put=w.put)
    plan = json.loads(json.dumps(PLAN))
    plan["prep"] = [{"id": "P1", "prompt": "tidy the edges", "mask": [{"box": [0, 0.4, 0.1, 0.7]}], "expect": []}]
    w.plan[("tidy the edges", 1)] = {**ANSWER, "formality": "eveningwear"}       # a change not meant
    frames = F.steps(plan, editor=w, reader=w, put=w.put, get=w.get)
    assert frames["arms"]["prep"]["P1"]["chosen"] == "P1-2"
    tidy = F.sha_of(w.volume[f"{F.VOL_DIR}/png/P1-2.png"])
    firsts = [prev for prev, prompt, _ in w.edits if prompt in ("add jewellery", "make it black")]
    assert firsts == [tidy, tidy]
    assert frames["arms"]["decorated"]["A1"]["from"] == "P1-2"
