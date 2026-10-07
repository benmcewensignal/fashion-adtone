"""Press: daily news tone and volume for every house, from GDELT.

    python -m adtone.press collect --run <id> [--budget-min 40]
    python -m adtone.press probe --run <id>

The reception term for adtone.runway: how the news wrote about a house in the days after a show,
against how it usually writes about it. GDELT's DOC 2.0 API needs no key and searches global news from
1 January 2017. A request returns a daily timeline when GDELT judges the span short enough, so each house
asks for all its history at once first, then a year, then 90 days, keeping the longest span that comes
back day by day; requests are politely spaced and the backfill resumes across runs until complete.

Tone is GDELT's average tone of the matching articles: the tone of news coverage, not fashion
criticism. Several house names are ambiguous (Celine, Hermes, Valentino, Chloe, Tom Ford), so those
houses search with fashion context. The queries are part of the instrument: recorded with every row
and frozen with Amendment 2.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from . import config, registry, store

API = "https://api.gdeltproject.org/api/v2/doc/doc"
START = date(2017, 1, 1)       # the DOC 2.0 API's fixed horizon
SPANS = (3700, 366, 90)        # window lengths tried in turn: all of history in one request where GDELT answers
WINDOW_DAYS = SPANS[0]         # it day by day, else a year, else 90 days, which always comes back daily.
SHORT_DAYS = SPANS[-1]         # Fewer requests matter: GDELT refuses GitHub's shared addresses much of the time.
SLEEP_S = 8.0                  # GDELT asks for one request every five seconds; shared runner addresses need more
RATE_TEXT = ("limit requests", "rate limit", "too many requests")
RATE_PAUSE_S = 300.0           # a refusal is waited out twice per run before the run gives up
RATE_PAUSES = 2
UA = "fashion-adtone research (press series; contact via the repository)"
CONTEXT = "(fashion OR runway OR collection OR handbag OR couture)"
QUERIES = {   # houses whose names mean something else too
    "celine": f'"Celine" {CONTEXT}',
    "hermes": f'"Hermes" {CONTEXT}',
    "valentino": f'"Valentino" {CONTEXT}',
    "chloe": f'"Chloe" {CONTEXT}',
    "saint_laurent": f'"Saint Laurent" {CONTEXT}',
    "tom_ford": f'"Tom Ford" {CONTEXT}',
    "alaia": '"Alaia"',
    "dolce_gabbana": '"Dolce" "Gabbana"',
    "brunello_cucinelli": '"Cucinelli"',
    "margiela": '"Margiela"',
    "dior": '(Dior OR "Christian Dior")',     # a quoted four-letter phrase is too short for GDELT
}


class RateLimited(RuntimeError):
    """GDELT kept refusing: stop the run and resume next time, rather than hammer on house by house."""


class NotDaily(RuntimeError):
    """The timeline came back coarser than daily for this span."""


def query_for(house: registry.House) -> str:
    return QUERIES.get(house.id, f'"{house.name}"')


def paths() -> dict[str, Path]:
    return {"dir": config.DATA / "press", "state": config.STATE_DIR / "press.json",
            "prov": config.PROV_DIR / "press.jsonl"}


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s


def _stamp(d: date, end: bool = False) -> str:
    return d.strftime("%Y%m%d") + ("235959" if end else "000000")


def parse_timeline(obj: dict) -> dict[str, dict]:
    """date -> {"value": ..., "norm": ...} from a DOC 2.0 timeline response. Empty when nothing matched."""
    out: dict[str, dict] = {}
    for series in (obj or {}).get("timeline", []) or []:
        for pt in series.get("data", []) or []:
            raw = str(pt.get("date", ""))
            if len(raw) >= 8 and raw[:8].isdigit():
                out[f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"] = {"value": pt.get("value"), "norm": pt.get("norm")}
        break   # one series per mode
    return out


def _refused(r) -> bool:
    """GDELT refuses with 429, with a 5xx, or with HTTP 200 and a plain-text plea to slow down."""
    if r.status_code == 429 or r.status_code >= 500:
        return True
    head = (r.text or "")[:300].lower()
    return r.status_code == 200 and not head.lstrip().startswith(("{", "[")) and any(t in head for t in RATE_TEXT)


def fetch(sess, query: str, start: date, end: date, mode: str, sleep=time.sleep, retries: int = 3) -> dict[str, dict]:
    params = {"query": query, "mode": mode, "format": "json",
              "startdatetime": _stamp(start), "enddatetime": _stamp(end, True)}
    last = ""
    for attempt in range(retries + 1):
        r = sess.get(API, params=params, timeout=60)
        if _refused(r):
            last = f"HTTP {r.status_code}: {(r.text or '').strip()[:120]}"
            if attempt < retries:
                sleep(30.0 * 2 ** attempt)     # 30 s, 60 s, 120 s
            continue
        if r.status_code != 200:
            raise RuntimeError(f"GDELT HTTP {r.status_code}")
        text = r.text.strip()
        if not text:
            return {}
        try:
            return parse_timeline(r.json())
        except ValueError:
            # GDELT answers a malformed or too-broad query with a plain-text message, not JSON
            raise RuntimeError(f"GDELT said: {text[:160]}") from None
    raise RateLimited(f"GDELT kept refusing ({last})")


def window(sess, query: str, start: date, end: date, sleep=time.sleep) -> list[dict]:
    """Daily rows for one window: article count, all-article total, and average tone."""
    vol = fetch(sess, query, start, end, "timelinevolraw", sleep=sleep)
    days = (end - start).days + 1
    if days > SHORT_DAYS and vol and len(vol) < 0.9 * days:
        raise NotDaily(f"{len(vol)} points for {days} days")
    sleep(SLEEP_S)
    tone = fetch(sess, query, start, end, "timelinetone", sleep=sleep)
    rows = []
    d = start
    while d <= end:
        k = d.isoformat()
        v, t = vol.get(k, {}), tone.get(k, {})
        n = int(v.get("value") or 0)
        rows.append({"date": k, "articles": n, "total": v.get("norm"),
                     "tone": (float(t["value"]) if n and t.get("value") is not None else None)})
        d += timedelta(days=1)
    return rows


def load_series(house: str) -> dict[date, dict]:
    return {date.fromisoformat(r["date"]): r for r in store.read_jsonl(paths()["dir"] / f"{house}.jsonl")}


def collect(sess, reg: registry.Registry, run: str, end: date | None = None, budget_s: float = 40 * 60,
            sleep=time.sleep, clock=time.monotonic) -> dict:
    """Extend every house's series to yesterday, window by window, until the budget runs out. Each window
    is written as soon as it arrives, so a run cut short keeps what it fetched and the next run resumes."""
    end = end or (datetime.now(timezone.utc).date() - timedelta(days=1))
    P = paths()
    P["dir"].mkdir(parents=True, exist_ok=True)
    state = store.read_state(P["state"])
    covered = state.get("covered", {})
    queries = {h.id: query_for(h) for h in reg.houses}
    span = state.get("span", {})
    failed, fetched, t0, limited, pauses, last_error = {}, 0, clock(), None, 0, None
    for h in reg.houses:
        if limited:
            break
        q = queries[h.id]
        if state.get("queries", {}).get(h.id, q) != q:
            covered.pop(h.id, None)      # a changed query is a different series: refetch it from the start
        nxt = date.fromisoformat(covered[h.id]) + timedelta(days=1) if h.id in covered else START
        path = P["dir"] / f"{h.id}.jsonl"
        while nxt <= end:
            if clock() - t0 > budget_s:
                break
            stop = min(end, nxt + timedelta(days=span.get(h.id, WINDOW_DAYS) - 1))
            try:
                rows = window(sess, q, nxt, stop, sleep=sleep)
            except NotDaily:
                cur = span.get(h.id, WINDOW_DAYS)
                span[h.id] = next((d for d in SPANS if d < cur), SHORT_DAYS)
                sleep(SLEEP_S)
                continue
            except RateLimited as e:
                last_error = str(e)[:200]
                if pauses < RATE_PAUSES and clock() - t0 + RATE_PAUSE_S < budget_s:
                    pauses += 1
                    sleep(RATE_PAUSE_S)   # wait the refusal out once more, then retry the same window
                    continue
                limited = str(e)
                break
            except (RuntimeError, requests.RequestException) as e:
                failed[h.id] = last_error = str(e)[:200]
                sleep(SLEEP_S)            # keep the pace even after a failure, or the next house is refused too
                break
            existing = {r["date"]: r for r in store.read_jsonl(path)} if path.exists() else {}
            existing.update({r["date"]: {**r, "query": q} for r in rows})
            store.write_jsonl(path, [existing[k] for k in sorted(existing)])
            covered[h.id] = stop.isoformat()
            fetched += 1
            nxt = stop + timedelta(days=1)
            sleep(SLEEP_S)
    complete = sorted(h for h in queries if covered.get(h) == end.isoformat())
    state = {"run": run, "updated_at": store.utc_now(), "covered": covered, "queries": queries, "span": span,
             "end": end.isoformat(), "complete": complete, "failed": failed, "rate_limited": limited,
             "last_error": last_error}
    store.write_state(P["state"], state)
    store.append_jsonl(P["prov"], [{"run": run, "updated_at": state["updated_at"], "windows": fetched,
                                    "complete": len(complete), "houses": len(queries), "failed": sorted(failed),
                                    "rate_limited": bool(limited), "pauses": pauses, "last_error": last_error}])
    return state


def probe(run: str) -> list[str]:
    st = store.read_state(paths()["state"])
    if st.get("run") != run:
        return [f"no press run {run} on file"]
    return [f"{h}: {why}" for h, why in sorted(st.get("failed", {}).items())]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--run", required=True)
    c.add_argument("--budget-min", type=float, default=40)
    p = sub.add_parser("probe")
    p.add_argument("--run", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "collect":
        st = collect(session(), registry.load(), a.run, budget_s=a.budget_min * 60)
        print(f"press: {len(st['complete'])} of {len(st['queries'])} houses complete to {st['end']}; "
              f"failed: {sorted(st['failed']) or 'none'}; "
              f"{'stopped: GDELT rate limit, resumes next run' if st['rate_limited'] else 'no rate limit'}")
        return 0
    errs = probe(a.run)
    for e in errs:
        print(f"::warning::{e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
