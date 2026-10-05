"""A phone-friendly status page, built from the data files and published to GitHub Pages.

    python -m adtone.status --out site
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from . import analysis, config, registry, store


def _last(path: Path) -> dict:
    rows = store.read_jsonl(path)
    return rows[-1] if rows else {}


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def gather() -> dict:
    reg = registry.load(config.REGISTRY_FILE)
    cands = _json(config.CANDIDATES_FILE)
    forward = {p.stem.replace("forward-", ""): _json(p) for p in sorted(config.RESULTS_DIR.glob("forward-*.json"))}
    return {
        "reg": reg, "candidates": cands, "collect_state": store.read_state(config.STATE_DIR / "collect.json"),
        "process_state": store.read_state(config.STATE_DIR / "process.json"),
        "collect_last": _last(config.PROV_DIR / "collect.jsonl"), "process_last": _last(config.PROV_DIR / "process.jsonl"),
        "gate": analysis.gate(reg), "summary": _json(config.RESULTS_DIR / "summary.json"), "forward": forward,
    }


def needs_you(d: dict) -> list[str]:
    out = []
    reg, sel = d["reg"], (d["candidates"].get("selected") or {})
    if not d["collect_state"].get("last_success"):
        out.append("No collection has succeeded yet. Check that META_AD_LIBRARY_TOKEN is set, then run collect.")
    picks = [h.id for h in reg.houses if not h.page_ids and sel.get(h.id, {}).get("rule") == "exact"]
    if picks and reg.status != "FROZEN":
        out.append(f"{len(picks)} automatic page picks await confirmation. Run confirm-pages with accept set to all.")
    loose = [h.id for h in reg.houses if not h.page_ids and h.id in sel and sel[h.id].get("rule") != "exact"]
    if loose:
        out.append(f"Suggestions need checking by eye: {', '.join(loose)}. Accept with overrides as house=id.")
    none = [h.id for h in reg.houses if not h.page_ids and h.id not in sel and h.group != "watch"]
    if none and d["candidates"]:
        out.append(f"No page found automatically for {', '.join(none)}. Find the id in the Ad Library URL (view_all_page_id).")
    token = d["collect_state"].get("token") or {}
    if token.get("days_left") is not None and token["days_left"] < 14:
        out.append(f"The Meta token expires in {token['days_left']} days. Replace META_AD_LIBRARY_TOKEN.")
    return out


def render(d: dict) -> str:
    e = html.escape
    reg, sel = d["reg"], (d["candidates"].get("selected") or {})
    cl, pl = d["collect_last"], d["process_last"]
    per_house = cl.get("per_house") or {}
    rows = []
    for h in reg.houses:
        s = sel.get(h.id) or {}
        if h.page_ids:
            state, page = "confirmed", ", ".join(h.page_ids)
        elif s.get("rule") == "exact":
            state, page = "auto pick", f"{s.get('page_name', '')} ({s.get('page_id')})"
        elif s:
            state, page = "suggestion", f"{s.get('page_name', '')} ({s.get('page_id')})"
        else:
            state, page = "none", ""
        rows.append(f"<tr><td>{e(h.name)}<span class=g>{e(h.group)}</span></td><td class='s {state.replace(' ', '-')}'>"
                    f"{state}</td><td>{e(page)}</td><td class=n>{per_house.get(h.id, '')}</td></tr>")
    tasks = needs_you(d)
    task_html = "".join(f"<li>{e(t)}</li>" for t in tasks) or "<li>Nothing. It is running by itself.</li>"
    fwd = "".join(f"<li><b>{e(k)}</b>: {e(str(v.get('status', 'not evaluated')))}</li>" for k, v in d["forward"].items()) \
        or "<li>Not evaluated yet.</li>"
    gate = "Waiting: " + "; ".join(d["gate"]) if d["gate"] else "Open: analysis runs weekly."
    rep = d["summary"].get("primary", {})
    h1 = (rep.get("event_study") or {}).get("h1", {}).get("supported", "n/a") if rep else "n/a"
    return f"""<!DOCTYPE html><html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>fashion-adtone status</title><style>
:root{{--bg:#fafbfc;--ink:#1d2430;--mute:#5a6472;--rule:#cdd6e0;--ok:#2f6b3a;--wait:#8a5a00;--no:#9b2c2c}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121821;--ink:#e3e8ee;--mute:#9aa6b5;--rule:#2c3949;--ok:#7cc48a;--wait:#e0b45c;--no:#ef8a8a}}}}
body{{margin:0;padding:max(1rem,env(safe-area-inset-top)) 1rem 3rem;background:var(--bg);color:var(--ink);
font:16px/1.5 -apple-system,BlinkMacSystemFont,"Helvetica Neue",Arial,sans-serif}}
main{{max-width:40rem;margin:0 auto}}h1{{font-size:1.4rem;margin:.5rem 0 .2rem}}h2{{font-size:1.05rem;margin:1.6rem 0 .4rem}}
.m{{color:var(--mute);font-size:.85rem}}ul{{padding-left:1.1rem;margin:.3rem 0}}li{{margin:.25rem 0}}
.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:.88rem}}
td{{padding:.45rem .5rem .45rem 0;border-bottom:1px solid var(--rule);vertical-align:top}}
.g{{display:block;color:var(--mute);font-size:.75rem}}.n{{text-align:right}}
.confirmed{{color:var(--ok)}}.auto-pick{{color:var(--wait)}}.suggestion{{color:var(--wait)}}.none{{color:var(--no)}}
</style></head><body><main>
<h1>fashion-adtone</h1><p class=m>Updated {e(store.utc_now())}. Registry {e(reg.status)}.</p>
<h2>Needs you</h2><ul>{task_html}</ul>
<h2>At a glance</h2><ul>
<li>Collection: {e(str(d['collect_state'].get('last_success', 'never')))}, {d['collect_state'].get('total_ads', 0)} ads on file{', partial last run' if cl.get('partial') else ''}.</li>
<li>Processing: {pl.get('processed', 0)} ads last run, {d['process_state'].get('pending_after', 'unknown')} pending, resolver {e(str(pl.get('resolver_used') or pl.get('resolver', 'n/a')))}.</li>
<li>Analysis: {e(gate)} H1: {e(str(h1))}.</li></ul>
<h2>Forward tests</h2><ul>{fwd}</ul>
<h2>Pages</h2><div class=scroll><table>{''.join(rows)}</table></div>
<p class=m>Last column: ads seen for the house in the latest collection run.</p>
</main></body></html>"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.status")
    ap.add_argument("--out", default="site")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render(gather()), encoding="utf-8")
    print(f"wrote {out / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
