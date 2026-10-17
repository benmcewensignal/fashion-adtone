"""Pinterest: the houses' ads in Pinterest's Ads Repository, its ad library for the EU under the Digital
Services Act, read as its own public page reads it (the page is open to anyone, with no account; the
resource it calls was recorded by adtone.explore on 17 October 2026 and is used here as the page uses it,
from the runner as it is, with nothing done to pass as a person).

    python -m adtone.pinterest collect --run <id> [--budget-min 60] [--houses gucci,dior]
    python -m adtone.pinterest probe --run <id>

What the repository gives: for a country, a window of at most a month and an advertiser's name, the ads
shown there with the advertiser, first and last day, the countries, genders, ages and interests targeted,
whether keywords or audience lists were used, how many people in the EU and in each country saw it in
bands, the picture or video, and any moderation. EU, EEA, Brazil and Türkiye only; no UK. Advertiser
names come from the page's own name search, matched to the house as elsewhere (no beauty or fragrance
licensees, no resellers). The year is read a month at a time for the larger markets; an ad shown in
several is kept once. Pictures and video stills go through the reader and the fingerprint, in memory;
the ad's title and text are kept only as features, never as text.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta

import requests

from . import adlibs, copyfeat, registry, thread
from .snap import is_house

RESOURCE = "https://ads.pinterest.com/resource/ApiResource/get/"
SOURCE_URL = "/ads-repository/"
UA = "fashion-adtone research (Pinterest Ads Repository; contact via the repository)"
COUNTRIES = ("FR", "DE", "IT", "ES", "NL", "SE")      # the larger markets; an ad shown in several is kept once
WINDOW_DAYS = 30
PAGE_SIZE = 50
PACE_S = 1.5
MAX_PAGES = 40


class Stop(RuntimeError):
    pass


class Source:
    name = "pinterest"
    lookback_days = 364
    advertiser_refresh_days = 30
    Stop = Stop

    def __init__(self, sess: requests.Session | None = None, sleep=time.sleep, pace_s: float = PACE_S, countries=COUNTRIES):
        self.s = sess or requests.Session()
        self.s.headers.setdefault("User-Agent", UA)
        self.s.headers.setdefault("Accept", "application/json, text/javascript, */*; q=0.01")
        self.s.headers.setdefault("X-Requested-With", "XMLHttpRequest")
        self.s.headers.setdefault("Referer", "https://ads.pinterest.com/ads-repository/")
        self.sleep, self.pace_s, self.last = sleep, pace_s, 0.0
        self.requests, self.refused, self.countries = 0, 0, list(countries)
        self.country_form: str | None = None
        self.notes: list[str] = []

    def state_extra(self) -> dict:
        return {"country_form": self.country_form, "notes": self.notes[-20:]}

    def resource(self, url: str, data: dict, bookmark: str | None = None) -> dict:
        opts = {"url": url, "data": data}
        if bookmark:
            opts["bookmarks"] = [bookmark]
            opts["data"] = {**data, "bookmark": bookmark}
        attempt = 0
        while True:
            attempt += 1
            wait = self.pace_s - (time.monotonic() - self.last)
            if wait > 0:
                self.sleep(wait)
            self.last = time.monotonic()
            self.requests += 1
            r = self.s.get(RESOURCE, params={"source_url": SOURCE_URL, "data": json.dumps({"options": opts, "context": {}}),
                                             "_": str(int(time.time() * 1000))}, timeout=60)
            if r.status_code in (401, 403):
                raise Stop(f"Pinterest refused the runner (HTTP {r.status_code})")
            if r.status_code == 429 or r.status_code >= 500:
                self.refused += 1
                if self.refused >= 8:
                    raise Stop(f"Pinterest refused {self.refused} requests in a row (HTTP {r.status_code})")
                self.sleep(min(90.0, 2.5 * 2 ** attempt))
                continue
            self.refused = 0
            try:
                out = r.json()
            except ValueError:
                raise RuntimeError(f"Pinterest HTTP {r.status_code}: not JSON")
            rr = out.get("resource_response") or {}
            if r.status_code != 200 or rr.get("status") not in (None, "success"):
                raise RuntimeError(f"Pinterest HTTP {r.status_code}: {str(rr.get('message') or out)[:200]}")
            return rr

    def advertisers(self, house: registry.House):
        keep, refused, seen = [], [], set()
        for term in [house.name] + list(house.search_terms or []):
            rr = self.resource("/ads/v4/ads_repository/advertiser_names", {"search_text": term})
            for name in (rr.get("data") or {}).get("advertiser_names") or []:
                if not isinstance(name, str) or name in seen:
                    continue
                seen.add(name)
                (keep if is_house(house, name) else refused).append({"id": name, "name": name})
        return keep, refused

    def _ads(self, name: str, country: str, start: date, end: date):
        bookmark = None
        for _ in range(MAX_PAGES):
            data = {"country": country, "start_date": start.isoformat(), "end_date": end.isoformat(), "page_size": PAGE_SIZE,
                    "advertiser_name": name}
            rr = self.resource("/ads/v4/ads_repository/ad_library", data, bookmark)
            items = (rr.get("data") or {}).get("pin_ids_with_metadata") or []
            for it in items:
                if isinstance(it, dict):
                    yield it
            bookmark = rr.get("bookmark")
            if not items or not bookmark or bookmark == "-end-":
                return

    def search_ads(self, house: registry.House, keep: list[dict], start: date, end: date):
        seen: set[str] = set()
        for adv in keep:
            for country in self.countries:
                win_end = end
                while win_end > start:
                    win_start = max(start, win_end - timedelta(days=WINDOW_DAYS))
                    try:
                        items = list(self._ads(adv["name"], country, win_start, win_end))
                    except RuntimeError as e:
                        # the country may be wanted as its name rather than its code: tried once, remembered
                        if self.country_form is None and "country" in str(e).lower():
                            self.country_form = "name"
                            self.notes.append(f"country as name after: {str(e)[:100]}")
                            self.countries = [_country_name(c) for c in self.countries]
                            continue
                        raise
                    if self.country_form is None:
                        self.country_form = "code"
                    for it in items:
                        pid = str(it.get("pin_id") or "")
                        if pid and pid not in seen:
                            seen.add(pid)
                            yield {"country_searched": country, "name_searched": adv["name"], **it}
                    win_end = win_start - timedelta(days=1)

    def is_houses(self, item: dict, house: registry.House, keep: list[dict]) -> bool:
        names = (item.get("ad_details") or {}).get("advertiser_names")
        if isinstance(names, list) and names:
            return any(is_house(house, n) for n in names if isinstance(n, str))
        return True   # the search was by the house's own advertiser name

    def ad_row(self, item: dict, house_id: str, run: str) -> dict:
        d = item.get("ad_details") or {}
        pin = d.get("pin_data") or {}
        texts = [t for t in (pin.get("title"), pin.get("details")) if isinstance(t, str) and t.strip()]
        names = d.get("advertiser_names")
        return {"ad_id": str(item.get("pin_id") or ""), "house_id": house_id,
                "advertiser": (names[0] if isinstance(names, list) and names else None) or item.get("name_searched"),
                "first_shown": (d.get("start_date") or "")[:10] or None, "last_shown": (d.get("end_date") or "")[:10] or None,
                "countries": d.get("countries"), "genders": d.get("genders"), "age_buckets": d.get("age_buckets"),
                "n_interests": len(d.get("interests") or []), "keywords_used": d.get("keywords_used"),
                "audience_list_used": bool(d.get("pinner_list_types")), "devices": d.get("devices") or None,
                "user_count_eu": d.get("user_count_eu"), "user_count_by_country": d.get("user_count_by_country"),
                "review_status": d.get("review_status"), "commercial_content": d.get("content_commercial"),
                "has_video": bool(pin.get("video_link")), "copy": copyfeat.features(texts) if texts else None,
                "line": thread.ad_line(texts) if texts else None, "last_run": run}

    def media_for(self, item: dict) -> list[dict]:
        d = item.get("ad_details") or {}
        pin = d.get("pin_data") or {}
        urls = [u for u in (pin.get("image_link"), d.get("image_link"), pin.get("video_link")) if isinstance(u, str) and u.startswith("http")]
        dyn = d.get("dynamic_images")
        if isinstance(dyn, list):
            urls += [u for u in dyn if isinstance(u, str) and u.startswith("http")][:4]
        return adlibs.fetch_media(self.s, urls, max_images=5, max_videos=1)


def _country_name(code: str) -> str:
    return {"FR": "France", "DE": "Germany", "IT": "Italy", "ES": "Spain", "NL": "Netherlands", "SE": "Sweden", "IE": "Ireland",
            "BE": "Belgium", "AT": "Austria", "PL": "Poland", "DK": "Denmark", "PT": "Portugal"}.get(code, code)


def main(argv: list[str] | None = None) -> int:
    return adlibs.main_for("pinterest", Source, argv)


if __name__ == "__main__":
    sys.exit(main())
