"""Client for the Meta Ad Library API (Graph API endpoint ads_archive).

Untested against the live API from the build environment (graph.facebook.com is not
reachable there). Behaviour is pinned by tests against recorded-shape fixtures; the
first live run is the real test, and the provenance log records what it saw.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Callable, Iterator

import requests

from .config import GRAPH_API_VERSION, GRAPH_BASE

log = logging.getLogger(__name__)

RATE_LIMIT_CODES = {4, 17, 32, 613, 80004}
TRANSIENT_CODES = {1, 2}
TOKEN_CODES = {190, 102, 463, 467}
_TOKEN_IN_TEXT = re.compile(r"(access_token=)[^&\s\"']+")


def scrub(text: str) -> str:
    """Remove access tokens from anything that might reach a log or a public file."""
    return _TOKEN_IN_TEXT.sub(r"\1<redacted>", str(text))


class GraphError(Exception):
    def __init__(self, message: str, code: int | None = None, subcode: int | None = None, status: int | None = None):
        super().__init__(scrub(message))
        self.code, self.subcode, self.status = code, subcode, status


class TokenError(GraphError):
    """Expired or invalid token. Never retried: the run stops and says so."""


class RateLimitError(GraphError):
    pass


class ParamError(GraphError):
    """The API rejected a parameter or field (code 100)."""


def classify(status: int, body: dict | None) -> GraphError:
    err = (body or {}).get("error") or {}
    code = err.get("code")
    sub = err.get("error_subcode")
    msg = err.get("message") or f"HTTP {status}"
    if status == 401 or code in TOKEN_CODES:
        return TokenError(msg, code, sub, status)
    if status == 429 or code in RATE_LIMIT_CODES:
        return RateLimitError(msg, code, sub, status)
    if code == 100:
        return ParamError(msg, code, sub, status)
    return GraphError(msg, code, sub, status)


def chunks(items: list, n: int) -> Iterator[list]:
    for i in range(0, len(items), n):
        yield items[i:i + n]


class AdLibraryClient:
    def __init__(self, token: str, version: str = GRAPH_API_VERSION, session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep, max_retries: int = 6, pause: float = 1.0):
        if not token:
            raise TokenError("no access token: set META_AD_LIBRARY_TOKEN")
        self.token = token
        self.base = f"{GRAPH_BASE}/{version}/ads_archive"
        self.session = session or requests.Session()
        self.sleep = sleep
        self.max_retries = max_retries
        self.pause = pause
        self.calls = 0
        self.notes: list[str] = []

    def _get(self, url: str, params: dict | None) -> dict:
        attempt = 0
        while True:
            self.calls += 1
            try:
                r = self.session.get(url, params=params, timeout=60)
            except requests.RequestException as e:
                err: GraphError = GraphError(f"network: {e}")
                status = None
            else:
                status = r.status_code
                try:
                    body = r.json()
                except ValueError:
                    body = None
                if status == 200 and body is not None and "error" not in body:
                    if self.pause:
                        self.sleep(self.pause)
                    return body
                err = classify(status, body)
            if isinstance(err, (TokenError, ParamError)):
                raise err
            transient = isinstance(err, RateLimitError) or err.code in TRANSIENT_CODES or status is None or (status or 0) >= 500
            if not transient or attempt >= self.max_retries:
                raise err
            wait = min(900, (60 if isinstance(err, RateLimitError) else 5) * 2 ** attempt)
            log.warning("retrying after %ss: %s", wait, err)
            self.sleep(wait)
            attempt += 1

    def search(self, *, countries: list[str], fields: list[str], page_ids: list[str] | None = None,
               search_terms: str | None = None, date_min: str | None = None, active_status: str = "ALL",
               limit: int = 250, max_rows: int | None = None) -> Iterator[dict]:
        if not page_ids and not search_terms:
            raise ValueError("the API needs search_page_ids or search_terms")
        if page_ids and len(page_ids) > 10:
            raise ValueError("at most 10 page ids per request")
        params = {
            "access_token": self.token,
            "ad_type": "ALL",
            "ad_active_status": active_status,
            "ad_reached_countries": json.dumps(countries),
            "fields": ",".join(fields),
            "limit": str(limit),
        }
        if page_ids:
            params["search_page_ids"] = json.dumps([str(p) for p in page_ids])
        if search_terms:
            params["search_terms"] = search_terms
        if date_min:
            params["ad_delivery_date_min"] = date_min
        url, p = self.base, params
        n = 0
        while True:
            body = self._get(url, p)
            data = body.get("data") or []
            if not data:
                return
            for row in data:
                yield row
                n += 1
                if max_rows and n >= max_rows:
                    return
            nxt = (body.get("paging") or {}).get("next")
            if not nxt:
                return
            url, p = nxt, None   # the next URL carries every parameter, token included

    def ads_for_pages(self, page_ids: list[str], countries: list[str], core: list[str], optional: list[str],
                      date_min: str | None) -> Iterator[dict]:
        """Every ad from these pages delivered in these countries since date_min, deduplicated.

        Falls back, and records why in self.notes: to the core field set if the API
        rejects an optional field, and to one country per request if it rejects the
        country list.
        """
        fields = core + optional
        seen: set[str] = set()
        for group in chunks(list(dict.fromkeys(page_ids)), 10):   # caller's order: deadlines first
            country_sets = [countries]
            while True:
                try:
                    for cs in country_sets:
                        for row in self.search(countries=cs, fields=fields, page_ids=group, date_min=date_min):
                            if row.get("id") in seen:
                                continue
                            seen.add(row.get("id"))
                            yield row
                    break
                except ParamError as e:
                    msg = str(e).lower()
                    if "field" in msg and fields != core:
                        self.notes.append(f"optional fields rejected, using core fields: {e}")
                        fields = list(core)
                        continue
                    if "countr" in msg and len(country_sets) == 1 and len(countries) > 1:
                        self.notes.append(f"country list rejected, querying one country at a time: {e}")
                        country_sets = [[c] for c in countries]
                        continue
                    raise
