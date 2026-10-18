"""Meta's Ad Library through a data vendor: the houses' ads on Facebook and Instagram as the public library
pages show them, read for us by an Apify actor that sells that reading through its API (the library's
own API is served only to people who have passed Meta's identity confirmation, and its pages refuse
automated readers; the vendor's reading is the vendor's). The rows take the shape adtone.collect gives
the API's ads, so everything downstream reads them alike.

    python -m adtone.metaweb collect --run <id> [--budget-min 60] [--houses gucci,dior]
    python -m adtone.metaweb probe --run <id>

Needs APIFY_TOKEN, an Apify account's API token, in the repository's secrets. Repository variables:
META_ACTOR (the actor, default curious_coder~facebook-ads-library-scraper, whose "scrape ad details" option
names the EU reach; apify~facebook-ads-scraper and bovi~meta-ads-library-scraper are also understood, though
on 17 October neither carried the EU block for Gucci's page),
META_COUNTRIES (the library's country filter, default ALL), META_MAX_ADS
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
from .snap import _day, _norm, division, find, is_house, is_own, urls_in

API = "https://api.apify.com/v2"
UA = "fashion-adtone research (Apify client; contact via the repository)"
POLL_S = 20
DEFAULT_ACTOR = "curious_coder~facebook-ads-library-scraper"   # its "scrape ad details" option names the EU reach
LIBRARY = "https://www.facebook.com/ads/library/"


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


NOT_A_HOUSE_PAGE = ("blog", "personal", "public figure", "community", "just for fun", "writer", "artist",
                    "musician", "band", "actor", "athlete", "journalist", "fan page", "video creator", "digital creator",
                    "cargo", "freight", "logistics", "medical", "clinic", "hospital", "hotel", "resort", "real estate",
                    "restaurant", "amusement", "water park", "surf", "travel agency")


# pages that bear a house's exact name and belong to another company, as the payers of their ads show
OTHER_COMPANIES_PAGES = {
    "106889830893927": "Loewe: Loewe Technology GmbH, the television maker (its ads paid by Loewe Technology GmbH and "
                       "Loewe International, reaching Denmark, Germany and Austria)",
}


def is_page(house: registry.House, page: dict) -> bool:
    """The page is the house's own: its name is the house's (Gucci, GUCCI, CHANEL 샤넬), or the house's name
    with a company suffix, or with a place and a few ads at least (Loewe Greece), never a person, a blog, a
    shop, a business that shares the name or a licensee whose name merely contains it (celine_maureen, Céline
    Rousseau - Blog Santé, LOEWE Perfumes, Thiết bị phòng tắm cao cấp Fendi, Alaïa Bay, Hermes Logistik)."""
    cats = " ".join(page.get("categories") or []).lower()
    if any(w in cats for w in NOT_A_HOUSE_PAGE) or str(page.get("id") or "") in OTHER_COMPANIES_PAGES:
        return False
    return is_own(house, page.get("name") or "", ads=int(page.get("ads") or 0))


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
        self.form = os.environ.get("META_INPUT_FORM", "library").strip() or "library"
        self.period = os.environ.get("META_PERIOD", "").strip()      # "" for the whole library, last30d for the weekly top-up
        self.first_search = False      # set by the loop for a house never searched to the end: its whole year, whatever the period
        self.sleep, self.clock, self.t0, self.budget_s = sleep, clock, clock(), budget_s
        self.requests, self.runs, self.cost_usd, self.errors = 0, [], 0.0, []

    def state_extra(self) -> dict:
        return {"actor": self.actor, "countries": self.countries, "form": self.form, "period": self.period, "vendor_runs": self.runs[-40:],
                "vendor_errors": self.errors[-20:]}

    def _call(self, method: str, path: str, **kw) -> dict:
        self.requests += 1
        r = self.s.request(method, f"{API}/{path}", params={"token": self.token, **kw.pop("params", {})}, timeout=120, **kw)
        if r.status_code in (401, 403):
            why = ""
            try:
                why = (r.json().get("error") or {}).get("message") or ""
            except ValueError:
                pass
            raise Stop(f"Apify refused the request (HTTP {r.status_code}): {why[:160]}" if why else f"Apify refused the token (HTTP {r.status_code})")
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
        n_urls = len(inp.get("urls") or inp.get("startUrls") or inp.get("pageIds") or inp.get("searchTerms") or [1])
        memory = 512                                   # curious_coder's actor wants at most one address per 512 MB,
        while memory * 2 <= 512 * n_urls and memory * 2 <= 4096:   # and Apify a power of two: the largest allowed
            memory *= 2
        started = self._call("POST", f"acts/{self.actor}/runs", json=inp, params={"memory": memory})
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
        """The actor's input: Apify's own actor takes library addresses (a page's ads, or a search by name) and a
        results limit; bovi's takes page ids or search terms and a country list."""
        n = max_results or self.max_ads
        if self.actor.startswith("curious_coder~"):
            # library addresses, with the ad details (the EU block) asked for by name
            country = self.countries[0] if self.countries else "ALL"
            urls = [f"{LIBRARY}?active_status=all&ad_type=all&country={country}&search_type=page&view_all_page_id={p}" for p in page_ids or []]
            urls += [f"{LIBRARY}?active_status=all&ad_type=all&country={country}&q={t}&search_type=keyword_unordered" for t in terms or []]
            return {"urls": [{"url": u} for u in urls], "scrapeAdDetails": True, "limitPerSource": n,
                    "scrapePageAds": {"period": "" if self.first_search or terms else self.period, "activeStatus": "all",
                                      "sortBy": "most_recent", "countryCode": country}}
        if self.actor.startswith("apify~"):
            country = self.countries[0] if self.countries else "ALL"
            if self.form == "page":   # the page's own address, which the actor's issue tracker shows answered with the EU block
                urls = [f"https://www.facebook.com/{p}" for p in page_ids or []]
            else:
                urls = [f"{LIBRARY}?active_status=all&ad_type=all&country={country}&search_type=page&view_all_page_id={p}" for p in page_ids or []]
            urls += [f"{LIBRARY}?active_status=all&ad_type=all&country={country}&q={t}&search_type=keyword_unordered" for t in terms or []]
            return {"startUrls": [{"url": u} for u in urls], "resultsLimit": n, "activeStatus": "", "scrapeAdDetails": True}
        inp = {"countries": self.countries, "adType": "all", "maxResults": n}
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
            rec = pages.setdefault(pid, {"id": pid, "name": pname, "ads": 0, "categories": []})
            rec["ads"] += 1
            cats = find(item, "pagecategories", "categories", want=(list,)) or []
            cat = find(item, "pagecategory", want=(str,))
            for c in [*cats, cat]:
                if isinstance(c, str) and c and c not in rec["categories"]:
                    rec["categories"].append(c)
        keep = [p for p in pages.values() if is_page(house, p)]
        refused = [p for p in pages.values() if not is_page(house, p)]
        keep.sort(key=lambda p: -p["ads"])
        refused.sort(key=lambda p: -p["ads"])
        return keep, refused

    def filter_keep(self, house: registry.House, keep: list[dict]) -> list[dict]:
        """The pages on file, judged by the rule as it stands (a page from the registry always stands)."""
        return [p for p in keep if p.get("from") == "registry" or is_page(house, p)]

    @staticmethod
    def keep_from_rows(rows: list[dict]) -> list[dict]:
        """The pages the rows on file came from, as the page lookup records them: for a house whose lookup
        record is missing, so that its rows are judged by their pages' names rather than all dropped."""
        pages: dict[str, dict] = {}
        for r in rows:
            if r.get("page_id"):
                rec = pages.setdefault(str(r["page_id"]), {"id": str(r["page_id"]), "name": r.get("page_name") or "", "ads": 0})
                rec["ads"] += 1
        return list(pages.values())

    @staticmethod
    def row_division(row: dict) -> str:
        return division(row.get("page_name") or "", *[str(p.get(k) or "") for p in (row.get("beneficiary_payers") or [])
                                                      for k in ("payer", "beneficiary")])

    def row_is_kept(self, row: dict, keep: list[dict], house: registry.House | None = None) -> bool:
        """A stored row stays only while its page is among the house's own: a page matched too loosely
        before, and dropped on a rematch, takes its rows with it."""
        return not row.get("page_id") or row["page_id"] in {k["id"] for k in keep}

    def search_ads(self, house: registry.House, keep: list[dict], start: date, end: date):
        ids = [k["id"] for k in keep]
        if not ids:
            return
        for item in self.run_actor(self._input(page_ids=ids)):
            if "error" in item and len(item) <= 3:      # the actor's own report of a failure, not an ad: the house is not searched
                self.errors.append({"house": house.id, **{k: str(v)[:300] for k, v in item.items()}})
                raise RuntimeError(f"Apify actor: {str(item.get('error'))[:200]}")
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
                                 for loc in (locs or []) if isinstance(loc, dict)] or None,
            "reach": _reach(breakdown) if breakdown else None,
            "beneficiary_payers": [{"payer": p.get("payer"), "beneficiary": p.get("beneficiary")} for p in (payers or []) if isinstance(p, dict)] or None,
            "division": division(find(item, "pagename", "page_name", want=(str,)) or "",
                                 *[str(p.get(k) or "") for p in (payers or []) if isinstance(p, dict) for k in ("payer", "beneficiary")]),
            "countries": _list(find(item, "targetedorreachedcountries", "countries", want=(list,))) or None,
            "has_metrics": find(item, "hasmetrics", want=(bool,)),
            "copy": copyfeat.features([t for t in texts if t]) if texts else None,
            "title": copyfeat.features([t for t in titles if isinstance(t, str)]) if titles else None,
            "line": thread.ad_line(texts) if texts else None,
            "vendor": self.actor,
            "last_collected": run,
        }
        row["first_shown"], row["last_shown"] = row["start"], row["stop"]
        return {k: v for k, v in row.items() if v is not None}

    def media_urls(self, item: dict) -> tuple[list[str], dict]:
        urls = []
        for key, u in urls_in(item):
            k = _norm(key)
            if any(w in k for w in ("profile", "logo", "avatar", "watermark")):
                continue
            if any(w in k for w in ("originalimageurl", "resizedimageurl", "videohdurl", "videosdurl", "videopreviewimageurl", "imageurl", "videourl")):
                # the full-size picture, and the standard-definition video: three stills need no HD file of tens of megabytes
                urls.append((0 if "original" in k or "videosd" in k else 1, u))
        # lists of addresses under a key (bovi's snapshot_images and snapshot_videos)
        for key in ("snapshot_images", "snapshot_videos", "images", "videos", "media_urls", "mediaUrls"):
            v = item.get(key)
            if isinstance(v, list):
                urls += [(0, u) for u in v if isinstance(u, str) and u.startswith("http")]
        if any(rank == 0 for rank, _ in urls):   # the full-size pictures are there: the resized copies are not fetched
            urls = [(rank, u) for rank, u in urls if rank == 0 or "preview" in u]
        urls.sort(key=lambda t: t[0])
        return list(dict.fromkeys(u for _, u in urls)), {"max_videos": 1, "max_video_bytes": 25_000_000}

    def media_for(self, item: dict) -> list[dict]:
        urls, kw = self.media_urls(item)
        return adlibs.fetch_media(self.s, urls, **kw)

    def vendor_datasets(self, since: str) -> list[dict]:
        """The account's successful actor runs since a day, newest first, with their dataset ids: the vendor
        keeps a run's records for days after it, so the picture addresses of ads already on file can be had
        again without buying the ads again."""
        out, offset = [], 0
        while True:
            page = self._call("GET", "actor-runs", params={"limit": 100, "offset": offset, "desc": "true"})
            items = (page.get("data") or {}).get("items") or []
            for r in items:
                if (r.get("startedAt") or "") < since:
                    return out
                if r.get("status") == "SUCCEEDED" and r.get("defaultDatasetId"):
                    out.append({"id": r.get("id"), "dataset": r["defaultDatasetId"], "started": r.get("startedAt")})
            if len(items) < 100:
                return out
            offset += 100

    def dataset_items(self, ds: str) -> list[dict]:
        items, offset = [], 0
        while True:
            page = self._call("GET", f"datasets/{ds}/items", params={"clean": "true", "offset": offset, "limit": 1000})
            rows = page if isinstance(page, list) else (page.get("items") or [])
            items.extend(r for r in rows if isinstance(r, dict))
            if len(rows) < 1000:
                return items
            offset += 1000

    def rehydrate(self, reg: registry.Registry, run: str, since: str, readers: tuple[str, ...] = ("clothes-v1",)) -> dict:
        """The picture addresses of the made fashion ads already on file whose pictures lack a reading by one of
        `readers`, taken again from the vendor's recent datasets and kept aside for the read stage, which reads
        them with those readers only and keeps the pictures as first recorded. Meta's addresses lapse within
        days of the search, so this is run while they stand."""
        from . import store as _store
        from .score import load_rubric
        P = adlibs.paths(self.name)
        houses = {h.id: h for h in reg.houses}
        want: dict[str, dict] = {}          # ad_id -> row
        for h in reg.houses:
            path = P["ads"] / f"{h.id}.jsonl"
            for r in _store.read_jsonl(path):
                if (r.get("display_format") or "") in adlibs.DYNAMIC or r.get("division", "fashion") != "fashion" or not r.get("media"):
                    continue
                want[r["ad_id"]] = r
        read = {}
        for v in readers:
            read[v] = {o["sha"] for o in _store.read_jsonl(P["obs"] / f"{load_rubric(v).version}.jsonl") if o.get("status") in ("ok", "invalid")}
        lacking = {a: r for a, r in want.items()
                   if any(any(m["sha"] not in read[v] for m in r["media"]) for v in readers)}
        counts = {"runs": 0, "items": 0, "ads_on_file": len(want), "lacking": len(lacking), "rehydrated": 0, "no_address": 0}
        found: dict[str, tuple] = {}
        for vr in self.vendor_datasets(since):
            counts["runs"] += 1
            for item in self.dataset_items(vr["dataset"]):
                counts["items"] += 1
                ad_id = str(find(item, "adarchiveid", "ad_archive_id", "id", "adid", want=(str, int)) or "")
                if ad_id in lacking and ad_id not in found:
                    urls, kw = self.media_urls(item)
                    if urls:
                        found[ad_id] = (urls, kw)
        by_house: dict[str, dict] = {}
        for ad_id, (urls, kw) in found.items():
            r = lacking[ad_id]
            by_house.setdefault(r["house_id"], {})[ad_id] = {"ad_id": ad_id, "house_id": r["house_id"], "urls": list(urls), "kw": dict(kw),
                                                           "first_shown": r.get("first_shown"), "found": r.get("found"),
                                                           "readers": list(readers), "keep_media": True}
        counts["no_address"] = len(lacking) - len(found)
        for h, entries in by_house.items():
            pend_path = P["pending"] / f"{h}.jsonl"
            pend = {x["ad_id"]: x for x in _store.read_jsonl(pend_path)}
            for ad_id, x in entries.items():
                if ad_id not in pend:
                    pend[ad_id] = x
                    counts["rehydrated"] += 1
            _store.write_jsonl(pend_path, sorted(pend.values(), key=lambda x: x["ad_id"]))
        out = {"run": run, "stage": "rehydrate", "updated_at": _store.utc_now(), "since": since, "readers": list(readers), **counts}
        _store.append_jsonl(P["prov"], [out])
        return out


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
