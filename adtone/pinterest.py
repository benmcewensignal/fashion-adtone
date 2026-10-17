"""Pinterest: the houses' ads in Pinterest's Ads Repository, its ad library for the EU under the Digital
Services Act, read through its own public page (open to anyone, with no account) in a headless browser,
from the runner as it is, with nothing done to pass as a person: the page's country, dates and advertiser
controls are set, its Search pressed, and the answers its own resource returns are read as they arrive.
(adtone.explore recorded the resource on 17 October 2026; asked directly, without the page, it answers 403,
so the page is used as a person uses it.)

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

import sys
import time
from datetime import date, timedelta

import requests

from . import adlibs, copyfeat, registry, thread
from .snap import is_house

PAGE_URL = "https://ads.pinterest.com/ads-repository/"
COUNTRIES = ("FR", "DE", "IT", "ES", "NL", "SE")      # the larger markets; an ad shown in several is kept once
COUNTRY_NAMES = {"FR": "France", "DE": "Germany", "IT": "Italy", "ES": "Spain", "NL": "Netherlands", "SE": "Sweden", "IE": "Ireland",
                 "BE": "Belgium", "AT": "Austria", "PL": "Poland", "DK": "Denmark", "PT": "Portugal"}
WINDOW_DAYS = 30
MAX_PAGES = 40
SETTLE_MS = 2500


class Page:
    """The repository page in a headless browser, with every answer of its ad-library resource kept as it
    arrives. One per run."""

    def __init__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._ctx = self._browser.new_context(locale="en-GB", viewport={"width": 1366, "height": 900})
        self.page = self._ctx.new_page()
        self.answers: list[dict] = []        # ad-library answers, oldest first
        self.names: list[list[str]] = []     # advertiser-name answers
        self.page.on("response", self._on_response)
        self.page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_timeout(5000)

    def _on_response(self, resp):
        try:
            if "ApiResource/get" not in resp.url or resp.status != 200:
                return
            if "ad_library" in resp.url:
                rr = (resp.json() or {}).get("resource_response") or {}
                self.answers.append(rr)
            elif "advertiser_names" in resp.url:
                rr = (resp.json() or {}).get("resource_response") or {}
                self.names.append(list((rr.get("data") or {}).get("advertiser_names") or []))
        except Exception:
            pass

    def close(self):
        for x in (self._ctx, self._browser):
            try:
                x.close()
            except Exception:
                pass
        try:
            self._pw.stop()
        except Exception:
            pass


class Stop(RuntimeError):
    pass


class Source:
    name = "pinterest"
    lookback_days = 364
    advertiser_refresh_days = 30
    Stop = Stop

    def __init__(self, page: Page | None = None, sess: requests.Session | None = None, countries=COUNTRIES, sleep=time.sleep):
        self._page = page
        self.s = sess or requests.Session()
        self.s.headers.setdefault("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36")
        self.sleep, self.countries = sleep, list(countries)
        self.requests, self.notes = 0, []

    @property
    def page(self) -> Page:
        if self._page is None:
            self._page = Page()
        return self._page

    def close(self):
        if self._page is not None:
            self._page.close()

    def state_extra(self) -> dict:
        return {"notes": self.notes[-20:]}

    # ---- driving the page ----
    def _type_name(self, name: str) -> None:
        pg = self.page.page
        box = pg.locator("#combobox-advertiserName")
        box.fill("")
        box.fill(name)
        pg.wait_for_timeout(SETTLE_MS)

    def _names(self, term: str) -> list[str]:
        """The advertiser names the page's own name search offers for a term."""
        before = len(self.page.names)
        self._type_name(term)
        self.requests += 1
        return [n for names in self.page.names[before:] for n in names if isinstance(n, str)]

    def advertisers(self, house: registry.House):
        keep, refused, seen = [], [], set()
        for term in [house.name] + list(house.search_terms or []):
            for name in self._names(term):
                if name in seen:
                    continue
                seen.add(name)
                (keep if is_house(house, name) else refused).append({"id": name, "name": name})
        return keep, refused

    def _search(self, name: str, country: str, start: date, end: date) -> list[dict]:
        """One search on the page: country, dates and the advertiser's name set, Search pressed, every page of
        the answer read."""
        pg, P = self.page.page, self.page
        pg.select_option("#country", label=COUNTRY_NAMES.get(country, country))
        for sel, d in (("#startDate", start), ("#endDate", end)):
            pg.fill(sel, d.strftime("%d/%m/%Y"))
            pg.keyboard.press("Tab")
        self._type_name(name)
        option = pg.locator(f"[role=option]:has-text('{name}')").first
        try:
            option.click(timeout=4000)
        except Exception:
            pg.keyboard.press("ArrowDown")
            pg.keyboard.press("Enter")
        before = len(P.answers)
        pg.click("text=Search", timeout=10000)
        self.requests += 1
        pg.wait_for_timeout(SETTLE_MS + 2500)
        items: list[dict] = []
        for _ in range(MAX_PAGES):
            new = P.answers[before:]
            if not new:
                break
            before = len(P.answers)
            page_items = [it for a in new for it in ((a.get("data") or {}).get("pin_ids_with_metadata") or []) if isinstance(it, dict)]
            items.extend(page_items)
            bookmark = new[-1].get("bookmark")
            if not page_items or not bookmark or bookmark == "-end-":
                break
            nxt = pg.locator("button[aria-label='Next page']")
            if nxt.count() == 0 or nxt.is_disabled():
                break
            nxt.click()
            self.requests += 1
            pg.wait_for_timeout(SETTLE_MS + 1500)
        return items

    def search_ads(self, house: registry.House, keep: list[dict], start: date, end: date):
        seen: set[str] = set()
        for adv in keep:
            for country in self.countries:
                win_end = end
                while win_end > start:
                    win_start = max(start, win_end - timedelta(days=WINDOW_DAYS))
                    try:
                        items = self._search(adv["name"], country, win_start, win_end)
                    except Exception as e:
                        self.notes.append(f"{adv['name']} {country} {win_start}: {e.__class__.__name__}: {str(e)[:120]}")
                        items = []
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


def main(argv: list[str] | None = None) -> int:
    return adlibs.main_for("pinterest", Source, argv)


if __name__ == "__main__":
    sys.exit(main())
