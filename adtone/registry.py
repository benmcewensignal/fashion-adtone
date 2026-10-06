"""The house panel: who is treated, who is a control, which pages they advertise from."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from .config import REGISTRY_FILE


@dataclass(frozen=True)
class Event:
    kind: str
    designer: str
    date: date
    verified: bool


@dataclass
class House:
    id: str
    name: str
    group: str
    owner: str
    search_terms: list[str]
    page_ids: list[str]
    events: list[Event] = field(default_factory=list)
    models_slug: str | None = None   # models.com client slug, when it is not the slugified name
    tier: str = "core"               # core: the frozen v1 panel; extension: added by Amendment 2

    @property
    def debut(self) -> Event | None:
        debuts = [e for e in self.events if e.kind == "designer_debut"]
        return debuts[-1] if debuts else None


@dataclass
class Registry:
    version: int
    status: str
    houses: list[House]

    def by_id(self, hid: str) -> House:
        for h in self.houses:
            if h.id == hid:
                return h
        raise KeyError(hid)

    @property
    def resolved(self) -> list[House]:
        return [h for h in self.houses if h.page_ids]

    def page_to_house(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for h in self.houses:
            for p in h.page_ids:
                if p in out:
                    raise ValueError(f"page {p} listed under both {out[p]} and {h.id}")
                out[p] = h.id
        return out

    def group(self, name: str) -> list[House]:
        return [h for h in self.houses if h.group == name]

    def core(self) -> "Registry":
        """The v1 panel the frozen tests read; extension houses are left out."""
        return Registry(version=self.version, status=self.status, houses=[h for h in self.houses if h.tier == "core"])

    def tier(self, name: str) -> list[House]:
        return [h for h in self.houses if h.tier == name]


def _as_date(v) -> date:
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


def load(path: Path = REGISTRY_FILE) -> Registry:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    houses = []
    seen = set()
    for h in raw["houses"]:
        if h["id"] in seen:
            raise ValueError(f"duplicate house id {h['id']}")
        seen.add(h["id"])
        if h["group"] not in ("treated", "control", "watch"):
            raise ValueError(f"{h['id']}: group must be treated, control or watch")
        events = [Event(e["kind"], e.get("designer", ""), _as_date(e["date"]), bool(e.get("verified", False)))
                  for e in h.get("events") or []]
        if h["group"] == "treated" and not any(e.kind == "designer_debut" for e in events):
            raise ValueError(f"{h['id']}: a treated house needs a designer_debut event")
        tier = h.get("tier", "core")
        if tier not in ("core", "extension"):
            raise ValueError(f"{h['id']}: tier must be core or extension")
        if h["group"] in ("control", "watch") and events:
            raise ValueError(f"{h['id']}: a {h['group']} house has no events by definition")
        houses.append(House(
            id=h["id"], name=h["name"], group=h["group"], owner=h.get("owner", ""),
            search_terms=list(h.get("search_terms") or [h["name"]]),
            page_ids=[str(p) for p in h.get("page_ids") or []],
            events=events,
            models_slug=h.get("models_slug"),
            tier=tier,
        ))
    reg = Registry(version=int(raw["version"]), status=str(raw["status"]), houses=houses)
    reg.page_to_house()  # raises on a page claimed twice
    return reg
