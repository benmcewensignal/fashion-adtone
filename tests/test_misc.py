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
    reg = registry.load()
    assert reg.status in ("DRAFT", "FROZEN")
    treated, control = reg.group("treated"), reg.group("control")
    assert len(treated) == 10 and len(control) == 8 and [h.id for h in reg.group("watch")] == ["chloe"]
    assert all(h.debut and date(2025, 7, 1) <= h.debut.date <= date(2026, 3, 1) for h in treated)
    assert {"balenciaga", "gucci"} <= {h.id for h in treated}


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


def test_workflows_only_persist_as_workflows_the_guard_knows():
    import re
    import yaml
    root = Path(__file__).resolve().parent.parent / ".github" / "workflows"
    files = sorted(root.glob("*.yml"))
    assert len(files) == 6
    for f in files:
        text = f.read_text()
        yaml.safe_load(text)
        for wf in re.findall(r"persist\.sh (\w+)", text):
            assert wf in guard.ALLOW, f"{f.name} persists as unknown workflow {wf}"
