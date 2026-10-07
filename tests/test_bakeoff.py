"""Bake-off: colour and light measured from pixels, positions fitted from paired judgements, the pairs
design, the sample, and the yardsticks that score each reader and image model."""
import random

import numpy as np
import pytest
from PIL import Image

from adtone import bakeoff as B


def _solid(rgb, size=(64, 48)):
    return Image.new("RGB", size, rgb)


def test_colour_and_light_from_pixels():
    grey = B.pixel_measures(_solid((128, 128, 128)))
    assert grey["chroma"] < 0.01 and grey["colourfulness"] < 0.01 and grey["warmth"] == 0 and grey["hue_spread"] == 0
    assert B.pixel_measures(_solid((255, 255, 255)))["high_key"] == 1.0
    black = B.pixel_measures(_solid((0, 0, 0)))
    assert black["low_key"] == 1.0 and black["lightness"] < 0.01
    red, blue = B.pixel_measures(_solid((200, 40, 30))), B.pixel_measures(_solid((30, 60, 200)))
    assert red["warmth"] == 1.0 and blue["warmth"] == -1.0
    assert red["chroma"] > 0.5 and red["colourful"] == 1.0
    half = Image.new("RGB", (64, 64), (0, 0, 0))
    half.paste((255, 255, 255), (0, 0, 64, 32))
    m = B.pixel_measures(half)
    assert m["contrast"] > 0.45 and abs(m["high_key"] - 0.5) < 0.02 and abs(m["low_key"] - 0.5) < 0.02
    assert set(m) == set(B.PIXEL_KEYS)


def test_positions_and_first_position_bias_are_recovered():
    rng = np.random.default_rng(0)
    n = 40
    truth = rng.normal(size=n)
    first, second, y = [], [], []
    for _ in range(3000):
        a, b = rng.choice(n, 2, replace=False)
        p = 1 / (1 + np.exp(-(truth[a] - truth[b] + 0.4)))
        first.append(a)
        second.append(b)
        y.append(float(rng.random() < p))
    th, beta = B.bradley_terry(n, np.array(first), np.array(second), np.array(y))
    assert np.corrcoef(th, truth)[0, 1] > 0.95
    assert abs(beta - 0.4) < 0.12
    assert abs(th.mean()) < 1e-9


def _pics(n_brands=6, per=10):
    return [{"sha": f"{h}-{i}", "house": f"b{h}"} for h in range(n_brands) for i in range(per)]


def test_the_pairs_design():
    pics = _pics()
    crops = [["0-0", "0-1"], ["1-0", "1-1"]]
    axes = [{"id": "opulent"}, {"id": "staged"}]
    d = B.design(pics, crops, axes, seed=1, human_per_axis=12, human_within=4, degree=6)
    for ax in ("opulent", "staged"):
        hs = [h for h in d["human"] if h["axis"] == ax]
        assert len(hs) == 12 and sum(h["same_brand"] for h in hs) == 4
        used = {}
        for h in hs:
            assert {h["left"], h["right"]} not in [set(c) for c in crops]
            for s in (h["left"], h["right"]):
                used[s] = used.get(s, 0) + 1
        assert max(used.values()) <= 2
        edges = d["reader"][ax]
        assert all({a, b} not in [set(c) for c in crops] for a, b in edges)
        deg = {}
        for a, b in edges:
            deg[a] = deg.get(a, 0) + 1
            deg[b] = deg.get(b, 0) + 1
        assert min(deg.values()) >= 4 and len(deg) == len(pics)
        assert all(sorted((h["left"], h["right"])) in edges for h in hs)
    assert d["human"][0]["id"].endswith("-01")


def test_the_sample_takes_six_a_side_and_adds_crops():
    rng = random.Random(3)
    images = []
    for h in range(4):
        for i in range(20):
            month = f"2024-{1 + i % 12:02d}" if i < 10 else f"2025-{7 + i % 6:02d}"
            images.append({"house": f"h{h}", "sha": f"{h}-{i}", "month": month,
                           "type": "campaign" if rng.random() < 0.5 else "on_model"})
    images += [{"house": "thin", "sha": f"t-{i}", "month": "2024-03", "type": "campaign"} for i in range(8)]
    dups = [(images[0], images[1]), (images[-1], images[-2])]
    s = B.choose(images, dups, {}, seed=5, per_side=6, n_crops=5)
    by = {}
    for p in s["pictures"]:
        by.setdefault((p["house"], p["side"]), []).append(p)
    assert all(len(by[(f"h{h}", side)]) >= 6 for h in range(4) for side in ("early", "late"))
    assert len(s["crop_pairs"]) == 2 and ("thin", "early") in by      # the crops of a thin brand come in too
    assert len({p["sha"] for p in s["pictures"]}) == len(s["pictures"])


def test_answers_are_read_from_short_replies():
    assert B.answer_of("First") == "first" and B.answer_of("second.") == "second"
    assert B.answer_of("The second picture") == "second" and B.answer_of("neither") is None


def test_the_pairs_rubric_is_frozen_and_names_both_ends():
    spec = B._pairs_rubric()
    assert spec["version"] == "pairs-v1" and len(spec["axes"]) == 5
    for ax in spec["axes"]:
        q = B.question(ax, spec)
        assert ax["away"] in q and ax["toward"] in q and q.endswith("Answer first or second.")
    assert "Do not identify any person" in spec["prompt"]


def test_the_yardsticks_reward_a_model_that_knows_brands():
    rng = np.random.default_rng(2)
    pics, good, bad = [], {}, {}
    centres = {h: rng.normal(size=16) for h in range(8)}
    for h in range(8):
        for i in range(12):
            sha = f"{h}-{i}"
            pics.append({"sha": sha, "house": f"b{h}", "side": "early" if i < 6 else "late"})
            good[sha] = centres[h] + rng.normal(scale=0.5, size=16)
            bad[sha] = rng.normal(size=16)
    for v in (good, bad):
        for k in v:
            v[k] = v[k] / np.linalg.norm(v[k])
    assert B._identity(good, pics)["hits"] >= 7 and B._identity(bad, pics)["hits"] <= 3
    assert B._knn_brand(good, pics, []) > 0.8 and B._knn_brand(bad, pics, []) < 0.4


def test_sealed_thumbnails_open_only_with_the_private_key(tmp_path, monkeypatch):
    nacl = pytest.importorskip("nacl.public")
    import base64
    import io
    import tarfile
    sk = nacl.PrivateKey.generate()
    key = tmp_path / "k.pub"
    key.write_text(base64.b64encode(bytes(sk.public_key)).decode())
    monkeypatch.setattr(B, "SEAL_KEY", key)
    monkeypatch.setattr(B, "LOCAL", tmp_path)
    (tmp_path / "thumb").mkdir()
    _solid((10, 20, 30)).save(tmp_path / "thumb" / "abc.jpg")
    sealed = B.seal_thumbnails().read_bytes()
    with pytest.raises(Exception):
        nacl.SealedBox(nacl.PrivateKey.generate()).decrypt(sealed)
    tar = tarfile.open(fileobj=io.BytesIO(nacl.SealedBox(sk).decrypt(sealed)))
    assert tar.getnames() == ["abc.jpg"]


def test_fetch_keeps_a_picture_only_when_its_bytes_match_and_resumes(tmp_path, monkeypatch):
    import hashlib
    import io
    monkeypatch.setattr(B, "DIR", tmp_path / "bakeoff")
    monkeypatch.setattr(B, "LOCAL", tmp_path / "local")
    monkeypatch.setattr(B, "PROV", tmp_path / "prov.jsonl")
    (tmp_path / "bakeoff").mkdir()

    def jpg(rgb):
        b = io.BytesIO()
        Image.new("RGB", (400, 500), rgb).save(b, format="JPEG")
        return b.getvalue()
    imgs = {f"https://x.com/{i}.jpg": jpg((40 * i, 80, 120)) for i in range(3)}
    shas = [hashlib.sha256(v).hexdigest() for v in imgs.values()]
    html = "<html><body>" + "".join(f'<img src="{u}">' for u in imgs) + "</body></html>"
    calls = []

    class R:
        def __init__(self, code, content=b"", text="", url=""):
            self.status_code, self.content, self.text, self.url = code, content, text, url

    class S:
        headers = {}

        def get(self, url, timeout=0):
            calls.append(url)
            if "/web/1/" in url:
                return R(200, text=html, url="https://web.archive.org/web/1/https://x.com/")
            for k, v in imgs.items():
                if url.endswith(k):
                    return R(200, content=v)
            return R(404)
    s = {"pictures": [{"sha": h, "capture": "1", "page": "https://x.com/"} for h in shas[:2]]
         + [{"sha": "not-there", "capture": "1", "page": "https://x.com/"}]}
    rows = B.fetch(s, pause=0, workers=2, session_factory=S)
    assert [r["found"] for r in rows] == [True, True, False]
    assert set(rows[0]["pixel"]) == set(B.PIXEL_KEYS)
    assert sorted(p.stem for p in (tmp_path / "local" / "read").glob("*.jpg")) == sorted(shas[:2])
    n = len(calls)
    rows = B.fetch(s, pause=0, workers=2, session_factory=S)
    assert [r["found"] for r in rows] == [True, True, False] and len(calls) > n      # only the missing one is sought again


def test_positions_survive_a_reader_that_leans_hard_on_one_position():
    rng = np.random.default_rng(5)
    n = 60
    truth = rng.normal(size=n)
    first, second, y = [], [], []
    for _ in range(4000):
        a, b = rng.choice(n, 2, replace=False)
        p = 1 / (1 + np.exp(-(1.5 * (truth[a] - truth[b]) - 2.0)))
        first.append(a)
        second.append(b)
        y.append(float(rng.random() < p))
    th, beta = B.bradley_terry(n, np.array(first), np.array(second), np.array(y))
    assert np.all(np.isfinite(th)) and np.isfinite(beta)
    assert np.corrcoef(th, truth)[0, 1] > 0.9 and abs(beta + 2.0) < 0.3


def test_a_checkpoint_loads_with_only_benign_objects_allowed_under_their_saved_names(tmp_path):
    import contextlib
    import pickle

    class Ser:
        def __init__(self, names, needs):
            self.names, self.needs, self.allowed = names, needs, []

        def get_unsafe_globals_in_checkpoint(self, path):
            if self.names is None:
                raise AttributeError("old torch")
            return self.names

        @contextlib.contextmanager
        def safe_globals(self, allow):
            self.allowed = [a[1] if isinstance(a, tuple) else a for a in allow]
            yield

    class Torch:
        def __init__(self, names, needs):
            self.serialization = Ser(names, needs)

        def load(self, path, map_location=None, weights_only=None):
            assert weights_only is True
            for n in self.serialization.needs:
                if n not in self.serialization.allowed:
                    raise pickle.UnpicklingError(f"Weights only load failed. Unsupported global: GLOBAL {n} was not an allowed global")
            return {"model_state_dict": {"w": 1}}

    need = ["numpy.core.multiarray.scalar", "numpy.dtype"]
    t = Torch(list(need), need)                         # the checkpoint names what it holds
    assert B._load_checkpoint(tmp_path / "c.pth", torch=t)["model_state_dict"] == {"w": 1}
    assert t.serialization.allowed == need               # allowed under the names it was saved with
    t = Torch(None, need)                                # older torch: the names are learnt from the refusals
    assert B._load_checkpoint(tmp_path / "c.pth", torch=t)["model_state_dict"] == {"w": 1}
    import pytest
    with pytest.raises(RuntimeError):
        B._load_checkpoint(tmp_path / "c.pth", torch=Torch(["os.system"], ["os.system"]))
    with pytest.raises(pickle.UnpicklingError):
        B._load_checkpoint(tmp_path / "c.pth", torch=Torch(None, ["builtins.eval"]))
