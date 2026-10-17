"""Microsoft: the houses' search ads on Bing in Microsoft's Ad Library, its repository for the European
Economic Area under the Digital Services Act, read through Microsoft's open API (learn.microsoft.com,
"Ad Library API": "publicly available and does not require sign-up or log-in", with stricter limits
unauthenticated; read 17 October 2026).

    python -m adtone.msads collect --run <id> [--budget-min 30] [--houses gucci,dior]
    python -m adtone.msads probe --run <id>

What the API gives: advertisers by name (id, name, country, whether verified), then each advertiser's ads
with title, description, display and destination addresses, any image assets, and on request the ad's
details: who paid, first and last day shown, the total impressions as a range and their share by country,
and the kinds of targeting used. Impressions were first recorded in June 2023. No UK coverage. Most of
these are text ads with no picture; where an ad carries an image asset it goes through the reader like
any other ad picture, in memory.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import date

import requests

from . import adlibs, registry
from .media import MediaError, to_fetched
from .snap import _norm, is_house

BASE = "https://adlibrary.api.bingads.microsoft.com/api/v1"
# the API sits behind Azure's web application firewall, which answered the first run's plain client with a
# JavaScript challenge (HTTP 403); the browser's own headers are tried next, and if that is refused too the
# documented authenticated route (a Microsoft Advertising developer token) is the way in
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
HEADERS = {"Accept": "application/json, text/plain, */*", "Accept-Language": "en-GB,en;q=0.9",
           "Referer": "https://adlibrary.ads.microsoft.com/", "Origin": "https://adlibrary.ads.microsoft.com"}
PACE_S = 1.0
PAGE = 50
MAX_ADS_PER_ADVERTISER = 4000
TARGET_TYPES = {1: "gender", 2: "age", 3: "location", 22: "microsoft_audiences", 25: "advertiser_audiences"}


class Stop(RuntimeError):
    pass


class Source:
    name = "msads"
    lookback_days = 364
    advertiser_refresh_days = 30
    Stop = Stop

    def __init__(self, sess: requests.Session | None = None, sleep=time.sleep, pace_s: float = PACE_S):
        self.s = sess or requests.Session()
        self.s.headers.setdefault("User-Agent", UA)
        for k, v in HEADERS.items():
            self.s.headers.setdefault(k, v)
        self.sleep, self.pace_s, self.last = sleep, pace_s, 0.0
        self.requests, self.refused = 0, 0

    def get(self, path: str, params: dict | None = None):
        attempt = 0
        while True:
            attempt += 1
            wait = self.pace_s - (time.monotonic() - self.last)
            if wait > 0:
                self.sleep(wait)
            self.last = time.monotonic()
            self.requests += 1
            r = self.s.get(f"{BASE}/{path}", params=params, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                self.refused += 1
                if self.refused >= 8:
                    raise Stop(f"Microsoft refused {self.refused} requests in a row (HTTP {r.status_code})")
                self.sleep(min(90.0, 2.5 * 2 ** attempt))
                continue
            self.refused = 0
            if r.status_code == 404:
                return None
            if r.status_code != 200:
                raise RuntimeError(f"Microsoft HTTP {r.status_code}: {r.text[:200]}")
            try:
                return r.json()
            except ValueError:
                raise RuntimeError("Microsoft: not JSON")

    def advertisers(self, house: registry.House):
        keep, refused, seen = [], [], set()
        for term in [house.name] + list(house.search_terms or []):
            got = self.get("Advertisers", {"searchText": term, "top": 25, "skip": 0}) or []
            rows = got.get("value") if isinstance(got, dict) else got
            for a in rows or []:
                if not isinstance(a, dict) or a.get("AdvertiserId") in seen:
                    continue
                seen.add(a.get("AdvertiserId"))
                row = {"id": str(a.get("AdvertiserId")), "name": a.get("AdvertiserName"), "country": a.get("AdvertiserCountry"),
                       "verified": bool(a.get("IsVerified"))}
                (keep if is_house(house, a.get("AdvertiserName") or "") else refused).append(row)
        return keep, refused

    def search_ads(self, house: registry.House, keep: list[dict], start: date, end: date):
        for adv in keep:
            skip = 0
            while skip < MAX_ADS_PER_ADVERTISER:
                got = self.get("Ads", {"advertiserId": adv["id"], "startDate": start.isoformat(), "endDate": end.isoformat(),
                                       "top": PAGE, "skip": skip}) or []
                rows = got.get("value") if isinstance(got, dict) else got
                rows = [r for r in rows or [] if isinstance(r, dict)]
                for ad in rows:
                    yield {"advertiser_id": adv["id"], **ad}
                if len(rows) < PAGE:
                    break
                skip += PAGE

    def is_houses(self, item: dict, house: registry.House, keep: list[dict]) -> bool:
        return str(item.get("advertiser_id")) in {k["id"] for k in keep} or is_house(house, item.get("AdvertiserName") or "")

    def ad_row(self, item: dict, house_id: str, run: str) -> dict:
        details = item.get("AdDetails")
        if not isinstance(details, dict) and item.get("AdId") is not None:
            full = self.get(f"Ads/{item['AdId']}", {"expand": "AdDetails(expand=ImpressionsByCountry,Targets)"})
            details = (full or {}).get("AdDetails") if isinstance(full, dict) else None
        details = details if isinstance(details, dict) else {}
        shares = {str(c.get("Country")): c.get("ImpressionShare") for c in details.get("ImpressionsByCountry") or [] if isinstance(c, dict)}
        targets = sorted({TARGET_TYPES.get(t.get("TargetType"), str(t.get("TargetType"))) + ("!" if t.get("UsedForExclusion") else "")
                          for t in details.get("Targets") or [] if isinstance(t, dict)})
        dest = item.get("DestinationUrl") or ""
        return {"ad_id": str(item.get("AdId")), "house_id": house_id, "advertiser_id": str(item.get("AdvertiserId") or item.get("advertiser_id")),
                "advertiser": item.get("AdvertiserName"), "paid_by": details.get("PaidForByName"),
                "first_shown": (details.get("StartDate") or "")[:10] or None, "last_shown": (details.get("EndDate") or "")[:10] or None,
                "impressions_range": details.get("TotalImpressionsRange"), "impression_share_by_country": shares or None,
                "targets": targets or None, "restriction": details.get("RestrictionReason"),
                "domain": re.sub(r"^https?://(www\.)?", "", dest).split("/")[0] if dest else None,
                "has_image": bool(_image_urls(item)), "last_run": run}

    def media_for(self, item: dict) -> list[dict]:
        got = []
        for u in _image_urls(item)[:3]:
            r = self.s.get(u, timeout=60)
            if r.status_code == 200 and r.content:
                try:
                    got.append({"kind": "image", "fetched": to_fetched(r.content)})
                except MediaError:
                    continue
        return got


def _image_urls(item: dict) -> list[str]:
    raw = item.get("AssetJson")
    if not raw:
        return []
    try:
        assets = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    out = []

    def walk(o, depth=0):
        if depth > 6:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, str) and v.startswith("http") and re.search(r"image|img|picture|thumbnail|url", _norm(k)) \
                        and re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", v, re.I):
                    out.append(v)
                else:
                    walk(v, depth + 1)
        elif isinstance(o, list):
            for v in o:
                walk(v, depth + 1)
    walk(assets)
    return list(dict.fromkeys(out))


def main(argv: list[str] | None = None) -> int:
    return adlibs.main_for("msads", Source, argv)


if __name__ == "__main__":
    sys.exit(main())
