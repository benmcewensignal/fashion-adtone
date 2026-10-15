"""The house issues: the plan names no house and asks for no marks, every house has a prompt, each prompt is
drawn once for each seed, a house drawn already is passed over, and nothing readable leaves sealed. The image
model is stood in for; pictures are made in memory."""
import io
import json

import pytest
from PIL import Image

from adtone import issues as I
from adtone import registry

HOUSES = {h.id: h.name for h in registry.load().houses}
ISSUED = [h.id for h in registry.load().houses if h.tier != "wider"]   # the 28; the wider field is drawn after them


def _png(colour=(120, 90, 60), size=(40, 50)):
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


class _Remote:
    def __init__(self, fn):
        self.remote = fn


class _Starter:
    def __init__(self):
        self.calls = []
        self.make = _Remote(self._make)

    def _make(self, prompt, negative, seeds, w, h, steps, cfg):
        self.calls.append(prompt)
        return [_png((s % 255, 80, 40)) for s in seeds]


def test_the_plan_has_a_prompt_for_every_house_and_names_none():
    plan = json.loads(I.PLAN.read_text())
    assert set(ISSUED) <= set(plan["houses"]) <= set(HOUSES)
    assert I.check_plan(plan, HOUSES, ISSUED) == []
    short = {**plan, "houses": {k: v for k, v in plan["houses"].items() if k != "armani"}}
    assert I.check_plan(short, HOUSES) == ["armani: no prompt"]      # every house needs one, unless told otherwise
    assert I.check_plan(short, HOUSES, [h for h in ISSUED]) == []


def test_a_prompt_that_names_a_house_or_asks_for_a_mark_is_refused():
    plan = {"negative": "text, logo", "houses": {h: "a coat on a dress form" for h in HOUSES}}
    plan["houses"]["gucci"] = "a loafer on red velvet in the Gucci manner"
    plan["houses"]["prada"] = "a nylon bag with a triangle logo"
    del plan["houses"]["zegna"]
    bad = I.check_plan(plan, HOUSES)
    assert "zegna: no prompt" in bad and any(b.startswith("gucci: names") for b in bad)
    assert "prada: asks for 'logo'" in bad
    assert I.check_plan({**plan, "negative": "blurry"}, HOUSES)[-1].startswith("the negative prompt")


def test_each_house_is_drawn_once_for_each_seed_and_a_house_drawn_already_is_passed_over(tmp_path, monkeypatch):
    pytest.importorskip("nacl")    # the pictures are sealed with PyNaCl, which the issues workflow installs
    from nacl.public import PrivateKey, SealedBox
    import base64, tarfile
    monkeypatch.setattr(I, "RUN", tmp_path / "run.json")
    monkeypatch.setattr(I, "DIR", tmp_path)
    monkeypatch.setattr(I, "PROV", tmp_path / "prov.jsonl")
    monkeypatch.setattr(I, "LOCAL", tmp_path / "local")
    sk = PrivateKey.generate()
    (tmp_path / "k.pub").write_text(base64.b64encode(bytes(sk.public_key)).decode())
    plan = {"version": "t", "negative": "text, logo", "style": "Tall portrait frame.", "seeds": [1, 2], "draw_size": [40, 50],
            "steps": 2, "cfg": 4.0, "houses": {h: "a coat on a dress form" for h in HOUSES}}
    vol, st = {}, _Starter()
    seal = lambda review: I.seal(review, tmp_path / "local", tmp_path / "k.pub")
    out = I.draw(plan, starter=st, put=vol.update, only=["gucci", "loewe"], seal_fn=seal)
    assert out["drawn_this_run"] == ["gucci", "loewe"] and len(out["rows"]) == 4
    assert all(p.endswith("Tall portrait frame.") for p in st.calls)
    assert f"{I.VOL_DIR}/png/gucci-2.png" in vol
    sealed = (tmp_path / "local" / "issues.tar.sealed").read_bytes()
    assert b"JFIF" not in sealed and b"PNG" not in sealed                   # nothing readable leaves sealed
    names = tarfile.open(fileobj=io.BytesIO(SealedBox(sk).decrypt(sealed))).getnames()
    assert names == ["gucci-1.jpg", "gucci-2.jpg", "loewe-1.jpg", "loewe-2.jpg"]
    again = I.draw(plan, starter=st, put=vol.update, only=["gucci", "loewe", "dior"], seal_fn=seal)
    assert again["drawn_this_run"] == ["dior"] and len(again["rows"]) == 6
    assert len(st.calls) == 3
    plan["seeds_for"] = {"gucci": [5, 6]}                                    # a new prompt, drawn on seeds of its own
    third = I.draw(plan, starter=st, put=vol.update, only=["gucci", "loewe"], seal_fn=seal)
    assert third["drawn_this_run"] == ["gucci"] and {r["name"] for r in third["rows"]} >= {"gucci-1", "gucci-5", "gucci-6"}


def test_the_chosen_pictures_become_the_sites_copies_named_by_their_hash(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setattr(I, "WEB", tmp_path / "web.json")
    monkeypatch.setattr(I, "DIR", tmp_path)
    monkeypatch.setattr(I, "PROV", tmp_path / "prov.jsonl")
    vol = {f"{I.VOL_DIR}/png/gucci-23.png": _png(size=(1088, 1360)), f"{I.VOL_DIR}/png/loewe-11.png": _png((10, 20, 30), (1104, 1472))}
    out = I.publish({"gucci": 23, "loewe": 11}, get=vol.__getitem__, put=vol.update)
    g = out["pictures"]["gucci"]
    data = vol[f"{I.VOL_DIR}/web/{g['name']}"]
    assert Image.open(io.BytesIO(data)).size == I.WEB_SIZE and Image.open(io.BytesIO(data)).format == "WEBP"
    assert g["sha1"] == hashlib.sha1(data).hexdigest() and g["name"] == f"gucci-{hashlib.sha256(data).hexdigest()[:12]}.webp"
    assert Image.open(io.BytesIO(vol[f"{I.VOL_DIR}/web/{out['pictures']['loewe']['name']}"])).size == I.WEB_SIZE   # 3:4 cut to 4:5
    assert json.loads((tmp_path / "web.json").read_text())["pictures"]["loewe"]["seed"] == 11


def test_a_house_published_before_keeps_its_name(tmp_path, monkeypatch):
    monkeypatch.setattr(I, "WEB", tmp_path / "web.json")
    monkeypatch.setattr(I, "DIR", tmp_path)
    monkeypatch.setattr(I, "PROV", tmp_path / "prov.jsonl")
    vol = {f"{I.VOL_DIR}/png/gucci-23.png": _png(size=(1088, 1360)), f"{I.VOL_DIR}/png/loewe-11.png": _png((10, 20, 30), (1088, 1360)),
           f"{I.VOL_DIR}/png/loewe-37.png": _png((200, 10, 10), (1088, 1360)), f"{I.VOL_DIR}/png/dior-5.png": _png((5, 5, 5), (1088, 1360))}
    first = I.publish({"gucci": 23, "loewe": 11}, get=vol.__getitem__, put=vol.update)
    gucci, loewe = dict(first["pictures"]["gucci"]), dict(first["pictures"]["loewe"])
    made = []
    monkeypatch.setattr(I, "web_copy", lambda png, f=I.web_copy: made.append(1) or f(png) + b"\0")   # as if the encoder changed
    again = I.publish({"gucci": 23, "loewe": 37, "dior": 5}, get=vol.__getitem__, put=vol.update)
    assert again["pictures"]["gucci"] == gucci and len(made) == 2                 # kept as it was; only loewe and dior made
    assert again["pictures"]["loewe"]["seed"] == 37 and again["pictures"]["loewe"]["name"] != loewe["name"]
    assert f"{I.VOL_DIR}/web/{again['pictures']['dior']['name']}" in vol and f"{I.VOL_DIR}/web/{gucci['name']}" in vol
    assert I.publish({"gucci": 23}, get=vol.__getitem__, put=lambda files: 1 / 0)["pictures"] == {"gucci": gucci}   # nothing to put
