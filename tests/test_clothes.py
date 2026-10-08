"""The clothes rubric's test set: which archived pages are opened for the looks, which pictures count as
looks, and how the test set, the crops and the pictures to label are drawn. Pictures are made in memory;
none is written anywhere but a temporary folder."""
import io
import json
import random

import numpy as np
import pytest
from PIL import Image

from adtone import clothes as C


def _jpeg(w, h, seed):
    """A picture made from a fixed pattern of 12 by 18 blocks for its seed, at any size: the same seed at two
    sizes is the same picture."""
    rng = np.random.default_rng(seed)
    a = (rng.random((18, 12, 3)) * 255).astype("uint8")
    img = Image.fromarray(a).resize((w, h), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


PROBE = {"houses": {
    "celine": {"verdict": "looks found", "pages": [
        {"status": "ok", "pictures": 29, "kept": 3, "ts": "20251225", "url": "https://www.celine.com/en-us/cm/fashion-show-spring-2026"},
        {"status": "ok", "pictures": 15, "kept": 3, "ts": "20251212", "url": "https://www.celine.com/en-us/cm/spring-26-highlights"}]},
    "miu_miu": {"verdict": "looks found", "pages": [
        {"status": "ok", "pictures": 108, "kept": 3, "ts": "20251213", "url": "https://www.miumiu.com/us/en/ss26-collection-pre-order/c/1"},
        {"status": "ok", "pictures": 211, "kept": 3, "ts": "20251228", "url": "https://www.miumiu.com/us/en/miumiu-club/fashion-shows/ss26-fashion-show.html"}]},
    "givenchy": {"verdict": "pages found, few pictures kept", "pages": [
        {"status": "ok", "pictures": 70, "kept": 0, "ts": "20251107", "url": "https://www.givenchy.com/gb/en/cm/x"}]},
}}


def test_the_show_pages_are_opened_and_product_pages_left():
    got = C.pages_for(PROBE)
    assert set(got) == {"celine", "miu_miu"}                          # givenchy's pictures were not kept
    assert got["miu_miu"] == [("20251228", "https://www.miumiu.com/us/en/miumiu-club/fashion-shows/ss26-fashion-show.html")]
    assert got["celine"][0][1].endswith("fashion-show-spring-2026")


def test_a_show_is_sampled_from_start_to_finish():
    assert C.spread(list(range(10)), 4) == [0, 3, 6, 9]
    assert C.spread([1, 2], 4) == [1, 2]


class _R:
    def __init__(self, status=200, text="", content=b"", url=""):
        self.status_code, self.text, self.content, self.url = status, text, content, url


def test_looks_are_portrait_pictures_counted_once_whatever_their_size():
    pics = {f"https://x.com/look-{i}.jpg": _jpeg(600, 900, i) for i in range(8)}
    pics["https://x.com/wide.jpg"] = _jpeg(1200, 600, 100)             # landscape: a campaign or a set
    pics["https://x.com/small.jpg"] = _jpeg(200, 300, 101)             # too small to read
    pics["https://x.com/look-0-big.jpg"] = _jpeg(800, 1200, 0)         # look 0 again, larger
    html = "".join(f'<img src="{u}">' for u in list(pics))

    def get(url):
        if "im_/" in url:
            return _R(content=pics[url.split("im_/", 1)[1]])
        return _R(text=f"<html><body>{html}</body></html>", url=url)

    kept = {}
    rows = C.house_looks(get, "celine", [("20251225", "https://x.com/show")], lambda sha, r, t: kept.update({sha: (r, t)}),
                         lambda img: (b"r", b"t"), per_house=4)
    assert len(rows) == 4 and set(kept) == {r["sha"] for r in rows}
    assert [r["order"] for r in rows] == [0, 2, 5, 7]                  # eight distinct looks, spread through
    assert all(r["h"] >= C.PORTRAIT * r["w"] and "_copies" not in r for r in rows)


def test_the_test_set_mixes_looks_and_advertising_and_labels_fifty_of_each(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DIR", tmp_path)
    monkeypatch.setattr(C, "PROV", tmp_path / "prov.jsonl")
    houses = ["balenciaga", "celine", "chloe", "hermes", "valentino"]
    looks = [{"sha": f"l{h}{i}", "house": h} for h in houses for i in range(14)]
    (tmp_path / "looks.jsonl").write_text("".join(json.dumps(r) + "\n" for r in looks))
    ads = [{"sha": f"a{i}", "house": houses[i % 5], "person": i % 3 != 0} for i in range(150)]
    monkeypatch.setattr(C, "adverts", lambda seed=C.SEED: ads)
    s = C.sample()
    looks_in = {r["sha"] for r in looks}
    lab = s["label"]
    assert len(lab) == 100 and len(set(lab)) == 100
    assert sum(x in looks_in for x in lab) == 50
    person = {a["sha"] for a in ads if a["person"]}
    assert sum(x in person for x in lab) == C.LABEL_PERSON
    per_house = {h: sum(1 for x in lab if x in looks_in and x[1:].startswith(h)) for h in houses}
    assert set(per_house.values()) == {10}                              # spread evenly across houses
    assert len(s["crop"]) == 60 and sum(x in looks_in for x in s["crop"]) == 30
    assert json.loads((tmp_path / "sample.json").read_text())["counts"]["label"] == 100


def test_the_advertising_pictures_come_from_the_earlier_bake_off_two_in_three_with_a_person():
    ads = C.adverts()
    assert len(ads) == C.N_ADS and sum(a["person"] for a in ads) == C.ADS_PERSON
    assert len({a["sha"] for a in ads}) == C.N_ADS


def test_a_crop_keeps_the_centre_and_is_stored_under_its_own_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DIR", tmp_path)
    monkeypatch.setattr(C, "PROV", tmp_path / "prov.jsonl")
    data = _jpeg(600, 900, 7)
    c = Image.open(io.BytesIO(C.crop_central(data)))
    assert abs(c.width / c.height - 600 / 900) < 0.02
    stored = {}
    s = {"looks": [{"sha": "x", "house": "celine"}], "crop": ["x", "y"]}
    rows = C.make_crops(s, read=lambda sha, vol: data if sha == "x" else None, put=lambda n, b: stored.update({n: b}))
    assert rows[0]["kind"] == "look" and f"{rows[0]['crop']}.jpg" in stored and rows[1]["crop"] is None
