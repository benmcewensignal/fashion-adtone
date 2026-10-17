"""X: the houses' ads in X's Ads Repository, its ad library for the EU under the Digital Services Act, through
the route X's help pages document (help.x.com, "Ads transparency", read 17 October 2026): a developer
account's bearer token, a CreateExportReportMutation request naming the advertiser's user id, a country and
a delivery window, then the report's status until its CSV is ready.

    python -m adtone.xads collect --run <id> [--budget-min 30] [--houses gucci,dior]
    python -m adtone.xads probe --run <id>

Needs X_BEARER_TOKEN (an app's bearer token from developer.x.com) in the repository's secrets, and each
house's X account in reference/x_accounts.csv (handle, and the numeric user id where known; a missing id
is looked up by handle, which X's free tier may refuse, and the refusal is recorded). What the repository
gives: the advertiser, the funding entity, the main targeting parameters, impressions and reach, and
whether the ad was halted; the ad's own content is not in the file, only an address for it, so there are
no pictures to read. EU only; no UK. X was found in breach over this repository and has until early 2027
to rebuild it with an API and content, so this reader records the file's columns (never a row's values)
in the state and keeps every column but the ad's text.
"""
from __future__ import annotations

import csv
import io
import os
import re
import sys
import time
from datetime import date

import requests

from . import adlibs, config, registry

CREATE = "https://api.twitter.com/graphql/e9OJa7fJKHtHftkNzcRkzw/CreateExportReportMutation"
STATUS = "https://api.twitter.com/graphql/0RTLTx4DunPS6rVj-1cLCg/GetExportReportStatusQuery"
LOOKUP = "https://api.twitter.com/2/users/by/username/{handle}"
ACCOUNTS = config.ROOT / "reference" / "x_accounts.csv"
COUNTRIES = ("FR", "IT", "DE", "ES", "NL", "BE", "AT", "IE", "PT", "SE", "DK", "PL")   # the larger EU markets first
UA = "fashion-adtone research (X Ads Repository; contact via the repository)"
POLL_S = 15
MAX_WAIT_S = 240
TEXT_COLUMNS = ("text", "body", "copy", "content", "message")


class Stop(RuntimeError):
    pass


def accounts() -> dict[str, dict]:
    if not ACCOUNTS.exists():
        return {}
    return {r["house"]: r for r in csv.DictReader(open(ACCOUNTS, encoding="utf-8")) if r.get("house")}


class Source:
    name = "xads"
    lookback_days = 364
    advertiser_refresh_days = 30
    Stop = Stop

    def __init__(self, sess: requests.Session | None = None, token: str | None = None, sleep=time.sleep,
                 countries=COUNTRIES, clock=time.monotonic):
        self.s = sess or requests.Session()
        self.s.headers.setdefault("User-Agent", UA)
        self.token = token if token is not None else os.environ.get("X_BEARER_TOKEN", "").strip()
        self.s.headers["Authorization"] = f"Bearer {self.token}"
        self.sleep, self.clock, self.countries = sleep, clock, list(countries)
        self.requests, self.columns, self.refusals = 0, None, []

    def state_extra(self) -> dict:
        return {"columns": self.columns, "refusals": self.refusals[-20:]}

    def _check(self, r) -> None:
        if r.status_code in (401, 403):
            raise Stop(f"X refused the bearer token or the route (HTTP {r.status_code})")
        if r.status_code == 429:
            raise Stop("X: rate limit reached")

    def advertisers(self, house: registry.House):
        acc = accounts().get(house.id)
        if not acc or not acc.get("handle"):
            return [], [{"why": "no X account on file in reference/x_accounts.csv"}]
        uid = (acc.get("user_id") or "").strip()
        if not uid:
            self.requests += 1
            r = self.s.get(LOOKUP.format(handle=acc["handle"].lstrip("@")), timeout=60)
            self._check(r)
            if r.status_code != 200:
                self.refusals.append({"house": house.id, "lookup": r.status_code, "text": r.text[:160]})
                return [], [{"why": f"user id lookup refused (HTTP {r.status_code}); add user_id to reference/x_accounts.csv"}]
            uid = str(((r.json() or {}).get("data") or {}).get("id") or "")
        return ([{"id": uid, "name": acc["handle"]}] if uid else []), []

    def _report(self, uid: str, country: str, start: date, end: date) -> str | None:
        """The CSV of one advertiser in one country over the window, or None when the request is refused."""
        self.requests += 1
        body = {"variables": {"user": uid, "geoLocation": country,
                              "deliveryRange": {"start_date": start.isoformat(), "end_date": end.isoformat()}}}
        r = self.s.post(CREATE, json=body, timeout=60)
        self._check(r)
        if r.status_code != 200:
            self.refusals.append({"create": r.status_code, "country": country, "text": r.text[:160]})
            return None
        out = r.json() if r.text else {}
        export_id = _find_key(out, "exportId") or _find_key(out, "export_id")
        if not export_id:
            self.refusals.append({"create": "no exportId", "country": country, "shape": adlibs.shape(out)})
            return None
        t0 = self.clock()
        while self.clock() - t0 < MAX_WAIT_S:
            self.sleep(POLL_S)
            self.requests += 1
            r = self.s.get(STATUS, params={"variables": '{"exportId": "%s"}' % export_id}, timeout=60)
            self._check(r)
            if r.status_code != 200:
                continue
            ctype = (r.headers.get("Content-Type") or "").lower() if hasattr(r, "headers") else ""
            if "csv" in ctype or r.text.lstrip().startswith(("advertiser", "Advertiser", "\"")):
                return r.text
            out = r.json() if r.text else {}
            url = _find_key(out, "downloadUrl") or _find_key(out, "download_url") or _find_key(out, "url")
            status = str(_find_key(out, "status") or "").lower()
            if url and (status in ("", "done", "complete", "completed", "ready", "success")):
                self.requests += 1
                f = self.s.get(url, timeout=120)
                if f.status_code == 200:
                    return f.text
            if status in ("failed", "error"):
                self.refusals.append({"status": status, "country": country})
                return None
        self.refusals.append({"status": "timeout", "country": country})
        return None

    def search_ads(self, house: registry.House, keep: list[dict], start: date, end: date):
        for adv in keep:
            for country in self.countries:
                text = self._report(adv["id"], country, start, end)
                if not text or not text.strip():
                    continue
                rows = list(csv.DictReader(io.StringIO(text)))
                if rows and self.columns is None:
                    self.columns = list(rows[0].keys())
                for row in rows:
                    yield {"country_searched": country, "advertiser_id": adv["id"], **row}

    def is_houses(self, item: dict, house: registry.House, keep: list[dict]) -> bool:
        return True   # the report was asked for by the house's own account

    def ad_row(self, item: dict, house_id: str, run: str) -> dict:
        kept = {}
        for k, v in item.items():
            kk = re.sub(r"[^a-z0-9]+", "_", (k or "").lower()).strip("_")
            if not kk or any(w in kk for w in TEXT_COLUMNS):
                continue
            kept[kk] = v
        ad_id = kept.get("ad_id") or kept.get("tweet_id") or kept.get("id") or ""
        if not ad_id:
            url = next((v for k, v in kept.items() if "url" in k and isinstance(v, str) and v.startswith("http")), "")
            ad_id = url.rsplit("/", 1)[-1] if url else ""
        start = next((v for k, v in kept.items() if "start" in k or "first" in k), None)
        stop = next((v for k, v in kept.items() if "end" in k or "last" in k), None)
        return {"ad_id": str(ad_id), "house_id": house_id, "first_shown": (start or "")[:10] or None,
                "last_shown": (stop or "")[:10] or None, "country": item.get("country_searched"), "fields": kept, "last_run": run}

    def media_for(self, item: dict) -> list[dict]:
        return []


def _find_key(obj, key: str, depth: int = 0):
    if depth > 6:
        return None
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            got = _find_key(v, key, depth + 1)
            if got is not None:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = _find_key(v, key, depth + 1)
            if got is not None:
                return got
    return None


def main(argv: list[str] | None = None) -> int:
    return adlibs.main_for("xads", Source, argv, needs=("X_BEARER_TOKEN",))


if __name__ == "__main__":
    sys.exit(main())
