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


def test_the_pictures_to_label_come_from_the_volume_at_a_size_a_person_can_judge(tmp_path, monkeypatch):
    pytest.importorskip("nacl")
    from adtone import bakeoff
    monkeypatch.setattr(C, "PROV", tmp_path / "prov.jsonl")
    asked = []

    def from_volume(shas, vol_dir):
        asked.append((vol_dir, sorted(shas)))
        return {s: _jpeg(900, 1350, i) for i, s in enumerate(shas)}
    monkeypatch.setattr(bakeoff, "from_volume", from_volume)
    s = {"looks": [{"sha": "l1", "house": "celine"}], "label": ["l1", "a1", "a2"]}
    sealed = C.seal_labelled(s, local=tmp_path)
    assert sorted(asked) == [(C.ADS_VOL_DIR, ["a1", "a2"]), (C.VOL_DIR, ["l1"])]
    files = sorted((tmp_path / "label").glob("*.jpg"))
    assert [f.stem for f in files] == ["a1", "a2", "l1"]
    assert max(Image.open(files[0]).size) == C.LABEL_EDGE
    assert sealed.exists() and sealed.stat().st_size > 1000 and b"JFIF" not in sealed.read_bytes()


def test_kappa_rewards_agreement_beyond_chance_and_near_misses_on_a_scale():
    assert C.cohen(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == 1.0
    assert C.cohen(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == 0.0
    near = C.cohen([1, 2, 3, 4, 5] * 4, [2, 3, 4, 5, 5] * 4, ordinal=True)
    far = C.cohen([1, 2, 3, 4, 5] * 4, [5, 1, 1, 2, 3] * 4, ordinal=True)
    assert near > 0.6 and far < 0
    assert C.pooled([("x", "x")] * 6 + [("y", "y")] * 6) == 1.0
    assert C.cohen(["a"] * 5, ["a"] * 5) is None          # no variation: agreement says nothing


SPEC = {"enums": {"subject": ["worn_full", "worn_part", "product_alone", "no_clothing"],
                  "pattern": ["plain", "stripe", "check", "not_applicable"],
                  "hemline": ["above_knee", "knee", "ankle_floor", "not_applicable"]},
        "lists": {"garments": {"options": ["dress", "coat", "trousers", "cape", "none"], "min": 1, "max": 3}},
        "integers": {"street_couture_axis": {"min": 1, "max": 5}},
        "eye": {"anyone": ["subject", "pattern", "hemline", "garments"], "trained": ["street_couture_axis"]}}


def _world(seed=3, n=60):
    """Pictures with true answers; the reader sees them except where told otherwise."""
    rng = random.Random(seed)
    truth, kinds = {}, {}
    for kind in ("look", "advert"):
        for i in range(n):
            sha = f"{kind[0]}{i}"
            kinds[sha] = kind
            subj = "worn_full" if kind == "look" or i % 3 else "product_alone"
            g = rng.sample(["dress", "coat", "trousers"], rng.randint(1, 2))
            truth[sha] = {"subject": subj, "pattern": rng.choice(["plain", "stripe", "check"]),
                          "hemline": rng.choice(["above_knee", "knee", "ankle_floor"]) if subj == "worn_full" else "not_applicable",
                          "garments": g, "street_couture_axis": rng.randint(1, 5)}
    return truth, kinds


def _labels(truth, kinds, k=30):
    pick = [s for s in truth if s.startswith("l")][:k] + [s for s in truth if s.startswith("a")][:k]
    return {s: dict(truth[s]) for s in pick}


def test_a_question_goes_forward_only_if_it_varies_holds_on_a_crop_and_agrees_on_both_kinds():
    truth, kinds = _world()
    reader = {s: dict(a) for s, a in truth.items()}
    rng = random.Random(9)
    for s, a in reader.items():
        if kinds[s] == "advert":                       # the reader reads hemlines on runway looks only
            a["hemline"] = rng.choice(["above_knee", "knee", "ankle_floor"]) if a["subject"] == "worn_full" else "not_applicable"
    crops = []
    for s in list(truth)[::3]:
        reader[s + "c"] = dict(reader[s])
        crops.append((s, s + "c"))
    labels = {"ben": _labels(truth, kinds), "trained-ann": _labels(truth, kinds), "trained-bo": _labels(truth, kinds)}
    out = C.judge(SPEC, reader, crops, kinds, labels)
    q = out["questions"]
    assert q["pattern"]["passes"] and q["subject"]["varies"]
    assert not q["hemline"]["passes"] and q["hemline"]["agree"]["ben:look"]["kappa"] == 1.0
    assert q["hemline"]["agree"]["ben:advert"]["kappa"] < C.KAPPA_MIN       # works on one kind only
    assert not q["garments.cape"]["varies"] and not q["garments.none"]["varies"]
    assert out["options"]["garments"] == ["dress", "coat", "trousers"]
    assert q["street_couture_axis"]["passes"] and q["street_couture_axis"]["experts_agree"]
    assert "garments" in out["forward"] and "hemline" not in out["forward"]


def test_a_trained_question_two_trained_eyes_split_on_does_not_go_forward():
    truth, kinds = _world()
    reader = {s: dict(a) for s, a in truth.items()}
    crops = [(s, s) for s in list(truth)[:20]]
    rng = random.Random(4)
    split = _labels(truth, kinds)
    for a in split.values():
        a["street_couture_axis"] = rng.randint(1, 5)
    out = C.judge(SPEC, reader, crops, kinds, {"ben": _labels(truth, kinds), "trained-ann": _labels(truth, kinds),
                                                "trained-bo": split})
    assert out["questions"]["street_couture_axis"]["experts_agree"] is False
    assert "street_couture_axis" not in out["forward"]
    alone = C.judge(SPEC, reader, crops, kinds, {"ben": _labels(truth, kinds)})
    assert "street_couture_axis" not in alone["forward"]               # no trained labels, no trained question
    assert alone["questions"]["street_couture_axis"]["untrained"]["reader~ben:look"]["kappa"] == 1.0


def test_a_runway_look_a_homepage_also_showed_is_not_drawn_as_advertising(tmp_path, monkeypatch):
    first = C.adverts()[0]["sha"]
    monkeypatch.setattr(C, "DIR", tmp_path)
    (tmp_path / "looks.jsonl").write_text(json.dumps({"sha": first, "house": "x"}) + "\n")
    assert first not in {a["sha"] for a in C.adverts()}


def test_the_runway_collector_keeps_a_spread_of_looks_per_show_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DIR", tmp_path)
    monkeypatch.setattr(C, "PROV", tmp_path / "prov.jsonl")
    looks = {f"https://www.chanel.com/i/look-{i}.jpg": _jpeg(600, 900, 200 + i) for i in range(50)}
    wide = {f"https://www.dior.com/i/set-{i}.jpg": _jpeg(1200, 600, 400 + i) for i in range(10)}
    pics = {**looks, **wide}

    class S:
        headers = {}
        calls = 0

        def get(self, url, timeout=None):
            S.calls += 1
            if "im_/" in url:
                return _R(content=pics[url.split("im_/", 1)[1]])
            src = looks if "chanel" in url else wide
            return _R(text="".join(f'<img src="{u}">' for u in src), url=url)

    cov = {"shows": {
        "chanel:2025-10-07:rtw": {"house": "chanel", "date": "2025-10-07", "season": "2026 SS rtw", "category": "rtw",
                                  "status": "found", "candidates": [{"ts": "20251010", "url": "https://www.chanel.com/show"}]},
        "dior:2025-10-01:rtw": {"house": "dior", "date": "2025-10-01", "season": "2026 SS rtw", "category": "rtw",
                                "status": "found", "candidates": [{"ts": "20251003", "url": "https://www.dior.com/show"}]},
        "celine:2025-10-03:rtw": {"house": "celine", "date": "2025-10-03", "season": "2026 SS rtw", "category": "rtw",
                                  "status": "none", "candidates": []}}}
    out = C.collect_runway(cov, pause=0, session_factory=S, local=tmp_path / "pics", workers=1)
    assert out["shows_collected"] == 1 and out["shows_without_looks"] == 1 and out["looks"] == C.PER_SHOW
    rows = [json.loads(x) for x in (tmp_path / "runway.jsonl").read_text().splitlines()]
    assert {r["season"] for r in rows} == {"2026 SS rtw"} and len({r["sha"] for r in rows}) == C.PER_SHOW
    assert max(r["order"] for r in rows) > 30                       # spread through the show, not its first looks
    assert len(list((tmp_path / "pics" / "read").glob("*.jpg"))) == C.PER_SHOW
    before = S.calls
    again = C.collect_runway(cov, pause=0, session_factory=S, local=tmp_path / "pics", workers=1)
    assert S.calls == before and again["looks"] == C.PER_SHOW          # nothing asked again


# ---------- the reading, and which questions stand ----------

ANSWER = {"subject": "worn_full", "garments": ["coat", "trousers"], "accessories": ["shoes"], "colour_main": "black",
          "colour_second": "white_ivory", "pattern": "plain", "skin_shown": "covered", "hemline": "ankle_floor",
          "layers": "two", "silhouette": "straight", "construction": "tailored", "finishing": ["none"],
          "materials": ["plain_woven"], "formality": "formal_tailored", "street_couture_axis": 4, "confidence": 0.8}


def _files(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DIR", tmp_path)
    monkeypatch.setattr(C, "READINGS", tmp_path / "readings.jsonl")
    monkeypatch.setattr(C, "PROV", tmp_path / "prov.jsonl")
    (tmp_path / "sample.json").write_text(json.dumps({
        "looks": [{"sha": "aa01", "house": "x"}, {"sha": "aa02", "house": "y"}],
        "adverts": [{"sha": "bb01", "house": "x", "person": True}, {"sha": "bb02", "house": "y", "person": False}],
        "label": ["aa01", "bb01"], "crop": ["aa01", "bb01"]}))
    (tmp_path / "crops.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
        {"orig": "aa01", "crop": "cc01", "kind": "look"}, {"orig": "bb01", "crop": "cc02", "kind": "advert"}]))
    (tmp_path / "runway.jsonl").write_text("".join(json.dumps({"sha": s, "house": "x", "date": "2026-03-01",
                                                               "category": "rtw"}) + "\n" for s in ("dd01", "dd02", "aa02")))
    return {C.VOL_DIR: {"aa01", "aa02", "cc01", "cc02"}, C.ADS_VOL_DIR: {"bb01"},
            C.RUNWAY_VOL_DIR: {"dd01", "aa02"}, C.HOME_VOL_DIR: {"bb01", "bb02", "ee01"}}


def test_the_test_set_is_read_first_and_each_picture_once_from_a_volume_that_holds_it(tmp_path, monkeypatch):
    present = _files(tmp_path, monkeypatch)
    plan = C.reading_plan(present)
    assert [p[2] for p in plan] == ["aa01", "aa02", "bb01", "bb02", "cc01", "cc02", "dd01", "ee01"]
    where = {sha: (st, folder) for st, folder, sha in plan}
    assert where["bb02"] == ("testset", C.HOME_VOL_DIR)        # not in the bake-off's folder, but the homepages hold it
    assert where["aa02"] == ("testset", C.VOL_DIR)             # a test-set look the runway also kept is read once
    assert "dd02" not in where                                 # on no volume yet: left for a later run


def test_the_reading_keeps_what_fits_the_rubric_tries_the_rest_again_and_never_reads_twice(tmp_path, monkeypatch):
    present = _files(tmp_path, monkeypatch)
    sent = []

    def fake(reader, method, parts, args, rows_of, out, deadline, log=None):
        n = 0
        for part in parts:
            folder, shas, system, schema = args(part)
            assert len({p[1] for p in part}) == 1 and folder == part[0][1] and len(shas) <= C.BATCH
            assert "Do not identify any person" in system and schema["additionalProperties"] is False
            sent.extend(shas)
            texts = []
            for sha in shas:
                if sha == "cc02" and sha not in sent[:-1]:
                    texts.append('{"subject": "worn_full"}')                              # does not fit: tried again
                elif sha == "dd01":
                    texts.append(json.dumps({**ANSWER, "garments": ["coat", "coat", "trousers"]}))
                else:
                    texts.append(json.dumps(ANSWER))
            rows = rows_of(part, texts)
            C.store.append_jsonl(out, rows)
            n += len(rows)
        return n

    first = C.read(present=present, on_modal=fake)
    assert first["sent"] == 8 and first["read_now"] == 7
    got = C.readings()
    assert set(got) == {"aa01", "aa02", "bb01", "bb02", "cc01", "dd01", "ee01"}
    assert got["dd01"]["garments"] == ["coat", "trousers"]          # an item named twice, taken once
    second = C.read(present=present, on_modal=fake)
    assert second["sent"] == 1 and set(C.readings()) == set(got) | {"cc02"}
    assert C.read(present=present, on_modal=fake)["sent"] == 0
    present[C.RUNWAY_VOL_DIR].add("dd02")
    assert C.read(present=present, on_modal=fake, min_waiting=5)["sent"] == 0      # one new look does not start the GPUs
    assert sent.count("aa01") == 1


def test_a_question_is_dropped_at_once_by_the_reader_rules_and_otherwise_provisional_until_its_labels_come():
    truth, kinds = _world()
    reader = {s: dict(a) for s, a in truth.items()}
    rng = random.Random(9)
    crops = []
    for s in list(truth)[::3]:
        reader[s + "c"] = dict(reader[s], pattern=rng.choice(["plain", "stripe", "check"]))   # pattern does not hold on a crop
        crops.append((s, s + "c"))
    for s, a in reader.items():
        if kinds.get(s) == "advert" and a["subject"] == "worn_full":
            a["hemline"] = rng.choice(["above_knee", "knee", "ankle_floor"])    # right on runway looks only
    now = C.standing(SPEC, C.judge(SPEC, reader, crops, kinds, {}), {})
    q = now["questions"]
    assert q["pattern"]["status"] == "dropped" and q["pattern"]["why"] == ["rule 2: stable on a crop"]
    assert q["hemline"]["status"] == q["subject"]["status"] == q["street_couture_axis"]["status"] == "provisional"
    assert q["garments"]["status"] == "provisional" and q["garments"]["options"] == ["dress", "coat", "trousers"]
    assert set(q["garments"]["dropped_options"]) == {"cape", "none"}
    assert now["status"] == "provisional" and "pattern" not in now["use"]
    labels = {"ben": _labels(truth, kinds)}
    ben = C.standing(SPEC, C.judge(SPEC, reader, crops, kinds, labels), {"ben": "all"})
    assert ben["questions"]["hemline"]["status"] == "dropped"                     # rule 3 on the advertising
    assert ben["questions"]["garments"]["status"] == "checked"
    assert ben["questions"]["subject"]["why"] == ["rule 3: agrees with the reference on both kinds"]   # every look is worn: no kappa
    assert ben["questions"]["street_couture_axis"]["status"] == "provisional"     # waits on a trained eye
    labels["trained-1"] = {s: {"street_couture_axis": a["street_couture_axis"]} for s, a in _labels(truth, kinds).items()}
    full = C.standing(SPEC, C.judge(SPEC, reader, crops, kinds, labels), {"ben": "all", "trained-1": "trained"})
    assert full["questions"]["street_couture_axis"]["status"] == "checked" and full["status"] == "checked"
    alone = C.standing(SPEC, C.judge(SPEC, reader, crops, kinds, {"trained-1": labels["trained-1"]}), {"trained-1": "trained"})
    assert alone["questions"]["street_couture_axis"]["status"] == "provisional"   # where it applies needs Ben's subject


def test_labels_from_the_page_are_kept_by_role_never_by_name_the_latest_answer_for_each_picture(tmp_path):
    spec = {"eye": {"anyone": ["subject", "pattern"], "trained": ["street_couture_axis"]}}
    s = {"label": [f"p{i}" for i in range(20)]}
    docs = tmp_path / "labels" / "u1"
    docs.mkdir(parents=True)
    full = {"subject": "worn_full", "pattern": "plain", "street_couture_axis": 3}
    n = 0
    for who, scope, shas, wrap in (("ben", "all", s["label"], False), ("trained-jane-doe", "trained", s["label"][:19], True),
                                   ("trained-al", "all", s["label"][:3], False)):
        for sha in shas:
            n += 1
            d = {"labeller": who, "role": "ben" if who == "ben" else "trained", "scope": scope, "task": "clothes",
                 "rubric": "clothes-v1", "sha": sha, "answers": dict(full), "at": 1000 + n + (0 if who != "trained-al" else -900)}
            (docs / f"{who}-{sha}.json").write_text(json.dumps({"id": sha, "version": 1, "data": d} if wrap else d))
    (docs / "old.json").write_text(json.dumps({"labeller": "ben", "task": "clothes", "rubric": "clothes-v1", "sha": "p0",
                                               "answers": {"subject": "no_clothing"}, "at": 1}))
    (docs / "kind.json").write_text(json.dumps({"labeller": "ben", "task": "picture_kind", "sha": "p1", "answers": {}}))
    out = C.import_labels(tmp_path / "labels", s=s, spec=spec, write=False)
    labs = out["labellers"]
    assert set(labs) == {"ben", "trained-1", "trained-2"}
    assert labs["trained-1"]["pictures"] == 3 and labs["trained-2"]["pictures"] == 19     # in the order they began
    assert "jane" not in json.dumps(out) and "trained-al" not in json.dumps(out)
    assert labs["ben"]["answers"]["p0"]["subject"] == "worn_full"                         # the later answer stands
    assert labs["ben"]["complete"] and labs["trained-2"]["complete"] and not labs["trained-1"]["complete"]


def test_a_show_is_read_only_from_pages_of_its_own_line(tmp_path, monkeypatch):
    assert not C.page_fits("https://www.dior.com/en_us/mens-fashion/shows/folder-winter-2022-2023-mens-show/x", "rtw")
    assert not C.page_fits("https://www.balenciaga.com/at/f%C3%BCr-ihn/collections/fallwinter-18-m_section", "rtw")
    assert not C.page_fits("https://www.chloe.com/us/chloe/women/fashionshow/", "men")
    assert not C.page_fits("https://www.dior.com/en_us/womens-fashion/haute-couture", "rtw")
    assert C.page_fits("https://www.chloe.com/us/chloe/women/fashionshow/", "rtw")
    assert C.page_fits("https://www.balenciaga.com/en-us/spring-24", "rtw")        # says nothing: kept
    assert C.page_fits("https://www.hermes.com/us/en/men/ready-wear/", "men")
    monkeypatch.setattr(C, "DIR", tmp_path)
    monkeypatch.setattr(C, "PROV", tmp_path / "prov.jsonl")
    rows = [{"sha": "a", "house": "dior", "date": "2022-03-01", "category": "rtw", "page": "https://www.dior.com/en_us/mens-fashion/shows/x"},
            {"sha": "b", "house": "dior", "date": "2022-03-01", "category": "rtw", "page": "https://www.dior.com/en_us/womens-fashion/shows/y"},
            {"sha": "c", "house": "chloe", "date": "2022-03-03", "category": "rtw", "page": "https://www.chloe.com/us/chloe/women/fashionshow/"}]
    (tmp_path / "runway.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (tmp_path / "runway_shows.json").write_text(json.dumps({"dior:2022-03-01:rtw": {"status": "collected", "looks": 2},
                                                             "chloe:2022-03-03:rtw": {"status": "collected", "looks": 1}}))
    assert C.recheck_lines() == {"shows_cleared": 1, "looks_dropped": 2, "looks_kept": 1}
    assert [r["sha"] for r in C.store.read_jsonl(tmp_path / "runway.jsonl")] == ["c"]
    assert set(json.loads((tmp_path / "runway_shows.json").read_text())) == {"chloe:2022-03-03:rtw"}   # Dior's to be taken again
    assert C.recheck_lines() == {"shows_cleared": 0, "looks_dropped": 0, "looks_kept": 1}
