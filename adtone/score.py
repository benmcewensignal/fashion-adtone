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



def json_schema(rubric: Rubric) -> dict:
    """The rubric as a JSON schema, so a constrained decoder cannot produce an answer outside it."""
    s, props = rubric.spec, {}
    for k, allowed in s["enums"].items():
        props[k] = {"type": "string", "enum": list(allowed)}
    for k, rule in s.get("lists", {}).items():
        props[k] = {"type": "array", "items": {"type": "string", "enum": list(rule["options"])},
                    "minItems": rule["min"], "maxItems": rule["max"], "uniqueItems": True}
    for k, rule in s.get("integers", {}).items():
        props[k] = {"type": "integer", "minimum": rule["min"], "maximum": rule["max"]}
    for k, rule in s.get("numbers", {}).items():
        props[k] = {"type": "number", "minimum": rule["min"], "maximum": rule["max"]}
    return {"type": "object", "properties": props, "required": sorted(props), "additionalProperties": False}



def gbnf(rubric: Rubric) -> str:
    """The rubric as an exact grammar: keys in a fixed order, every value one of the rubric's options,
    no whitespace anywhere. A reply has exactly one shape and ends with the last answer, so a constrained
    decoder cannot pad it out to the token limit. (Whether a picture without people has a gaze is left to
    validation: a grammar cannot see the image.)"""
    s = rubric.spec

    def lit(x: str) -> str:
        return '"\\"' + x + '\\""'

    rules, parts = [], []
    for i, k in enumerate(rubric.keys):
        name = "v" + str(i)
        if k in s["enums"]:
            rules.append(f"{name} ::= " + " | ".join(lit(v) for v in s["enums"][k]))
        elif k in s.get("lists", {}):
            rule = s["lists"][k]
            item = name + "i"
            rules.append(f"{item} ::= " + " | ".join(lit(v) for v in rule["options"]))
            if rule["min"] == 0:   # the words rubric: an empty list is an answer
                tail = ""
                for _ in range(rule["max"] - 1):
                    tail = f'( "," {item} {tail})?'
                rules.append(f'{name} ::= "[" ( {item} {tail})? "]"')
            else:
                tail = ""
                for _ in range(rule["max"] - rule["min"]):
                    tail = f'( "," {item} {tail})?'
                head = " ".join([item] + [f'"," {item}'] * (rule["min"] - 1))
                rules.append(f'{name} ::= "[" {head} {tail} "]"')
        elif k in s.get("integers", {}):
            rule = s["integers"][k]
            rules.append(f"{name} ::= " + " | ".join(f'"{n}"' for n in range(rule["min"], rule["max"] + 1)))
        else:
            rule = s["numbers"][k]
            assert (rule["min"], rule["max"]) == (0, 1), "the grammar writes numbers between 0 and 1 only"
            rules.append(f'{name} ::= "0" | "1" | "1.0" | "0." [0-9] | "0." [0-9] [0-9]')
        parts.append(('"," ' if i else "") + '"\\"' + k + '\\":" ' + name)
    root = 'root ::= "{" ' + " ".join(parts) + ' "}"'
    return "\n".join([root, *rules]) + "\n"

class ModalScorer:
    """The frozen rubric read by an open vision model with pinned weights, on a Modal GPU.

    The model sees only the image and the rubric, exactly as the Claude reader does. Its replies are
    constrained to the rubric's schema and then validated by the same code, so the two readers'
    observations are interchangeable in every analysis."""

    def __init__(self, rubric: Rubric, remote=None, revision: str | None = None):
        revision = config.open_model_revision() if revision is None else revision
        if not revision:
            raise ScoreError("API: the open model's weights are not pinned; run the reader workflow first")
        if remote is None:
            import modal
            remote = modal.Cls.from_name(config.MODAL_APP, "Reader")().read.remote
        self.remote, self.rubric, self.revision = remote, rubric, revision
        self.schema = json_schema(rubric)
        self.grammar = gbnf(rubric)
        self.instrument = f"{rubric.version}@{config.OPEN_MODEL.split('/')[-1].lower()}@{revision[:12]}"
        self.calls = 0

    def score_many(self, jpegs: list[bytes]) -> list[dict | ScoreError]:
        self.calls += 1
        try:
            texts = self.remote(jpegs, self.rubric.prompt, self.schema, self.grammar)
        except Exception as e:   # Modal client errors vary by version; any failure here is the service's
            raise ScoreError(f"API: Modal {e.__class__.__name__}") from None
        out: list[dict | ScoreError] = []
        for text in texts:
            try:
                out.append(parse(text, self.rubric))
            except ScoreError as e:
                out.append(e)
        return out

    def score(self, jpeg: bytes) -> dict:
        res = self.score_many([jpeg])[0]
        if isinstance(res, ScoreError):
            raise res
        return res


def make_scorer(rubric: Rubric, reader: str | None = None):
    reader = reader or config.READER
    if reader == "claude":
        return ClaudeScorer(rubric)
    if reader == "modal":
        return ModalScorer(rubric)
    raise ValueError(f"unknown reader {reader!r}")


def score_batch(scorer, jpegs: list[bytes]) -> list:
    """Several images in one call where the reader takes batches (the open model on Modal), one by one
    otherwise. Each answer is a dict, or the ScoreError of an invalid reply; a failure of the reader
    itself (an "API:" error) is raised, since nothing from the batch can be trusted."""
    if not jpegs:
        return []
    if hasattr(scorer, "score_many"):
        return scorer.score_many(jpegs)
    out = []
    for j in jpegs:
        try:
            out.append(scorer.score(j))
        except ScoreError as e:
            if str(e).startswith("API:"):
                raise
            out.append(e)
    return out

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
