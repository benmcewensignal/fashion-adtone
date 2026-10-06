"""Campaign crew credits: the seed table for separating photographers from houses.

    python -m adtone.credits      # coverage, carry-over, crossings and designer moves

reference/credits.csv holds one row per credited person per campaign entry, read by hand from
public pages on 6 October 2026: models.com listings and work pages, campaign reviews and press.
Every row names the page it came from. Crew only: nobody who appears in the pictures is kept.

Nothing in the v1 pre-registered tests reads this file. It is for a later registered question,
separating photographers from houses, which needs to know who shot what. When the backcat
workflow runs, `from_backcat` turns its models.com rows into the same shape.

The creative director on a row is either named by its source (`cd_basis` source) or follows from
the house having one creative director throughout that period (`tenure`). Rows from a handover,
where neither holds, leave it blank rather than guess.
"""
from __future__ import annotations

import csv
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import config, registry

CREDITS_FILE = config.ROOT / "reference" / "credits.csv"
LINKS_FILE = config.ROOT / "reference" / "stated_links.csv"

COLUMNS = ["house", "client", "scope", "campaign", "work_type", "published", "creative_director",
           "cd_basis", "role", "person", "source_url", "notes"]
LINK_COLUMNS = ["subject", "subject_type", "person", "relation", "source_url"]
ROLES = ("photographer", "director", "videographer", "cinematography", "art_direction", "stylist",
         "casting", "set_design", "hair", "makeup")
FILM = ("director", "videographer", "cinematography")
WORK_TYPES = ("advertising", "lookbook", "social", "ecommerce")
SCOPES = ("fashion", "beauty")
CD_BASIS = ("source", "tenure", "")
DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
YEAR_RE = re.compile(r"\b(20\d{2})\b")

# models.com role labels, as adtone.backcat reads them, onto the roles used here. Every other
# label is dropped: models and talent are never kept, and "Designer" is the house's creative
# director, which has its own column rather than a crew row.
MODELS_ROLES = {
    "Photographer": "photographer", "Director": "director", "Director of Photography": "cinematography",
    "Art Director": "art_direction", "Artistic Director": "art_direction", "Creative Director": "art_direction",
    "Fashion Editor/Stylist": "stylist", "Fashion Director": "stylist", "Casting Director": "casting",
    "Set Designer": "set_design", "Hair Stylist": "hair", "Makeup Artist": "makeup",
}


def norm(name: str) -> str:
    """Match spellings of one credit: accents, '&' against 'and', punctuation and case."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s.replace("&", " and "))
    return " ".join(s.split())


def sources(field: str) -> list[str]:
    return [u.strip() for u in field.split("|") if u.strip()]


@dataclass(frozen=True)
class Credit:
    house: str
    client: str
    scope: str
    campaign: str
    work_type: str
    published: str
    creative_director: str
    cd_basis: str
    role: str
    person: str
    source_url: str
    notes: str = ""

    @property
    def key(self) -> str:
        return norm(self.person)

    @property
    def year(self) -> int | None:
        """Publication year, else the season year in the campaign name (a teaser can precede it)."""
        if self.published:
            return int(self.published[:4])
        m = YEAR_RE.search(self.campaign)
        return int(m.group(1)) if m else None

    def under(self, designer: str) -> bool:
        return bool(self.creative_director) and norm(self.creative_director) == norm(designer)


def problems(rows: list[dict], house_ids: set[str]) -> list[str]:
    out, seen = [], set()
    for i, r in enumerate(rows, start=2):   # file line numbers; the header is line 1
        at = f"line {i}"
        if r["house"] not in house_ids:
            out.append(f"{at}: unknown house {r['house']!r}")
        for col, allowed in (("scope", SCOPES), ("work_type", WORK_TYPES), ("role", ROLES), ("cd_basis", CD_BASIS)):
            if r[col] not in allowed:
                out.append(f"{at}: {col} {r[col]!r} is not one of {', '.join(a or '(blank)' for a in allowed)}")
        if bool(r["creative_director"]) != bool(r["cd_basis"]):
            out.append(f"{at}: creative_director and cd_basis are filled together or not at all")
        if r["published"] and not DATE_RE.match(r["published"]):
            out.append(f"{at}: published {r['published']!r} is not YYYY, YYYY-MM or YYYY-MM-DD")
        if not r["person"]:
            out.append(f"{at}: no person")
        urls = sources(r["source_url"])
        if not urls or not all(u.startswith("https://") for u in urls):
            out.append(f"{at}: every row needs its source page, as an https address")
        k = tuple(r[c] for c in COLUMNS if c != "notes")
        if k in seen:
            out.append(f"{at}: repeats an earlier row")
        seen.add(k)
    return out


def _read(path: Path, columns: list[str]) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        if rd.fieldnames != columns:
            raise ValueError(f"{path.name}: columns must be {columns}, found {rd.fieldnames}")
        return [{k: (v or "").strip() for k, v in r.items()} for r in rd]


def load(path: Path = CREDITS_FILE, house_ids: set[str] | None = None) -> list[Credit]:
    rows = _read(path, COLUMNS)
    if house_ids is None:
        house_ids = {h.id for h in registry.load().houses}
    bad = problems(rows, house_ids)
    if bad:
        raise ValueError(f"{path.name}: " + "; ".join(bad))
    return [Credit(**r) for r in rows]


def stated_links(path: Path = LINKS_FILE) -> list[dict]:
    rows = _read(path, LINK_COLUMNS)
    for i, r in enumerate(rows, start=2):
        if r["subject_type"] not in ("designer", "house") or not r["person"]:
            raise ValueError(f"{path.name} line {i}: needs a person and a subject_type of designer or house")
        if not sources(r["source_url"]) or not all(u.startswith("https://") for u in sources(r["source_url"])):
            raise ValueError(f"{path.name} line {i}: needs its source page, as an https address")
    return rows


def from_backcat(row: dict, house: str, client: str = "", scope: str = "fashion") -> list[Credit]:
    """A backcat campaign row (adtone.backcat.parse_work) as credit rows. Campaign pages only.

    backcat stores a month-only date as the first of the month, so its dates read as exact."""
    if (row.get("kind") or "").lower() != "campaign":
        return []
    people = row.get("people") or {}
    designers = people.get("Designer") or []
    cd = designers[0] if designers else ""
    skip = {norm(d) for d in designers}
    out = []
    for label, names in people.items():
        role = MODELS_ROLES.get(label)
        if not role:
            continue
        for name in names:
            if norm(name) in skip:
                continue
            out.append(Credit(house=house, client=client, scope=scope, campaign=row.get("title") or "",
                              work_type="advertising", published=row.get("published") or "",
                              creative_director=cd, cd_basis="source" if cd else "", role=role, person=name,
                              source_url=row["url"], notes="from the backcat workflow"))
    return out


def select(credits: list[Credit], role: str | None = "photographer", scope: str | None = "fashion",
           work_types: tuple[str, ...] | None = ("advertising",)) -> list[Credit]:
    """Filter; role 'film' means any film credit, None means every role."""
    roles = FILM if role == "film" else ((role,) if role else None)
    return [c for c in credits if (scope is None or c.scope == scope)
            and (work_types is None or c.work_type in work_types) and (roles is None or c.role in roles)]


def _before(published: str, debut: date) -> bool | None:
    """Published before the debut? None when the date's precision cannot say."""
    if not published:
        return None
    p = [int(x) for x in published.split("-")]
    if len(p) == 3:
        return date(*p) < debut
    if len(p) == 2:
        return None if (p[0], p[1]) == (debut.year, debut.month) else (p[0], p[1]) < (debut.year, debut.month)
    return None if p[0] == debut.year else p[0] < debut.year


def era(c: Credit, designer: str, debut: date) -> str | None:
    """'after' under the new creative director, 'before' under anyone earlier, None when unclear.

    A row with no creative director counts as before only if it was published before the debut."""
    if c.creative_director:
        return "after" if c.under(designer) else "before"
    return "before" if _before(c.published, debut) else None


def carryover(credits: list[Credit], house: registry.House, role: str = "photographer", **kw) -> dict:
    """Who worked for a treated house under its old and its new creative director, and who did both."""
    d = house.debut
    sets: dict[str, set[str]] = {"before": set(), "after": set()}
    names: dict[str, str] = {}
    for c in select(credits, role, **kw):
        if c.house != house.id:
            continue
        e = era(c, d.designer, d.date)
        if e:
            sets[e].add(c.key)
            names.setdefault(c.key, c.person)
    show = lambda ks: sorted(names[k] for k in ks)
    return {"house": house.id, "designer": d.designer, "before": show(sets["before"]),
            "after": show(sets["after"]), "shared": show(sets["before"] & sets["after"])}


def yearly_carryover(credits: list[Credit], house_id: str, role: str = "photographer", **kw) -> list[dict]:
    """Who worked for a house in consecutive years."""
    by_year: dict[int, set[str]] = defaultdict(set)
    names: dict[str, str] = {}
    for c in select(credits, role, **kw):
        if c.house == house_id and c.year:
            by_year[c.year].add(c.key)
            names.setdefault(c.key, c.person)
    ys = sorted(by_year)
    return [{"from": a, "to": b, "n_from": len(by_year[a]), "n_to": len(by_year[b]),
             "shared": sorted(names[k] for k in by_year[a] & by_year[b])}
            for a, b in zip(ys, ys[1:]) if b == a + 1]


def crossings(credits: list[Credit], role: str = "photographer", min_houses: int = 2, **kw) -> dict[str, list[str]]:
    """People credited at several houses: the links that let a photographer be told apart from a house."""
    houses: dict[str, set[str]] = defaultdict(set)
    names: dict[str, str] = {}
    for c in select(credits, role, **kw):
        houses[c.key].add(c.house)
        names.setdefault(c.key, c.person)
    out = {names[k]: sorted(v) for k, v in houses.items() if len(v) >= min_houses}
    return dict(sorted(out.items(), key=lambda kv: (-len(kv[1]), kv[0])))


def designer_moves(credits: list[Credit], scope: str = "fashion") -> list[dict]:
    """Creative directors credited at more than one house in the table, in order of their years there."""
    years: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    names: dict[str, str] = {}
    for c in credits:
        if c.scope != scope or not c.creative_director or c.year is None:
            continue
        k = norm(c.creative_director)
        names.setdefault(k, c.creative_director)
        years[k][c.house].append(c.year)
    moves = []
    for k, by_house in years.items():
        order = sorted(by_house, key=lambda h: (min(by_house[h]), max(by_house[h])))
        moves += [{"designer": names[k], "from": a, "to": b} for a, b in zip(order, order[1:])]
    return sorted(moves, key=lambda m: (m["designer"], m["from"]))


def imports(credits: list[Credit], scope: str = "fashion") -> list[dict]:
    """For each move, who worked with the designer at the old house and again at the new one, any role."""
    out = []
    for m in designer_moves(credits, scope):
        def crew(house):
            return [c for c in credits if c.scope == scope and c.house == house and c.under(m["designer"])]
        old = {c.key for c in crew(m["from"])}
        roles: dict[str, set[str]] = defaultdict(set)
        names: dict[str, str] = {}
        for c in crew(m["to"]):
            if c.key in old:
                roles[c.key].add(c.role)
                names.setdefault(c.key, c.person)
        out.append({**m, "people": {names[k]: sorted(v) for k, v in sorted(roles.items(), key=lambda kv: names[kv[0]])}})
    return out


def summary(credits: list[Credit], reg: registry.Registry, links: list[dict] | None = None) -> str:
    n_src = len({u for c in credits for u in sources(c.source_url)})
    houses = {c.house for c in credits}
    lines = [f"{len(credits)} credits from {n_src} source pages, {len(houses)} of {len(reg.houses)} registry houses.",
             f"Not covered: {', '.join(h.id for h in reg.houses if h.id not in houses) or 'none'}.", "",
             "Treated houses, advertising photographers under the old and the new creative director:"]
    for h in reg.group("treated"):
        co = carryover(credits, h)
        if not co["before"] or not co["after"]:
            side = "before" if not co["before"] else "after"
            lines.append(f"  {h.id}: nothing on file for the {side} side")
            continue
        lines.append(f"  {h.id}: {len(co['before'])} before, {len(co['after'])} after, "
                     f"kept: {', '.join(co['shared']) or 'none'}")
    lines += ["", "Controls, advertising photographers kept from one year to the next:"]
    for h in reg.group("control"):
        yc = yearly_carryover(credits, h.id)
        if not yc:
            lines.append(f"  {h.id}: no consecutive years on file")
        for y in yc:
            lines.append(f"  {h.id} {y['from']} to {y['to']}: {y['n_from']} then {y['n_to']}, "
                         f"kept: {', '.join(y['shared']) or 'none'}")
    lines += ["", "Advertising photographers credited at two or more houses:"]
    lines += [f"  {name}: {', '.join(hs)}" for name, hs in crossings(credits).items()]
    lines += ["", "Creative directors who moved between houses in the table, and who came with them:"]
    for m in imports(credits):
        crew = "; ".join(f"{p} ({', '.join(r)})" + (" [the designer]" if norm(p) == norm(m["designer"]) else "")
                         for p, r in m["people"].items())
        lines.append(f"  {m['designer']}, {m['from']} to {m['to']}: {crew or 'nobody on file'}")
    if links:
        lines += ["", "Relationships stated by a source rather than seen in the credits:"]
        lines += [f"  {r['subject']}: {r['person']}, {r['relation']}" for r in links]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    print(summary(load(), registry.load(), stated_links()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
