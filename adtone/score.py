"""Score one image against the frozen tone rubric.

The rubric file is the method: its prompt section is sent verbatim, and its JSON
block is what every response is validated against. A response that fails validation
after one re-ask is recorded as invalid, never repaired by guesswork.
"""
from __future__ import annotations

import base64
import hashlib
import json
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageStat

from . import config

_PROMPT = re.compile(r"<!-- PROMPT START -->\s*(.*?)\s*<!-- PROMPT END -->", re.S)
_SPEC = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)
_OBJ = re.compile(r"\{.*\}", re.S)


class ScoreError(Exception):
    pass


class RubricChanged(Exception):
    pass


@dataclass(frozen=True)
class Rubric:
    version: str
    prompt: str
    spec: dict

    @property
    def keys(self) -> list[str]:
        s = self.spec
        return sorted([*s["enums"], *s.get("lists", {}), *s.get("integers", {}), *s.get("numbers", {})])


def check_frozen(path: Path, sha_path: Path) -> str:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = sha_path.read_text().strip()
    if actual != expected:
        raise RubricChanged(f"{path.name} changed since it was frozen: a changed rubric is a new version, not an edit")
    return actual


def load_rubric(version: str = config.RUBRIC_VERSION, root: Path = config.RUBRIC_DIR, verify: bool = True) -> Rubric:
    path = root / f"{version}.md"
    if verify:
        check_frozen(path, root / f"{version}.sha256")
    text = path.read_text(encoding="utf-8")
    pm, sm = _PROMPT.search(text), _SPEC.search(text)
    if not pm or not sm:
        raise ValueError(f"{path.name}: missing prompt markers or JSON spec")
    spec = json.loads(sm.group(1))
    if spec.get("version") != version:
        raise ValueError(f"{path.name}: spec version {spec.get('version')} does not match {version}")
    return Rubric(version=version, prompt=pm.group(1), spec=spec)


def validate(obj: dict, rubric: Rubric) -> dict:
    s = rubric.spec
    expected = set(rubric.keys)
    got = set(obj)
    if got != expected:
        missing, extra = sorted(expected - got), sorted(got - expected)
        raise ScoreError(f"keys: missing {missing}, unexpected {extra}")
    for k, allowed in s["enums"].items():
        if obj[k] not in allowed:
            raise ScoreError(f"{k}={obj[k]!r} not in rubric")
    for k, rule in s.get("lists", {}).items():
        v = obj[k]
        if not isinstance(v, list) or not (rule["min"] <= len(v) <= rule["max"]) or any(x not in rule["options"] for x in v):
            raise ScoreError(f"{k}={v!r} violates rubric")
        if len(set(v)) != len(v):
            raise ScoreError(f"{k} repeats a value")
    for k, rule in s.get("integers", {}).items():
        v = obj[k]
        if isinstance(v, bool) or not isinstance(v, int) or not (rule["min"] <= v <= rule["max"]):
            raise ScoreError(f"{k}={v!r} violates rubric")
    for k, rule in s.get("numbers", {}).items():
        v = obj[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not (rule["min"] <= v <= rule["max"]):
            raise ScoreError(f"{k}={v!r} violates rubric")
    if obj.get("people") == "none" and obj.get("gaze") not in ("not_applicable", "no_face"):
        raise ScoreError("gaze given for an image with no people")
    return obj


def parse(text: str, rubric: Rubric) -> dict:
    m = _OBJ.search(text or "")
    if not m:
        raise ScoreError("no JSON object in response")
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ScoreError(f"bad JSON: {e}") from None
    if not isinstance(obj, dict):
        raise ScoreError("response is not an object")
    return validate(obj, rubric)


class ClaudeScorer:
    def __init__(self, rubric: Rubric, model: str = config.CLAUDE_MODEL, client=None, max_retries: int = 5,
                 sleep=time.sleep):
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client, self.rubric, self.model = client, rubric, model
        self.max_retries, self.sleep = max_retries, sleep
        self.instrument = f"{rubric.version}@{model}"
        self.calls = 0

    def _call(self, jpeg: bytes, nudge: bool) -> str:
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(jpeg).decode("ascii")}},
            {"type": "text", "text": "Score this image. Return only the JSON object."
             + (" Your previous reply did not match the required keys and values exactly." if nudge else "")},
        ]
        attempt = 0
        while True:
            self.calls += 1
            try:
                resp = self.client.messages.create(model=self.model, max_tokens=700, temperature=0,
                                                   system=self.rubric.prompt,
                                                   messages=[{"role": "user", "content": content}])
                return "".join(getattr(b, "text", "") for b in resp.content)
            except Exception as e:  # SDK error classes vary by version; retry on status alone
                status = getattr(e, "status_code", None)
                retryable = status in (408, 409, 429, 500, 502, 503, 504, 529) or e.__class__.__name__ in (
                    "APIConnectionError", "APITimeoutError")
                if not retryable or attempt >= self.max_retries:
                    raise ScoreError(f"API: {e.__class__.__name__} {status}") from None
                self.sleep(min(120, 4 * 2 ** attempt) + random.random())
                attempt += 1

    def score(self, jpeg: bytes) -> dict:
        try:
            return parse(self._call(jpeg, nudge=False), self.rubric)
        except ScoreError as first:
            if str(first).startswith("API:"):
                raise
            return parse(self._call(jpeg, nudge=True), self.rubric)


class FakeScorer:
    """Deterministic valid output from image brightness, for tests."""

    def __init__(self, rubric: Rubric):
        self.rubric = rubric
        self.instrument = f"{rubric.version}@fake"
        self.calls = 0

    def score_image(self, img: Image.Image) -> dict:
        self.calls += 1
        lum = ImageStat.Stat(img.convert("L")).mean[0] / 255.0
        return validate({
            "creative_type": "brand_image", "category": "ready_to_wear",
            "light": "high_key" if lum > 0.6 else "low_key" if lum < 0.35 else "mixed",
            "colour_temperature": "neutral", "saturation": "moderate", "setting": "studio_plain",
            "people": "one", "gaze": "to_camera", "expression": "neutral", "pose": "posed_static",
            "framing": "medium", "primary_subject": "garment", "styling_register": "formal_tailored",
            "production": "polished_commercial", "text_in_image": "logo_only", "mood": ["serene"],
            "street_couture_axis": 1 + min(4, int(lum * 5)), "confidence": 0.8,
        }, self.rubric)

    def score(self, jpeg: bytes) -> dict:
        import io
        return self.score_image(Image.open(io.BytesIO(jpeg)).convert("RGB"))
