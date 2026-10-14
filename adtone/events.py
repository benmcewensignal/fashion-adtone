"""The events register: every dated change Focal follows, in one list, and what each one sets off.

    python -m adtone.events            # writes data/results/events.json and prints what is due

The facts stay where they are kept by hand, each row with its source: creative leads in
reference/designers.csv, chief executives in reference/executives.csv, ambassadors in
reference/ambassadors.csv, owners from Wikidata (data/wikidata/houses.json), and the forward tests' facts
in forward/events.yml. This module only joins them, so there is one place to ask what happened at a house
and when, and it checks the joins:

- every appointment of a creative lead (womenswear or the whole house) whose first show falls after the
  designer-change template was frozen has its record under `designer_changes` in forward/events.yml, so
  no change escapes the forward calls;
- a record whose first show is known in designers.csv carries the same date;
- a departure with no successor named is on the watch list.

And it lists what each change is due: the first campaign reading (from the third month after the first
show, when the template's after window opens), readings at 12 and 24 months, and, for a registered change,
its adjudication date.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

import yaml

from . import config

REF = config.ROOT / "reference"
FORWARD = config.ROOT / "forward"
TEMPLATE_FROZEN = "2026-10-14"        # forward/designer-change-v1.md: first shows after this date are covered
TEMPLATE_ADJUDICATE_MONTHS = 18
SCOPES = ("womenswear", "both")


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _month(d: str) -> str:
    return str(d)[:7]


def _shift(month: str, n: int) -> str:
    k = int(month[:4]) * 12 + int(month[5:7]) - 1 + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def _first_source(s: str) -> str:
    return (s or "").split(" | ")[0].strip()


def load_records(path: Path | None = None) -> list[dict]:
    p = path or FORWARD / "events.yml"
    if not p.exists():
        return []
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return doc.get("designer_changes") or []


def creative_leads(rows: list[dict] | None = None) -> list[dict]:
    out = []
    for r in rows if rows is not None else _rows(REF / "designers.csv"):
        base = {"house": r["house_id"], "who": r["designer"], "scope": r["scope"], "verified": r["verified"] == "true",
                "source": _first_source(r["source_urls"])}
        for col, kind in (("announced_date", "creative lead appointed"), ("first_show_date", "first show"),
                          ("left_announced_date", "creative lead leaving"), ("last_show_date", "last show")):
            if len(r[col]) >= 7:
                out.append({**base, "kind": kind, "date": r[col]})
    return out


def chief_executives() -> list[dict]:
    return [{"house": r["house_id"], "who": r["executive"], "role": r["role"], "kind": "chief executive appointed",
             "date": r["announced_date"], "verified": r["verified"] == "true", "source": r["source_url"]}
            for r in _rows(REF / "executives.csv") if len(r["announced_date"]) >= 7 and not r["executive"].startswith("(")]


def ambassadors() -> list[dict]:
    return [{"house": r["house_id"], "who": r["person"], "role": r["role"], "category": r["category"],
             "kind": "ambassador named", "date": r["announced_date"], "verified": r["verified"] == "true",
             "source": _first_source(r["source_urls"])}
            for r in _rows(REF / "ambassadors.csv") if len(r["announced_date"]) >= 7 and not r["person"].startswith("(")]


def owners() -> list[dict]:
    p = config.DATA / "wikidata" / "houses.json"
    if not p.exists():
        return []
    facts = json.loads(p.read_text(encoding="utf-8"))
    out = []
    for h, f in facts.items():
        for prop in ("owned_by", "parent"):
            for r in f.get(prop) or []:
                s = str(r.get("start") or "")
                if len(s) >= 7 and s[5:7] != "00":
                    out.append({"house": h, "who": str(r.get("value")), "kind": "owner from", "date": s[:10],
                                "verified": False, "source": f"https://www.wikidata.org/wiki/{f.get('item', '')}"})
    return out


def register() -> list[dict]:
    rows = creative_leads() + chief_executives() + ambassadors() + owners()
    return sorted(rows, key=lambda e: (e["date"], e["house"], e["kind"]))


def _matches(rec: dict, r: dict) -> bool:
    """A designer_changes record and a designers.csv appointment row are the same change."""
    if rec["house"] != r["house_id"]:
        return False
    if rec.get("successor") and rec["successor"] == r["designer"]:
        return True
    return bool(rec.get("announced")) and str(rec["announced"])[:10] == r["announced_date"][:10]


def checks(designers: list[dict] | None = None, records: list[dict] | None = None) -> dict:
    designers = designers if designers is not None else _rows(REF / "designers.csv")
    records = records if records is not None else load_records()
    unregistered, mismatched, watch = [], [], []
    named = [r for r in designers if r["scope"] in SCOPES and r["designer"].strip().lower() != "design studio"]
    for r in named:
        first = r["first_show_date"]
        pending = (first and first > TEMPLATE_FROZEN) or (not first and r["announced_date"] > TEMPLATE_FROZEN[:4] + "-01-01"
                                                           and not r["left_announced_date"])
        if not pending:
            continue
        rec = next((x for x in records if _matches(x, r)), None)
        if rec is None:
            unregistered.append({"house": r["house_id"], "who": r["designer"], "announced": r["announced_date"],
                                 "first_show": first or None})
        elif first and str(rec.get("first_show") or "")[:10] != first[:10]:
            mismatched.append({"house": r["house_id"], "who": r["designer"], "designers_csv": first,
                               "events_yml": rec.get("first_show")})
    # a departure is open until a later appointment at the same house, by a named lead or the studio
    for r in designers:
        if r["scope"] not in SCOPES or not r["left_announced_date"]:
            continue
        later = [x for x in designers if x["house_id"] == r["house_id"] and x["scope"] in SCOPES and x is not r
                 and (x["announced_date"] or x["first_show_date"] or "") >= r["left_announced_date"][:10]]
        named_later = [x for x in later if x["designer"].strip().lower() != "design studio"]
        if not named_later and r["left_announced_date"] >= "2025-01-01":
            watch.append({"house": r["house_id"], "left": r["designer"], "left_announced": r["left_announced_date"],
                          "for_now": later[0]["designer"] if later else None,
                          "record": any(x["house"] == r["house_id"] and not x.get("successor") for x in records)})
    return {"unregistered": unregistered, "mismatched": mismatched, "watch": watch}


def due(designers: list[dict] | None = None, records: list[dict] | None = None) -> list[dict]:
    """What each change of creative lead since 2025 is due, by month: the first campaign reading, then
    readings at 12 and 24 months from the first show, and a registered change's adjudication."""
    designers = designers if designers is not None else _rows(REF / "designers.csv")
    records = records if records is not None else load_records()
    out = []
    for r in designers:
        first = r["first_show_date"]
        if r["scope"] not in SCOPES or not first or first < "2025-01-01" or r["designer"].strip().lower() == "design studio":
            continue
        m = _month(first)
        for what, n in (("first campaign reading", 3), ("reading at 12 months", 12), ("reading at 24 months", 24)):
            out.append({"house": r["house_id"], "who": r["designer"], "first_show": first, "due": _shift(m, n), "what": what})
    for rec in records:
        if rec.get("first_show"):
            m = _month(rec["first_show"])
            out.append({"house": rec["house"], "who": rec.get("successor"), "first_show": str(rec["first_show"]),
                        "due": _shift(m, TEMPLATE_ADJUDICATE_MONTHS), "what": "designer-change-v1 adjudication"})
    return sorted(out, key=lambda x: (x["due"], x["house"], x["what"]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.events")
    ap.add_argument("--today", default=None)
    a = ap.parse_args(argv)
    today = a.today or date.today().isoformat()
    reg, chk, sched = register(), checks(), due()
    doc = {"generated_on": today, "events": reg, "checks": chk, "due": sched,
           "counts": {k: sum(1 for e in reg if e["kind"] == k) for k in sorted({e["kind"] for e in reg})}}
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.RESULTS_DIR / "events.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(reg)} events; " + ", ".join(f"{v} {k}" for k, v in doc["counts"].items()))
    for k in ("unregistered", "mismatched"):
        for x in chk[k]:
            print(f"{k}: {x}")
    for x in chk["watch"]:
        print(f"watch: {x['house']}, after {x['left']} (departure announced {x['left_announced']}), "
              f"{'recorded' if x['record'] else 'NOT recorded'} for the forward template")
    this_month = today[:7]
    for x in sched:
        if this_month <= x["due"] <= _shift(this_month, 3):
            print(f"due {x['due']}: {x['house']}, {x['who']}: {x['what']}")
    return 1 if chk["unregistered"] or chk["mismatched"] else 0


if __name__ == "__main__":
    sys.exit(main())
