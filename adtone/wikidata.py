"""Facts about each house from Wikidata, checked against the registry.

    python -m adtone.wikidata collect --run <id>
    python -m adtone.wikidata check

The registry (registry/houses.yml) is the only source of house identity, and its owners and designer
events are kept by hand. Wikidata keeps the same facts, edited by others, with dates: who owns the
house and since when, its parent organisation, its founding, and the people who directed it. This
reads those statements for every house, keeps them in a compact form with the names of the people
and companies resolved, and lists where Wikidata and the registry disagree. A disagreement is a
prompt to check a source, not an automatic correction: Wikidata lags and is sometimes wrong.

Directors are taken from "director / manager" (P1037) and "significant person" (P3342) statements;
the role each carried is kept from the statement's qualifiers, so creative directors can be told
from chief executives. Items come from adtone.wikiviews, which finds them from the English articles.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

from . import config, registry, store, wikiviews

PROPS = {"P127": "owned_by", "P749": "parent", "P571": "inception", "P112": "founded_by", "P159": "headquarters",
         "P1037": "director", "P3342": "significant_person"}
QUALIFIERS = {"P580": "start", "P582": "end", "P2868": "role", "P3831": "role", "P39": "role", "P1810": "named_as"}
CREATIVE = ("creative director", "artistic director", "designer", "fashion designer", "creative")


def paths() -> dict[str, Path]:
    return {"facts": config.DATA / "wikidata" / "houses.json", "state": config.STATE_DIR / "wikidata.json",
            "prov": config.PROV_DIR / "wikidata.jsonl"}


def _value(snak: dict):
    dv = (snak.get("datavalue") or {})
    v = dv.get("value")
    t = dv.get("type")
    if t == "wikibase-entityid":
        return v.get("id")
    if t == "time":
        return v.get("time", "").lstrip("+")[:10]   # precision is kept in Wikidata; a year shows as YYYY-00-00
    if t == "monolingualtext":
        return v.get("text")
    if t == "string":
        return v
    return None


def statements(entity: dict) -> dict[str, list[dict]]:
    """The properties we keep, each statement with its value and its dated, role-bearing qualifiers."""
    out: dict[str, list[dict]] = {}
    for pid, name in PROPS.items():
        for c in (entity.get("claims") or {}).get(pid, []):
            if c.get("rank") == "deprecated":
                continue
            row = {"value": _value(c.get("mainsnak") or {}), "rank": c.get("rank")}
            for qid, qname in QUALIFIERS.items():
                vals = [_value(q) for q in (c.get("qualifiers") or {}).get(qid, [])]
                vals = [v for v in vals if v]
                if vals:
                    row[qname] = vals[0] if qname in ("start", "end") else vals
            if row["value"]:
                out.setdefault(name, []).append(row)
    return out


def referenced(facts: dict[str, dict[str, list[dict]]]) -> set[str]:
    ids = set()
    for st in facts.values():
        for rows in st.values():
            for r in rows:
                for v in [r.get("value")] + list(r.get("role") or []):
                    if isinstance(v, str) and v.startswith("Q") and v[1:].isdigit():
                        ids.add(v)
    return ids


def label_of(entity: dict) -> str | None:
    return ((entity.get("labels") or {}).get("en") or {}).get("value")


def resolve_labels(facts: dict, labels: dict[str, str]) -> dict:
    def name(v):
        return labels.get(v, v) if isinstance(v, str) else v
    out = {}
    for hid, st in facts.items():
        out[hid] = {}
        for prop, rows in st.items():
            out[hid][prop] = [{**r, "value": name(r["value"]), **({"role": [name(x) for x in r["role"]]} if r.get("role") else {})}
                              for r in rows]
    return out


def is_creative(row: dict) -> bool:
    return any(any(k in str(role).lower() for k in CREATIVE) for role in row.get("role") or [])


def current(rows: list[dict]) -> list[dict]:
    return [r for r in rows if not r.get("end")]


def disagreements(reg: registry.Registry, facts: dict) -> list[str]:
    """Owner and current creative director, Wikidata against the registry."""
    out = []
    for h in reg.houses:
        f = facts.get(h.id)
        if not f:
            out.append(f"{h.id}: no Wikidata facts")
            continue
        owners = [r["value"] for r in current(f.get("owned_by", []) + f.get("parent", []))]
        if h.owner and owners and not any(h.owner.split()[0].lower() in str(o).lower() for o in owners):
            out.append(f"{h.id}: registry owner {h.owner!r}, Wikidata says {', '.join(map(str, owners))}")
        if h.debut:
            people = [r for r in f.get("director", []) + f.get("significant_person", []) if is_creative(r)]
            now = [r["value"] for r in current(people)]
            surname = h.debut.designer.split()[-1].lower()
            if now and not any(surname in str(p).lower() for p in now):
                out.append(f"{h.id}: registry debut {h.debut.designer} ({h.debut.date}), Wikidata's current creative lead: "
                           f"{', '.join(map(str, now))}")
    return out


def collect(sess, reg: registry.Registry, run: str, sleep=time.sleep) -> dict:
    P = paths()
    items = (store.read_state(wikiviews.paths()["state"]).get("items") or {})
    if not items:
        en = (store.read_state(config.STATE_DIR / "attention.json").get("titles") or {})
        items = wikiviews.items_for(sess, en, sleep=sleep)
    ents = wikiviews.entities(sess, sorted(set(items.values())), "claims|labels", sleep=sleep)
    raw = {hid: statements(ents.get(q, {})) for hid, q in items.items()}
    need = sorted(referenced(raw))
    named = wikiviews.entities(sess, need, "labels", sleep=sleep) if need else {}
    labels = {q: label_of(e) for q, e in named.items() if label_of(e)}
    facts = resolve_labels(raw, labels)
    facts = {hid: {"item": items[hid], "label": label_of(ents.get(items[hid], {})), **f} for hid, f in facts.items()}
    store.write_state(P["facts"], facts)
    issues = disagreements(reg, facts)
    out = {"run": run, "updated_at": store.utc_now(), "houses": len(facts), "disagreements": issues}
    store.write_state(P["state"], out)
    store.append_jsonl(P["prov"], [{k: v for k, v in out.items() if k != "disagreements"} | {"n_disagreements": len(issues)}])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.wikidata")
    ap.add_argument("stage", choices=["collect", "check"])
    ap.add_argument("--run", default=config.run_id())
    a = ap.parse_args(argv)
    if a.stage == "collect":
        try:
            out = collect(wikiviews.session(), registry.load(), a.run)
        except Exception as e:
            out = {"run": a.run, "updated_at": store.utc_now(), "crashed": f"{e.__class__.__name__}: {str(e)[:300]}"}
            store.append_jsonl(paths()["prov"], [out])
            print(f"::error::wikidata crashed, recorded in provenance: {out['crashed']}")
            return 1
        for d in out["disagreements"]:
            print(f"::warning::{d}")
        print({k: v for k, v in out.items() if k != "disagreements"})
        return 0
    facts = store.read_state(paths()["facts"])
    for d in disagreements(registry.load(), facts):
        print(d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
