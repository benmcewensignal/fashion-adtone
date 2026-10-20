import csv
from datetime import date
from pathlib import Path

import pytest

from adtone import agreement, config, copyfeat, guard, probe, registry, store


# ---------- guard ----------

def test_guard_refuses_images_anywhere_and_foreign_files(tmp_path):
    assert guard.violations("process", ["data/media/2026-10.jsonl"], tmp_path) == []
    v = guard.violations("process", ["data/media/look.jpg", "data/ads/2026-10.jsonl", "README.md"], tmp_path)
    assert len(v) == 3 and "never reach a commit" in v[0] and "not owned" in v[1]


def test_guard_refuses_files_containing_a_secret(tmp_path):
    p = tmp_path / "data" / "ads"
    p.mkdir(parents=True)
    (p / "2026-10.jsonl").write_text('{"ad_id":"1","note":"access_token=SECRETTOKEN123456"}\n')
    assert guard.violations("collect", ["data/ads/2026-10.jsonl"], tmp_path, secrets=["SECRETTOKEN123456"])
    assert guard.violations("collect", ["data/ads/2026-10.jsonl"], tmp_path, secrets=["other-secret-xyz"]) == []


def test_guard_rejects_unknown_workflows():
    assert guard.violations("deploy", ["x"]) == ["unknown workflow 'deploy'"]


def test_every_owned_path_belongs_to_one_workflow_only():
    owners = {}
    for wf, pats in guard.ALLOW.items():
        for pat in pats:
            assert pat not in owners, f"{pat} owned by {owners.get(pat)} and {wf}"
            owners[pat] = wf


# ---------- probes ----------

def test_collect_probe_flags_token_leaks_and_missing_keys(tmp_data, monkeypatch):
    monkeypatch.setattr(registry, "load", lambda *a, **k: registry.Registry(1, "T", []))
    store.write_jsonl(config.ADS_DIR / "2026-01.jsonl",
                      [{"ad_id": "1", "house_id": "h", "page_id": "p", "first_collected": "r", "last_collected": "r",
                        "copy": {}, "x": "access_token=abc"}])
    errs = probe.probe_collect("r", "incremental")
    assert any("access_token" in e for e in errs)
    store.write_jsonl(config.ADS_DIR / "2026-01.jsonl", [{"ad_id": "1"}])
    assert any("missing" in e for e in probe.probe_collect("r", "incremental"))


def test_process_probe_counts_from_disk(tmp_data):
    store.write_jsonl(config.MEDIA_DIR / "2026-10.jsonl", [
        {"ad_id": str(i), "processed_run": "r", "status": "resolved", "images": [{"sha": f"s{i}"}]} for i in range(5)])
    store.write_jsonl(config.OBS_DIR / "tone-v1.jsonl", [{"sha": "s0", "instrument": "tone-v1@m"}])
    errs = probe.probe_process("r", "tone-v1@m", "fake")
    assert any("no observation row" in e for e in errs) and any("no vector" in e for e in errs)


def test_process_probe_catches_a_resolver_that_finds_nothing(tmp_data):
    store.write_jsonl(config.MEDIA_DIR / "2026-10.jsonl",
                      [{"ad_id": str(i), "processed_run": "r", "status": "no_candidates"} for i in range(25)])
    assert any("browser" in e for e in probe.probe_process("r", "tone-v1@m", "fake"))


# ---------- registry ----------

def test_shipped_registry_is_coherent():
    full = registry.load()
    assert full.status in ("DRAFT", "FROZEN")
    reg = full.core()   # the frozen v1 panel; extension houses belong to Amendment 2
    treated, control = reg.group("treated"), reg.group("control")
    assert len(treated) == 10 and len(control) == 8 and [h.id for h in reg.group("watch")] == ["chloe"]
    assert all(h.debut and date(2025, 7, 1) <= h.debut.date <= date(2026, 3, 1) for h in treated)
    assert {"balenciaga", "gucci"} <= {h.id for h in treated}
    ext = full.tier("extension")
    checked = {h.id for h in ext for e in h.events if e.verified}
    assert ext and checked <= {"givenchy", "tom_ford"}, "extension dates stay unverified until checked against a calendar"


def test_a_house_counts_with_the_owner_of_the_day():
    full = registry.load()
    versace = full.by_id("versace")
    assert versace.owner_on(date(2018, 7, 1)) != versace.owner_on(date(2019, 7, 1)) != "Prada Group"
    assert versace.owner_on(date(2025, 7, 1)) == "Capri Holdings" and versace.owner_on(date(2026, 7, 1)) == "Prada Group"
    assert full.by_id("jil_sander").owner_on(date(2020, 7, 1)) == "Onward Holdings"
    assert full.by_id("prada").owner_on(date(2018, 7, 1)) == "Prada Group"       # no earlier owner on file


def test_registry_rejects_a_page_claimed_by_two_houses(tmp_path):
    p = tmp_path / "h.yml"
    p.write_text("version: 1\nstatus: DRAFT\nhouses:\n"
                 "  - {id: a, name: A, group: control, page_ids: ['1'], events: []}\n"
                 "  - {id: b, name: B, group: control, page_ids: ['1'], events: []}\n")
    with pytest.raises(ValueError, match="both"):
        registry.load(p)


# ---------- copy features ----------

def test_copy_features_and_hash_ignore_case_and_spacing():
    f = copyfeat.features(["NEW Collection!  Shop now €590 #SS26 @house", "NEW Collection!  Shop now €590 #SS26 @house"])
    assert f["n_variants"] == 1 and f["has_price"] and f["n_hashtags"] == 1 and f["n_mentions"] == 1
    assert copyfeat.text_sha(["New   collection"]) == copyfeat.text_sha(["new collection"])
    assert copyfeat.features(None)["sha"] is None
    assert copyfeat.features(["Hi {{product.name}}"])["is_template"] is True


# ---------- agreement ----------

def test_kappa_values():
    a = ["x", "y"] * 10
    assert agreement.kappa(a, a) == 1.0
    assert agreement.kappa(a, ["x"] * 20) == 0.0
    assert agreement.kappa(a[:5], a[:5]) is None


def test_sample_is_blind(tmp_data):
    store.write_jsonl(config.MEDIA_DIR / "2026-10.jsonl", [
        {"ad_id": str(i), "house_id": "h", "status": "resolved", "images": [{"sha": f"s{i}", "w": 800, "h": 1000}]}
        for i in range(30)])
    store.write_jsonl(config.OBS_DIR / "tone-v1.jsonl", [
        {"sha": f"s{i}", "instrument": "tone-v1@m", "status": "ok", "output": {"light": "low_key"}} for i in range(30)])
    out = agreement.sample(12, "tone-v1@m")
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 12
    assert all(r["light"] == "" and r["library_url"].endswith(r["ad_id"]) for r in rows)


def test_the_check_samples_the_vendor_s_meta_ads_and_scores_the_page_s_answers(tmp_data):
    import json
    from adtone import adlibs
    P = adlibs.paths("metaweb")
    store.write_jsonl(P["ads"] / "h.jsonl", [
        {"ad_id": str(100 + i), "house_id": "h", "page_id": "p1" if i < 25 else "p9", "media": [
            {"sha": f"v{i}a", "w": 400, "h": 400, "kind": "image"}, {"sha": f"v{i}b", "w": 800, "h": 1000, "kind": "image"},
            {"sha": f"v{i}f", "w": 2000, "h": 2000, "kind": "frame"}]} for i in range(30)])
    store.write_jsonl(P["obs"] / "tone-v1.jsonl", [
        {"sha": f"v{i}b", "ad_id": str(100 + i), "instrument": "tone-v1@m", "status": "ok",
         "output": {"light": "low_key" if i % 2 else "high_key", "setting": "studio_plain"}} for i in range(30)])
    store.write_jsonl(P["ads"] / "h2.jsonl", [{"ad_id": "90", "house_id": "h", "page_id": "p1",
                                               "media": [{"sha": "v3b", "w": 800, "h": 1000, "kind": "image"}]}])  # a shared picture
    out = agreement.sample(20, "tone-v1@m", confirmed={"h": {"p1"}})
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 20 and len({r["sha"] for r in rows}) == 20                         # each image once
    assert all(r["sha"].endswith("b") and (r["picture"], r["of"]) in (("2", "2"), ("1", "1")) for r in rows)
    assert all(int(r["ad_id"]) < 125 for r in rows) and all(r["light"] == "" for r in rows)   # confirmed pages; blind
    # the labelling page's documents: the latest answer kept, a trained labeller by role, never by name
    folder = tmp_data / "export"
    folder.mkdir()
    for k, r in enumerate(rows):
        i = int(r["ad_id"]) - 100
        doc = {"task": "human_check", "rubric": "tone-v1", "sha": r["sha"], "labeller": "ben", "at": 2,
               "answers": {"light": "low_key" if i % 2 else "high_key", "setting": "studio_plain", "street_couture_axis": 3}}
        (folder / f"b{k}.json").write_text(json.dumps({"data": doc}))
        (folder / f"t{k}.json").write_text(json.dumps({**doc, "labeller": "trained-ann", "answers": {"light": "mixed"}}))
    (folder / "old.json").write_text(json.dumps({**doc, "at": 1, "answers": {"light": "unclear"}}))
    coded = agreement.import_labels(folder, "tone-v1")
    assert sorted(coded) == ["ben", "trained-1"]
    got = agreement.score(coded["ben"], "tone-v1@m")
    assert got["light"]["n"] == 20 and got["light"]["kappa"] == 1.0 and got["light"]["usable"] is True
    assert got["creative_type"]["n"] == 0 and got["creative_type"]["kappa"] is None
    assert agreement.score(coded["trained-1"], "tone-v1@m")["light"]["usable"] is False


def test_workflows_only_persist_as_workflows_the_guard_knows():
    import re
    import yaml
    root = Path(__file__).resolve().parent.parent / ".github" / "workflows"
    files = sorted(root.glob("*.yml"))
    assert len(files) == 32   # reader (Amendment 2, 2e), press split out, homepages, youtube, tiktok, statements, bakeoff, luxury, thread, clothes, flex, adstransparency, archiveprobe, issues, live, snap, msads, explore, metaweb, xads, pinterest, linkedin
    for f in files:
        text = f.read_text()
        yaml.safe_load(text)
        for wf in re.findall(r"persist\.sh (\w+)", text):
            assert wf in guard.ALLOW, f"{f.name} persists as unknown workflow {wf}"
