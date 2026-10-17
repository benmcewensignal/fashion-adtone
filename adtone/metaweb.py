"""Meta's Ad Library through a data vendor: the houses' ads on Facebook and Instagram as the public library
pages show them, read for us by an Apify actor that sells that reading through its API (the library's
own API is served only to people who have passed Meta's identity confirmation, and its pages refuse
automated readers; the vendor's reading is the vendor's). The rows take the shape adtone.collect gives
the API's ads, so everything downstream reads them alike.

    python -m adtone.metaweb collect --run <id> [--budget-min 60] [--houses gucci,dior]
    python -m adtone.metaweb probe --run <id>

Needs APIFY_TOKEN, an Apify account's API token, in the repository's secrets. Repository variables:
META_ACTOR (the actor, default bovi~meta-ads-library-scraper, whose records carry the EU transparency data
as regional_regulation_data), META_COUNTRIES (the library's country filter, default ALL), META_MAX_ADS
(ads per house per run, default 2000). Without the token the run records that it waited and stops.

Each house's Facebook page ids are found once from a search by its name: the pages whose name is the
house's (and not a beauty or fragrance licensee or a reseller) are kept in the state, with the near
misses refused, so the choice can be checked; registry page_ids, when set, are used instead. The ads
of those pages are then fetched for the past year. The vendor's record fields vary by actor, so the row
reads them by name patterns and the first live run keeps the record's shape (keys and types, no values)
in the state. Ad text is never stored; its features and the line it sells are kept as adtone.collect
keeps them. Pictures and video stills go through the reader and the fingerprint, in memory.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date

import requests

from . import adlibs, copyfeat, registry, thread
from .snap import _day, _norm, find, is_house, urls_in

API = "https://api.apify.com/v2"
UA = "fashion-adtone research (Apify client; contact via the repository)"
POLL_S = 20
DEFAULT_ACTOR = "bovi~meta-ads-library-scraper"


class Stop(RuntimeError):
    pass


def _list(v) -> list:
    return v if isinstance(v, list) else ([v] if v else [])


def _texts(item: dict) -> list[str]:
    out = []
    for k in ("ad_creative_bodies", "bodies", "body", "ad_creative_link_titles", "titles", "title",
              "ad_creative_link_descriptions", "descriptions", "link_description", "caption", "cta_text"):
        v = find(item, _norm(k), want=(str, list))
        out.extend(x for x in _list(v) if isinstance(x, str))
    return out


class Source:
    name = "metaweb"
    lookback_days = 400          # a little more than the library's year, as adtone.collect does
    advertiser_refresh_days = 90
    Stop = Stop

    def __init__(self, sess: requests.Session | None = None, token: str | None = None, actor: str | None = None,
                 countries: str | None = None, max_ads: int | None = None, sleep=time.sleep, budget_s: float = 55 * 60,
                 clock=time.monotonic):
        self.s = sess or requests.Session()
        self.s.headers.setdefault("User-Agent", UA)
        self.token = token if token is not None else os.environ.get("APIFY_TOKEN", "").strip()
        self.actor = actor or os.environ.get("META_ACTOR", DEFAULT_ACTOR).replace("/", "~")
        self.countries = [c.strip().upper() for c in (countries or os.environ.get("META_COUNTRIES", "ALL")).split(",") if c.strip()]
        self.max_ads = max_ads or int(os.environ.get("META_MAX_ADS", "2000"))
        self.sleep, self.clock, self.t0, self.budget_s = sleep, clock, clock(), budget_s
        self.requests, self.runs, self.cost_usd = 0, [], 0.0

    def state_extra(self) -> dict:
        return {"actor": self.actor, "countries": self.countries, "vendor_runs": self.runs[-40:]}

    def _call(self, method: str, path: str, **kw) -> dict:
        self.requests += 1
        r = self.s.request(method, f"{API}/{path}", params={"token": self.token, **kw.pop("params", {})}, timeout=120, **kw)
        if r.status_code in (401, 403):
            raise Stop(f"Apify refused the token (HTTP {r.status_code})")
        if r.status_code == 402:
            raise Stop("Apify: the account's credit is used up (HTTP 402)")
        if r.status_code >= 400:
            raise RuntimeError(f"Apify HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError:
            raise RuntimeError("Apify: not JSON")

    def run_actor(self, inp: dict) -> list[dict]:
        """Start the actor with the input, wait for it, and return its dataset's records."""
        started = self._call("POST", f"acts/{self.actor}/runs", json=inp, params={"memory": 1024})
        run = started.get("data") or {}
        rid = run.get("id")
        if not rid:
            raise RuntimeError(f"Apify: no run id in {str(started)[:200]}")
        status = run.get("status")
        while status in (None, "READY", "RUNNING"):
            if self.clock() - self.t0 > self.budget_s:
                self._call("POST", f"actor-runs/{rid}/abort")
                raise Stop("budget: the vendor's run was aborted")
            self.sleep(POLL_S)
            run = (self._call("GET", f"actor-runs/{rid}") or {}).get("data") or {}
            status = run.get("status")
        usage = run.get("usageTotalUsd")
        self.runs.append({"id": rid, "status": status, "usd": usage, "input": {k: v for k, v in inp.items() if k != "proxy"}})
        if isinstance(usage, (int, float)):
            self.cost_usd += float(usage)
        if status != "SUCCEEDED":
            raise RuntimeError(f"Apify run {rid} {status}")
        ds = run.get("defaultDatasetId")
        items, offset = [], 0
        while True:
            page = self._call("GET", f"datasets/{ds}/items", params={"clean": "true", "offset": offset, "limit": 1000})
            rows = page if isinstance(page, list) else (page.get("items") or [])
            items.extend(r for r in rows if isinstance(r, dict))
            if len(rows) < 1000:
                return items
            offset += 1000

    def _input(self, page_ids: list[str] | None = None, terms: list[str] | None = None, max_results: int | None = None) -> dict:
        inp = {"countries": self.countries, "adType": "all", "maxResults": max_results or self.max_ads}
        if page_ids:
            inp["pageIds"] = page_ids
        if terms:
            inp["searchTerms"] = terms
        return inp

    # ---- the source protocol ----
    def advertisers(self, house: registry.House):
        if house.page_ids:
            return [{"id": str(p), "name": house.name, "from": "registry"} for p in house.page_ids], []
        pages: dict[str, dict] = {}
        for item in self.run_actor(self._input(terms=[house.name], max_results=300)):
            pid = str(find(item, "pageid", "page_id", want=(str, int)) or "")
            pname = find(item, "pagename", "page_name", want=(str,)) or ""
            if not pid:
                continue
            rec = pages.setdefault(pid, {"id": pid, "name": pname, "ads": 0})
            rec["ads"] += 1
        keep = [p for p in pages.values() if is_house(house, p["name"])]
        refused = [p for p in pages.values() if not is_house(house, p["name"])]
        keep.sort(key=lambda p: -p["ads"])
        refused.sort(key=lambda p: -p["ads"])
        return keep, refused

    def search_ads(self, house: registry.House, keep: list[dict], start: date, end: date):
        ids = [k["id"] for k in keep]
        if not ids:
            return
        for item in self.run_actor(self._input(page_ids=ids)):
            yield item

    def is_houses(self, item: dict, house: registry.House, keep: list[dict]) -> bool:
        pid = str(find(item, "pageid", "page_id", want=(str, int)) or "")
        return pid in {k["id"] for k in keep} or (not pid and is_house(house, find(item, "pagename", "page_name", want=(str,)) or ""))

    def ad_row(self, item: dict, house_id: str, run: str) -> dict:
        reg_data = find(item, "regionalregulationdata", "aaainfo", "eutransparency", want=(dict,)) or {}
        eu_reach = find(reg_data, "eutotalreach", want=(int, str)) or find(item, "eutotalreach", want=(int, str))
        breakdown = find(reg_data, "agecountrygenderreachbreakdown", want=(list,)) or find(item, "agecountrygenderreachbreakdown", want=(list,))
        payers = find(reg_data, "payerbeneficiarydata", "beneficiarypayers", want=(list,)) or find(item, "payerbeneficiarydata", "beneficiarypayers", want=(list,))
        locs = find(reg_data, "locationaudience", "targetlocations", want=(list,)) or find(item, "targetlocations", "locationaudience", want=(list,))
        ages = find(reg_data, "ageaudience", "targetages", want=(dict, list)) or find(item, "targetages", "ageaudience", want=(dict, list))
        gender = find(reg_data, "genderaudience", "targetgender", want=(str,)) or find(item, "targetgender", "genderaudience", want=(str,))
        texts = _texts(item)
        titles = _list(find(item, "adcreativelinktitles", "titles", "title", want=(str, list)))
        row = {
            "ad_id": str(find(item, "adarchiveid", "ad_archive_id", "id", "adid", want=(str, int)) or ""),
            "house_id": house_id,
            "page_id": str(find(item, "pageid", "page_id", want=(str, int)) or ""),
            "page_name": find(item, "pagename", "page_name", want=(str,)),
            "start": _day(find(item, "addeliverystartdate", "addeliverystarttime", "startdate", "startdatestring", want=(str, int))),
            "stop": _day(find(item, "addeliverystopdate", "addeliverystoptime", "enddate", "enddatestring", want=(str, int))),
            "is_active": find(item, "isactive", "active", want=(bool,)),
            "platforms": _list(find(item, "publisherplatforms", "publisherplatform", want=(list, str))) or None,
            "display_format": find(item, "displayformat", want=(str,)),
            "collation_count": find(item, "collationcount", want=(int,)),
            "eu_total_reach": int(eu_reach) if isinstance(eu_reach, (int, str)) and str(eu_reach).isdigit() else eu_reach,
            "target_ages": ages,
            "target_gender": gender,
            "target_locations": [{"name": loc.get("name"), "type": loc.get("type"), "excluded": bool(loc.get("excluded"))}
                                 for loc in locs if isinstance(loc, dict)] or None,
            "reach": _reach(breakdown) if breakdown else None,
            "beneficiary_payers": [{"payer": p.get("payer"), "beneficiary": p.get("beneficiary")} for p in payers if isinstance(p, dict)] or None,
            "copy": copyfeat.features([t for t in texts if t]) if texts else None,
            "title": copyfeat.features([t for t in titles if isinstance(t, str)]) if titles else None,
            "line": thread.ad_line(texts) if texts else None,
            "vendor": self.actor,
            "last_collected": run,
        }
        row["first_shown"], row["last_shown"] = row["start"], row["stop"]
        return {k: v for k, v in row.items() if v is not None}

    def media_for(self, item: dict) -> list[dict]:
        urls = []
        for key, u in urls_in(item):
            k = _norm(key)
            if "snapshot" in k and "profile" in k:
                continue
            if any(w in k for w in ("originalimageurl", "resizedimageurl", "videohdurl", "videosdurl", "videopreviewimageurl", "imageurl", "videourl")):
                urls.append((0 if "original" in k or "hd" in k else 1, u))
        if any(rank == 0 for rank, _ in urls):   # the full-size pictures are there: the resized copies are not fetched
            urls = [(rank, u) for rank, u in urls if rank == 0 or "preview" in u]
        urls.sort(key=lambda t: t[0])
        return adlibs.fetch_media(self.s, list(dict.fromkeys(u for _, u in urls)))


def _reach(breakdown: list) -> dict:
    from collections import Counter
    by_age, by_gender, by_country = Counter(), Counter(), Counter()
    for item in breakdown:
        if not isinstance(item, dict):
            continue
        country = item.get("country") or "unknown"
        for ag in item.get("age_gender_breakdowns") or item.get("ageGenderBreakdowns") or []:
            if not isinstance(ag, dict):
                continue
            age = ag.get("age_range") or ag.get("ageRange") or "unknown"
            for g in ("male", "female", "unknown"):
                v = ag.get(g) or 0
                if v:
                    by_age[age] += int(v)
                    by_gender[g] += int(v)
                    by_country[country] += int(v)
    return {"by_age": dict(sorted(by_age.items())), "by_gender": dict(sorted(by_gender.items())),
            "by_country": dict(sorted(by_country.items()))}


def main(argv: list[str] | None = None) -> int:
    return adlibs.main_for("metaweb", Source, argv, needs=("APIFY_TOKEN",))


if __name__ == "__main__":
    sys.exit(main())
