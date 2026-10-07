"""Synthetic data with known ground truth, so every analysis is shown to recover a
planted effect, and to stay quiet without one, before it sees real data.
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
from PIL import Image

from .concepts import Concept, assign_blocks


# ---------- HTTP fakes ----------

class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None, content=b""):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else (json.dumps(payload) if payload is not None else "")
        self.content = content
        self.raw = None

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Routes GETs to a handler(url, params) -> FakeResponse and records every call. POSTs go to
    post_handler(url, params, json, data) when one is given."""

    def __init__(self, handler, post_handler=None):
        self.handler = handler
        self.post_handler = post_handler
        self.calls: list[tuple[str, dict | None]] = []
        self.posts: list[tuple[str, dict | None, object]] = []
        self.headers: dict = {}

    def get(self, url, params=None, timeout=None, stream=False, **kw):
        self.calls.append((url, dict(params) if params else None))
        return self.handler(url, params)

    def post(self, url, params=None, json=None, data=None, timeout=None, headers=None, **kw):
        self.posts.append((url, dict(params) if params else None, json if json is not None else data))
        if self.post_handler is None:
            raise AssertionError(f"unexpected POST {url}")
        return self.post_handler(url, params, json, data)


def api_ad(ad_id: str, page_id: str, start: str, stop: str | None = None, body: str = "The new collection",
           reach: int = 1000) -> dict:
    return {
        "id": ad_id, "page_id": page_id, "page_name": f"Page {page_id}",
        "ad_creation_time": start, "ad_delivery_start_time": start, "ad_delivery_stop_time": stop,
        "ad_creative_bodies": [body], "ad_creative_link_titles": ["Shop now"],
        "publisher_platforms": ["facebook", "instagram"], "languages": ["fr"],
        "eu_total_reach": reach, "target_ages": ["18", "65+"], "target_gender": "All",
        "target_locations": [{"name": "France", "num_obfuscated": 0, "type": "countries", "excluded": False}],
        "age_country_gender_reach_breakdown": [
            {"country": "FR", "age_gender_breakdowns": [
                {"age_range": "25-34", "male": 100, "female": 300, "unknown": 5},
                {"age_range": "35-44", "male": 80, "female": 200}]}],
        "beneficiary_payers": [{"payer": "House SA", "beneficiary": "House SA", "current": True}],
        "ad_snapshot_url": f"https://www.facebook.com/ads/archive/render_ad/?id={ad_id}&access_token=SECRETTOKEN123456",
    }


def render_page(creative_urls: list[str], avatar: str | None = None, escaped: bool = False) -> str:
    imgs = "".join(f'<img src="{u}" class="x1">' for u in creative_urls)
    av = f'<img src="{avatar}" width="60">' if avatar else ""
    html = f"<html><body>{av}<div>{imgs}</div><script src='https://static.xx.fbcdn.net/rsrc.php/v3/a.js'></script></body></html>"
    if escaped:
        payload = json.dumps({"images": creative_urls}).replace("/", "\\/")
        html = f"<html><body>{av}<script>var d = {payload};</script></body></html>"
    return html


# ---------- images ----------

def toned_image(brightness: float, warmth: float = 0.0, size: tuple[int, int] = (800, 1000), seed: int = 0) -> Image.Image:
    rng = np.random.default_rng(seed)
    base = np.full((size[1], size[0], 3), brightness * 255.0)
    base[..., 0] += warmth * 40
    base[..., 2] -= warmth * 40
    base += rng.normal(0, 18, base.shape)
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))


def jpeg_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


# ---------- concept world ----------

@dataclass
class World:
    concepts: list[Concept]
    truth: dict = field(default_factory=dict)


def _unit(v):
    return v / np.linalg.norm(v)


def make_world(houses: dict[str, dict], dim: int = 16, start: date = date(2025, 10, 10), days: int = 360,
               campaigns: int = 10, per_campaign: tuple[int, int] = (3, 6), noise: float = 0.30,
               campaign_sd: float = 0.25, season_amp: float = 0.6, mover: tuple[str, str, float] | None = None,
               seed: int = 0, movers: list[tuple[str, str, float]] | None = None) -> World:
    """houses: id -> {"debut": date|None, "shift": float}. A shift starts 45 days after the debut.

    Every house shares a seasonal drift (which the control field must remove) and each
    campaign shares a shoot effect (which block permutation must absorb).
    """
    rng = np.random.default_rng(seed)
    planted = ([mover] if mover else []) + list(movers or [])
    season_dir = _unit(rng.normal(size=dim))
    base = {h: _unit(rng.normal(size=dim)) for h in houses}
    new_dir = {h: _unit(rng.normal(size=dim)) for h in houses}
    concepts: list[Concept] = []
    for h, spec in houses.items():
        debut = spec.get("debut")
        onset = debut + timedelta(days=45) if debut else None
        step = days / campaigns
        for k in range(campaigns):
            launch = start + timedelta(days=int(k * step + rng.integers(0, 6)))
            shoot = rng.normal(0, campaign_sd, dim)
            post = onset is not None and launch >= onset
            centre = base[h]
            if post and spec.get("shift"):
                centre = _unit(base[h] + spec["shift"] * new_dir[h])
            for m in planted:
                if post and h == m[1]:
                    centre = _unit((1 - m[2]) * centre + m[2] * base[m[0]])
            for j in range(int(rng.integers(per_campaign[0], per_campaign[1] + 1))):
                d = launch + timedelta(days=int(rng.integers(0, 10)))
                t = (d - start).days
                v = centre + season_amp * np.sin(2 * np.pi * t / 365) * season_dir + shoot + rng.normal(0, noise, dim)
                light = "low_key" if post and spec.get("shift") else "high_key"
                out = {"creative_type": "brand_image", "category": "ready_to_wear", "light": light,
                       "colour_temperature": "neutral", "saturation": "moderate", "setting": "studio_plain",
                       "people": "one", "framing": "medium", "styling_register": "formal_tailored",
                       "production": "polished_commercial", "primary_subject": "garment",
                       "street_couture_axis": 2 if post and spec.get("shift") else 4,
                       "text_in_image": "none", "confidence": 0.8}
                concepts.append(Concept(house_id=h, concept_id=f"{h}:{k}:{j}", first_seen=d,
                                        ad_ids=[f"{h}{k}{j}"], shas=[f"{h}{k}{j}"], vec=_unit(v), outputs=[out]))
    assign_blocks(concepts)
    return World(concepts=concepts, truth={"houses": houses, "mover": mover, "movers": planted})
