"""LinkedIn: the houses' ads in LinkedIn's Ad Library, its ad library for the EU under the Digital Services
Act, through LinkedIn's own Ad Library API (a developer app's product, "Ad Library API", granted on review;
GET https://api.linkedin.com/rest/adLibrary?q=criteria with the advertiser, a Linkedin-Version month and
the Rest.li protocol header, as LinkedIn's developer portal describes it; its public documentation page
was not reachable on 17 October 2026, so the response is read by name patterns and its shape recorded).
The library's web pages refuse the runner's addresses, and that refusal is left as it is.

    python -m adtone.linkedin collect --run <id> [--budget-min 40] [--houses gucci,dior]
    python -m adtone.linkedin probe --run <id>

Needs LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET (the app's, for a client-credentials token) or
LINKEDIN_ACCESS_TOKEN, in the repository's secrets; LINKEDIN_VERSION (a month, YYYYMM) as a repository
variable, default this month's. What the API gives: each ad's creative, advertiser and payer, format,
first and latest impression dates, restricted status, and for EU-served ads the impression ranges by
country and the targeting parameters disclosed. No spend; no UK detail. Pictures go through the reader
and the fingerprint, in memory; ad text is kept only as features.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timezone

import requests

from . import adlibs, copyfeat, registry, thread
from .snap import _day, _norm, find, is_house, urls_in

ENDPOINT = "https://api.linkedin.com/rest/adLibrary"
TOKEN = "https://www.linkedin.com/oauth/v2/accessToken"
UA = "fashion-adtone research (LinkedIn Ad Library API; contact via the repository)"
PACE_S = 1.0
PAGE = 25
MAX_PAGES = 80


class Stop(RuntimeError):
    pass


class Source:
    name = "linkedin"
    lookback_days = 364
    advertiser_refresh_days = 30
    Stop = Stop

    def __init__(self, sess: requests.Session | None = None, token: str | None = None, version: str | None = None,
                 sleep=time.sleep, pace_s: float = PACE_S):
        self.s = sess or requests.Session()
        self.s.headers.setdefault("User-Agent", UA)
        self.sleep, self.pace_s, self.last = sleep, pace_s, 0.0
        self.requests, self.refused = 0, 0
        self.version = version or os.environ.get("LINKEDIN_VERSION", "").strip() or datetime.now(timezone.utc).strftime("%Y%m")
        self.token = token if token is not None else self._token()
        self.s.headers.update({"Authorization": f"Bearer {self.token}", "X-RestLi-Protocol-Version": "2.0.0",
                               "Linkedin-Version": self.version})
        self.names_answered: dict[str, list[str]] = {}

    def _token(self) -> str:
        tok = os.environ.get("LINKEDIN_ACCESS_TOKEN", "").strip()
        if tok:
            return tok
        cid, sec = os.environ.get("LINKEDIN_CLIENT_ID", "").strip(), os.environ.get("LINKEDIN_CLIENT_SECRET", "").strip()
        r = self.s.post(TOKEN, data={"grant_type": "client_credentials", "client_id": cid, "client_secret": sec}, timeout=60)
        body = r.json() if r.status_code == 200 else {}
        if not body.get("access_token"):
            raise Stop(f"LinkedIn refused the app's credentials (HTTP {r.status_code})")
        return body["access_token"]

    def state_extra(self) -> dict:
        return {"version": self.version, "names_answered": self.names_answered}

    def get(self, params: dict) -> dict:
        attempt = 0
        while True:
            attempt += 1
            wait = self.pace_s - (time.monotonic() - self.last)
            if wait > 0:
                self.sleep(wait)
            self.last = time.monotonic()
            self.requests += 1
            r = self.s.get(ENDPOINT, params=params, timeout=60)
            if r.status_code in (401, 403):
                raise Stop(f"LinkedIn refused the token or the product (HTTP {r.status_code}): {r.text[:160]}")
            if r.status_code == 429 or r.status_code >= 500:
                self.refused += 1
                if self.refused >= 8:
                    raise Stop(f"LinkedIn refused {self.refused} requests in a row (HTTP {r.status_code})")
                self.sleep(min(90.0, 2.5 * 2 ** attempt))
                continue
            self.refused = 0
            if r.status_code != 200:
                raise RuntimeError(f"LinkedIn HTTP {r.status_code}: {r.text[:200]}")
            try:
                return r.json()
            except ValueError:
                raise RuntimeError("LinkedIn: not JSON")

    def advertisers(self, house: registry.House):
        names = []
        for n in [house.name] + list(house.search_terms or []):
            if n and n not in names:
                names.append(n)
        return [{"id": n, "name": n} for n in names], []

    def search_ads(self, house: registry.House, keep: list[dict], start: date, end: date):
        seen: set[str] = set()
        for adv in keep:
            n_here, startp = 0, 0
            for _ in range(MAX_PAGES):
                out = self.get({"q": "criteria", "advertiser": adv["name"], "start": startp, "count": PAGE})
                elems = out.get("elements") if isinstance(out, dict) else None
                if not isinstance(elems, list):
                    elems = find(out, "elements", "ads", "results", want=(list,)) or []
                for item in elems:
                    if not isinstance(item, dict):
                        continue
                    ad_id = str(find(item, "adurn", "id", "adid", "urn", want=(str, int)) or "")
                    if ad_id in seen:
                        continue
                    seen.add(ad_id)
                    n_here += 1
                    yield {"name_searched": adv["name"], **item}
                paging = out.get("paging") if isinstance(out, dict) else None
                total = (paging or {}).get("total")
                if len(elems) < PAGE or (isinstance(total, int) and startp + PAGE >= total):
                    break
                startp += PAGE
            if n_here:
                self.names_answered.setdefault(house.id, [])
                if adv["name"] not in self.names_answered[house.id]:
                    self.names_answered[house.id].append(adv["name"])

    def is_houses(self, item: dict, house: registry.House, keep: list[dict]) -> bool:
        adv = find(item, "advertisername", "advertiser", "accountowner", "ownername", want=(str,))
        payer = find(item, "payer", "paidby", "payername", "paidfor.*", want=(str,))
        if adv is None and payer is None:
            return True
        return is_house(house, adv or "") or is_house(house, payer or "")

    def ad_row(self, item: dict, house_id: str, run: str) -> dict:
        texts = [t for t in (find(item, "headline", want=(str,)), find(item, "body", "text", "description", want=(str,)))
                 if isinstance(t, str) and t.strip()]
        by_country = find(item, "impressionsbycountry", "impressions.*country.*", "countryimpressions", want=(dict, list))
        if isinstance(by_country, list):
            by_country = {str(c.get("country") or c.get("countryCode") or c.get("code")): c.get("impressions") or c.get("share") or c.get("value")
                          for c in by_country if isinstance(c, dict)}
        targeting = find(item, "targeting", "targetingfacets", "adtargeting", want=(dict, list))
        return {"ad_id": str(find(item, "adurn", "id", "adid", "urn", want=(str, int)) or ""), "house_id": house_id,
                "advertiser": find(item, "advertisername", "advertiser", "accountowner", want=(str,)),
                "payer": find(item, "payer", "paidby", "payername", want=(str,)),
                "format": find(item, "adformat", "format", "type", want=(str,)),
                "first_shown": _day(find(item, "firstimpression.*", "firstshown.*", "startdate", "start.*", want=(str, int))),
                "last_shown": _day(find(item, "latestimpression.*", "lastimpression.*", "lastshown.*", "enddate", "end.*", want=(str, int))),
                "impressions_range": find(item, "totalimpressions", "impressions", "impressionsrange", want=(str, dict)),
                "impressions_by_country": by_country or None,
                "targeting": targeting, "restricted": find(item, "isrestricted", "restricted", want=(bool,)),
                "copy": copyfeat.features(texts) if texts else None, "line": thread.ad_line(texts) if texts else None,
                "last_run": run}

    def media_urls(self, item: dict) -> tuple[list[str], dict]:
        urls = [u for k, u in urls_in(item) if any(w in _norm(k) for w in ("image", "media", "thumbnail", "video", "creative"))]
        return urls, {"max_images": 4, "max_videos": 1}

    def media_for(self, item: dict) -> list[dict]:
        urls, kw = self.media_urls(item)
        return adlibs.fetch_media(self.s, urls, **kw)


def main(argv: list[str] | None = None) -> int:
    import os as _os
    has_token = _os.environ.get("LINKEDIN_ACCESS_TOKEN", "").strip()
    needs = () if has_token else ("LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET")
    return adlibs.main_for("linkedin", Source, argv, needs=needs)


if __name__ == "__main__":
    sys.exit(main())
