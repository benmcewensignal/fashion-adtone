"""The luxury reading: the corpus of every homepage picture, and fetching it again."""
import hashlib
import io
import json

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
