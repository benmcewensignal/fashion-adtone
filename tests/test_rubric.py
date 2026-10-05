import shutil

import pytest

from adtone import config
from adtone.score import RubricChanged, ScoreError, check_frozen, load_rubric, parse, validate

VALID = {
    "creative_type": "brand_image", "category": "leather_goods", "light": "low_key",
    "colour_temperature": "warm", "saturation": "muted", "setting": "interior", "people": "one",
    "gaze": "away", "expression": "neutral", "pose": "posed_static", "framing": "medium",
    "primary_subject": "bag", "styling_register": "formal_tailored", "production": "editorial_art",
    "text_in_image": "logo_only", "mood": ["austere", "intimate"], "street_couture_axis": 4, "confidence": 0.7,
}


def test_rubric_is_frozen_and_loads():
    r = load_rubric()
    assert r.version == "tone-v1"
    assert "Do not identify any person" in r.prompt
    assert len(r.keys) == 18


def test_every_spec_value_appears_in_the_prompt():
    r = load_rubric()
    for key, values in r.spec["enums"].items():
        assert key in r.prompt
        for v in values:
            assert v in r.prompt, f"{key}={v} is validated but never offered to the scorer"
    for key, rule in r.spec["lists"].items():
        for v in rule["options"]:
            assert v in r.prompt


def test_a_changed_rubric_is_refused(tmp_path):
    src = config.RUBRIC_DIR
    shutil.copy(src / "tone-v1.md", tmp_path / "tone-v1.md")
    shutil.copy(src / "tone-v1.sha256", tmp_path / "tone-v1.sha256")
    check_frozen(tmp_path / "tone-v1.md", tmp_path / "tone-v1.sha256")
    with (tmp_path / "tone-v1.md").open("a") as f:
        f.write("\nOne small clarification.\n")
    with pytest.raises(RubricChanged):
        load_rubric(root=tmp_path)


def test_validate_accepts_a_valid_response():
    assert validate(dict(VALID), load_rubric()) == VALID


@pytest.mark.parametrize("change", [
    {"light": "golden_hour"},
    {"mood": []},
    {"mood": ["austere", "serene", "playful", "ironic"]},
    {"mood": ["austere", "austere"]},
    {"street_couture_axis": 6},
    {"street_couture_axis": 3.5},
    {"street_couture_axis": True},
    {"confidence": 1.4},
    {"people": "none", "gaze": "to_camera"},
])
def test_validate_rejects_rubric_violations(change):
    with pytest.raises(ScoreError):
        validate({**VALID, **change}, load_rubric())


def test_validate_rejects_missing_and_extra_keys():
    r = load_rubric()
    bad = dict(VALID)
    del bad["light"]
    with pytest.raises(ScoreError, match="missing"):
        validate(bad, r)
    with pytest.raises(ScoreError, match="unexpected"):
        validate({**VALID, "brand_guess": "x"}, r)


def test_parse_takes_the_object_out_of_surrounding_text():
    import json
    r = load_rubric()
    assert parse("Here you go:\n" + json.dumps(VALID) + "\n", r)["light"] == "low_key"
    with pytest.raises(ScoreError):
        parse("I cannot score this image.", r)
