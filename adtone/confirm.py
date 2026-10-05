"""Write confirmed page ids into registry/houses.yml, and optionally freeze it.

    python -m adtone.confirm --accept all [--overrides hermes=123,loro_piana=456] [--freeze]

`--accept all` takes every exact-name automatic pick; a comma list takes just those houses.
Looser suggestions and ids found by hand go through --overrides. Comments and layout are kept:
only page_ids lines and the status line change. A frozen registry is never edited.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from . import config, registry


def set_page_ids(text: str, house_id: str, ids: list[str]) -> str:
    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if re.match(rf"^\s{{2}}- \{{?id: {re.escape(house_id)}[,\s]", l)), None)
    if start is None:
        raise KeyError(house_id)
    flow = json.dumps([str(i) for i in ids])
    if "{id:" in lines[start]:
        lines[start] = re.sub(r"page_ids: \[[^\]]*\]", f"page_ids: {flow}", lines[start], count=1)
        return "".join(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^\s{2}- ", lines[j]):
            break
        m = re.match(r"^(\s*)page_ids:", lines[j])
        if m:
            lines[j] = f"{m.group(1)}page_ids: {flow}\n"
            return "".join(lines)
    raise ValueError(f"{house_id}: no page_ids line")


def apply(text: str, reg: registry.Registry, candidates: dict, accept: str, overrides: str, freeze: bool) -> tuple[str, list[str]]:
    if reg.status == "FROZEN":
        raise SystemExit("the registry is frozen: a change of pages is a v2 panel, not an edit")
    ids = {h.id for h in reg.houses}
    chosen: dict[str, str] = {}
    for pair in [p for p in (overrides or "").split(",") if p.strip()]:
        hid, _, pid = pair.partition("=")
        hid, pid = hid.strip(), pid.strip()
        if hid not in ids or not pid.isdigit():
            raise SystemExit(f"bad override {pair!r}: expected house_id=numeric page id")
        chosen[hid] = pid
    selected = (candidates or {}).get("selected") or {}
    wanted = [h.id for h in reg.houses] if accept.strip() == "all" else [x.strip() for x in accept.split(",") if x.strip()]
    for hid in wanted:
        if hid not in ids:
            raise SystemExit(f"unknown house {hid!r}")
        sel = selected.get(hid)
        if hid in chosen or reg.by_id(hid).page_ids or not sel:
            continue
        if sel.get("rule") != "exact" and accept.strip() == "all":
            continue   # looser matches need an explicit override
        chosen[hid] = str(sel["page_id"])
    for hid, pid in chosen.items():
        text = set_page_ids(text, hid, [pid])
    done = [f"{hid}={pid}" for hid, pid in sorted(chosen.items())]
    if freeze:
        tmp = config.ROOT / "registry" / ".houses.check.yml"
        tmp.write_text(text, encoding="utf-8")
        try:
            check = registry.load(tmp)
        finally:
            tmp.unlink()
        missing = [h.id for h in check.houses if h.group != "watch" and not h.page_ids]
        if missing:
            raise SystemExit(f"cannot freeze: no confirmed page for {', '.join(missing)}")
        text, n = re.subn(r"^status: DRAFT\b", "status: FROZEN", text, count=1, flags=re.M)
        if n != 1:
            raise SystemExit("no 'status: DRAFT' line to freeze")
    return text, done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.confirm")
    ap.add_argument("--accept", default="all")
    ap.add_argument("--overrides", default="")
    ap.add_argument("--freeze", action="store_true")
    a = ap.parse_args(argv)
    path = config.REGISTRY_FILE
    reg = registry.load(path)
    cands = json.loads(config.CANDIDATES_FILE.read_text()) if config.CANDIDATES_FILE.exists() else {}
    text, done = apply(path.read_text(encoding="utf-8"), reg, cands, a.accept, a.overrides, a.freeze)
    path.write_text(text, encoding="utf-8")
    registry.load(path)   # raises if the result is invalid, before anything is committed
    print(f"confirmed {len(done)} pages: {', '.join(done) or 'none'}" + ("; registry frozen" if a.freeze else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
