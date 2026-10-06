"""Collect ads from the Meta Ad Library for the panel's pages.

    python -m adtone.collect resolve            # candidate page ids per house, for confirmation
    python -m adtone.collect run --mode backfill
    python -m adtone.collect run --mode incremental

The repository is itself an archive: an ad stays in it for a year after it last ran.
So a skipped weekly run loses nothing as long as the next run's window overlaps,
which is why incremental runs look back 35 days rather than 7. What the archive does
not keep is history older than a year, so the first backfill is the time-critical run.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import requests

from . import config, copyfeat, registry, store
from .adlib import AdLibraryClient, GraphError, TokenError, scrub

log = logging.getLogger("adtone.collect")


def _reach_breakdown(raw) -> dict:
    """Collapse age_country_gender_reach_breakdown into three small marginals."""
    by_age: Counter = Counter()
    by_gender: Counter = Counter()
    by_country: Counter = Counter()
    try:
        for item in raw or []:
            country = item.get("country") or "unknown"
            for ag in item.get("age_gender_breakdowns") or []:
                age = ag.get("age_range") or "unknown"
                for g in ("male", "female", "unknown"):
                    v = ag.get(g) or 0
                    if v:
                        by_age[age] += int(v)
                        by_gender[g] += int(v)
                        by_country[country] += int(v)
    except (AttributeError, TypeError, ValueError):
        return {"parse_error": True}
    return {"by_age": dict(sorted(by_age.items())), "by_gender": dict(sorted(by_gender.items())),
            "by_country": dict(sorted(by_country.items()))}


def normalise(raw: dict, house_id: str, rid: str) -> dict:
    """One stored row per ad. No snapshot URL (it can carry the token), no copy text."""
    locs = []
    for loc in raw.get("target_locations") or []:
        if isinstance(loc, dict):
            locs.append({"name": loc.get("name"), "type": loc.get("type"), "excluded": bool(loc.get("excluded"))})
    payers = []
    for bp in raw.get("beneficiary_payers") or []:
        if isinstance(bp, dict):
            payers.append({"payer": bp.get("payer"), "beneficiary": bp.get("beneficiary")})
    row = {
        "ad_id": str(raw["id"]),
        "house_id": house_id,
        "page_id": str(raw.get("page_id") or ""),
        "page_name": raw.get("page_name"),
        "created": raw.get("ad_creation_time"),
        "start": raw.get("ad_delivery_start_time"),
        "stop": raw.get("ad_delivery_stop_time"),
        "platforms": raw.get("publisher_platforms"),
        "languages": raw.get("languages"),
        "eu_total_reach": raw.get("eu_total_reach"),
        "target_ages": raw.get("target_ages"),
        "target_gender": raw.get("target_gender"),
        "target_locations": locs or None,
        "reach": _reach_breakdown(raw.get("age_country_gender_reach_breakdown")) if raw.get("age_country_gender_reach_breakdown") else None,
        "beneficiary_payers": payers or None,
        "copy": copyfeat.features(raw.get("ad_creative_bodies")),
        "title": copyfeat.features(raw.get("ad_creative_link_titles")),
        "first_collected": rid,
        "last_collected": rid,
    }
    return {k: v for k, v in row.items() if v is not None}


def _merge(old: dict, new: dict) -> dict:
    out = {**old, **new}
    out["first_collected"] = old.get("first_collected", new["first_collected"])
    return out


def _token() -> str:
    tok = os.environ.get("META_AD_LIBRARY_TOKEN", "")
    if not tok:
        raise TokenError("META_AD_LIBRARY_TOKEN is not set")
    return tok


def check_token_expiry(token: str, session: requests.Session | None = None) -> dict:
    """If META_APP_ID and META_APP_SECRET are set, ask Graph when the token expires."""
    app_id, secret = os.environ.get("META_APP_ID"), os.environ.get("META_APP_SECRET")
    if not (app_id and secret):
        return {"checked": False}
    s = session or requests.Session()
    try:
        r = s.get(f"{config.GRAPH_BASE}/{config.GRAPH_API_VERSION}/debug_token",
                  params={"input_token": token, "access_token": f"{app_id}|{secret}"}, timeout=30)
        d = (r.json() or {}).get("data") or {}
    except (requests.RequestException, ValueError) as e:
        return {"checked": False, "error": scrub(e)}
    exp = d.get("expires_at") or d.get("data_access_expires_at")
    out = {"checked": True, "is_valid": d.get("is_valid")}
    if exp:
        when = datetime.fromtimestamp(int(exp), tz=timezone.utc)
        out["expires_at"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")
        out["days_left"] = (when - datetime.now(timezone.utc)).days
    return out


# Pages that carry a house's name but not its fashion advertising.
EXCLUDE_PAGE_TERMS = ("beauty", "fragrance", "parfum", "perfume", "makeup", "make up", "cosmetic", "eyewear",
                      "outlet", "vintage", "resale", "pre owned", "preowned", "second hand", "secondhand",
                      "preloved", "pre loved", "kids", "home", "watches", "jewel", "joaillerie")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def select_page(house: registry.House, ranked: list[dict]) -> dict | None:
    """The house's own page among the candidates: an exact name match if there is one,
    otherwise a page whose name contains the house name as whole words, excluding beauty,
    fragrance, reseller and similar pages. Most ads wins within each rule."""
    names = {_norm(house.name)} | {_norm(t) for t in house.search_terms}
    exact, contains = [], []
    for c in ranked:
        n = _norm(c.get("page_name") or "")
        if not n or any(t in n for t in EXCLUDE_PAGE_TERMS):
            continue
        if n in names:
            exact.append(c)
        elif any(re.search(rf"(^| ){re.escape(x)}( |$)", n) for x in names if x):
            contains.append(c)
    for rule, pool in (("exact", exact), ("contains", contains)):
        if pool:
            best = max(pool, key=lambda c: c["n_ads"])
            return {**best, "rule": rule}
    return None


def deadline_first(reg: registry.Registry) -> list[registry.House]:
    """Houses in collection order: those whose old look leaves the archive soonest come first."""
    rank = {h: i for i, h in enumerate(config.DEADLINE_ORDER)}
    return sorted(reg.houses, key=lambda h: rank.get(h.id, len(rank)))   # stable: the rest keep registry order


def page_map(reg: registry.Registry, candidates: dict | None) -> tuple[dict[str, str], dict[str, str]]:
    """page id -> house id, and each house's status: confirmed (registry) or provisional (automatic)."""
    mapping: dict[str, str] = {}
    status: dict[str, str] = {}
    for h in reg.houses:
        if h.page_ids:
            for p in h.page_ids:
                mapping[p] = h.id
            status[h.id] = "confirmed"
    selected = (candidates or {}).get("selected") or {}
    for h in reg.houses:
        # Only exact name matches are collected without a person looking. A looser match is a
        # suggestion: keyword searches for some houses (Hermès most of all) are crowded with
        # resellers whose page names contain the house name.
        if h.page_ids or h.id not in selected or selected[h.id].get("rule") != "exact":
            continue
        p = str(selected[h.id]["page_id"])
        if p in mapping:
            continue   # already claimed by a confirmed house or an earlier selection
        mapping[p] = h.id
        status[h.id] = "provisional"
    return mapping, status


def resolve(client: AdLibraryClient, reg: registry.Registry, per_term: int = 2000) -> dict:
    """Which pages advertise under each house's name. For a person to confirm, not for use as is."""
    since = (date.today() - timedelta(days=config.BACKFILL_LOOKBACK_DAYS)).isoformat()
    out = {"generated_at": store.utc_now(), "note": "selected pages are provisional until confirmed in registry/houses.yml",
           "houses": {}, "selected": {}}
    for h in deadline_first(reg):
        counts: Counter = Counter()
        names: dict[str, str] = {}
        for term in h.search_terms:
            for row in client.search(countries=config.EU_UK_COUNTRIES, fields=["page_id", "page_name"],
                                     search_terms=term, date_min=since, max_rows=per_term):
                pid = str(row.get("page_id") or "")
                if pid:
                    counts[pid] += 1
                    names[pid] = row.get("page_name") or ""
        ranked = [{"page_id": p, "page_name": names[p], "n_ads": n} for p, n in counts.most_common()]
        out["houses"][h.id] = ranked[:15]
        sel = select_page(h, ranked)
        if sel:
            out["selected"][h.id] = sel
        log.info("%s: %d candidate pages", h.id, len(counts))
    return out


def run(client: AdLibraryClient, reg: registry.Registry, mode: str, rid: str, today: date | None = None) -> dict:
    today = today or date.today()
    candidates = None
    if config.CANDIDATES_FILE.exists():
        candidates = json.loads(config.CANDIDATES_FILE.read_text(encoding="utf-8"))
    if candidates is None and any(not h.page_ids for h in reg.houses):
        # First run: find each house's page automatically instead of waiting for a person.
        # Selections are provisional until copied into registry/houses.yml, and analysis
        # only ever uses pages confirmed there, so a wrong pick costs storage, not results.
        candidates = resolve(client, reg)
        store.write_state(config.CANDIDATES_FILE, candidates)
    pages, page_status = page_map(reg, candidates)
    if not pages:
        raise SystemExit("no pages to collect: none confirmed in registry/houses.yml and no automatic match")
    state_path = config.STATE_DIR / "collect.json"
    state = store.read_state(state_path)
    if mode == "backfill" or not state.get("last_success"):
        since = today - timedelta(days=config.BACKFILL_LOOKBACK_DAYS)
        mode = "backfill"
    else:
        last = date.fromisoformat(state["last_success"][:10])
        since = min(last, today) - timedelta(days=config.INCREMENTAL_LOOKBACK_DAYS)
    table = store.ShardedTable(config.ADS_DIR, "ad_id")
    per_house: Counter = Counter()
    new = updated = unknown_page = 0
    failure: GraphError | None = None
    try:
        rank = {h.id: i for i, h in enumerate(deadline_first(reg))}
        ordered = sorted(pages, key=lambda p: rank.get(pages[p], len(rank)))
        for raw in client.ads_for_pages(ordered, config.EU_UK_COUNTRIES, config.CORE_FIELDS,
                                        config.OPTIONAL_FIELDS, since.isoformat()):
            pid = str(raw.get("page_id") or "")
            hid = pages.get(pid)
            if hid is None:
                unknown_page += 1
                continue
            row = normalise(raw, hid, rid)
            if table.upsert(row, shard=store.month_of(row.get("start") or row.get("created")), merge=_merge):
                new += 1
            else:
                updated += 1
            per_house[hid] += 1
    except GraphError as e:
        # Keep what arrived before the failure: a backfill that dies after an hour should not
        # have to start again from nothing. last_success is not advanced, so the next run
        # covers the same window.
        failure = e
    shards = table.save()
    houses_without_ads = sorted(h for h in page_status if per_house[h] == 0)
    summary = {
        "run_id": rid, "mode": mode, "since": since.isoformat(), "finished_at": store.utc_now(),
        "api_version": config.GRAPH_API_VERSION, "api_calls": client.calls, "notes": client.notes,
        "new": new, "updated": updated, "seen": new + updated, "unknown_page_rows": unknown_page,
        "per_house": dict(sorted(per_house.items())), "houses_without_ads": houses_without_ads,
        "shards_written": shards, "total_ads": len(table.rows), "page_status": page_status,
        "houses_without_pages": sorted(h.id for h in reg.houses if h.id not in page_status),
        "suggested_pages": {k: v for k, v in ((candidates or {}).get("selected") or {}).items()
                            if v.get("rule") != "exact" and k not in page_status},
        "partial": failure is not None, "error": str(failure) if failure else None,
    }
    if failure is None:
        state.update({"last_success": summary["finished_at"], "last_mode": mode, "last_run": rid,
                      "total_ads": len(table.rows), "houses_without_ads": houses_without_ads})
        if mode == "backfill":
            state["last_backfill"] = summary["finished_at"]
        store.write_state(state_path, state)
    store.append_jsonl(config.PROV_DIR / "collect.jsonl", [summary])
    if failure is not None:
        raise failure
    return summary


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(prog="adtone.collect")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("resolve")
    r = sub.add_parser("run")
    r.add_argument("--mode", choices=["incremental", "backfill"], default="incremental")
    args = ap.parse_args(argv)
    reg = registry.load()
    rid = config.run_id()
    try:
        token = _token()
        client = AdLibraryClient(token)
        if args.cmd == "resolve":
            out = resolve(client, reg)
            store.write_state(config.CANDIDATES_FILE, out)
            store.append_jsonl(config.PROV_DIR / "collect.jsonl",
                               [{"run_id": rid, "mode": "resolve", "finished_at": store.utc_now(), "api_calls": client.calls}])
            print(f"wrote candidates for {len(out['houses'])} houses to {config.CANDIDATES_FILE.relative_to(config.ROOT)}")
            return 0
        expiry = check_token_expiry(token)
        if expiry.get("days_left") is not None and expiry["days_left"] < 10:
            print(f"::warning::Ad Library token expires in {expiry['days_left']} days; refresh META_AD_LIBRARY_TOKEN")
        summary = run(client, reg, args.mode, rid)
        summary["token"] = expiry
        if expiry.get("checked"):
            st = store.read_state(config.STATE_DIR / "collect.json")
            st["token"] = expiry
            store.write_state(config.STATE_DIR / "collect.json", st)
    except TokenError as e:
        print(f"::error::Ad Library token rejected ({e}). Refresh the long-lived token in the META_AD_LIBRARY_TOKEN secret.")
        return 3
    except GraphError as e:
        print(f"::error::Ad Library API error: {e}")
        return 4
    print(f"{summary['mode']}: {summary['new']} new, {summary['updated']} updated across "
          f"{len(summary['per_house'])} houses, {summary['api_calls']} API calls")
    for note in summary["notes"]:
        print(f"::warning::{note}")
    if summary["houses_without_ads"]:
        print(f"::warning::no ads in window for: {', '.join(summary['houses_without_ads'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
