"""Snapchat: the houses' ads in Snap's Ads Gallery, its ad library for the EU under the Digital Services Act,
read through Snap's own open API (developers.snap.com, "Ads Gallery API": the endpoints "are open for use by
3rd parties and do not require an OAuth token", read 17 October 2026).

    python -m adtone.snap collect --run <id> [--budget-min 60] [--houses gucci,dior]
    python -m adtone.snap probe --run <id>

What the API gives: ads delivered in the 27 EU states in the last twelve months, searched by the paying
advertiser's name, with start date, status, impressions in all and by country, the creative's media and the
targeting. There is no search by keyword and no UK coverage. The paying advertiser's name has to match, so
each house is tried under its own name, its search terms and the legal names on file, and the names that
answered are kept in the state. The exact field names of an ad are not documented (the reference shows a
placeholder), so the first live run records the shape of a record in the state and the row reads the fields
by name patterns until that shape is pinned here.

Pictures and three frames of each video go through the reader and the fingerprint, in memory. Snap's
gallery was unavailable most of April 2026 under its own rate limiting (AI Forensics), and still answers
HTTP 429 often, so requests are paced and a run that is refused repeatedly stops and says so.
"""
from __future__ import annotations

import io
import re
import sys
import time
import unicodedata
from datetime import date

import requests

from . import adlibs, registry
from .media import MediaError, to_fetched
from .tiktok import FRAMES_AT, MAX_VIDEO_BYTES, NOT_THE_HOUSE, video_frames

SEARCH = "https://adsapi.snapchat.com/v1/ads_library/ads/search"
COUNTRIES = "de be fi pt bg dk lt lu hr lv fr hu se si sk ie ee el mt it es at cy cz pl ro nl".split()
UA = "fashion-adtone research (Snap Ads Gallery API; contact via the repository)"
PACE_S = 2.0
MAX_429_IN_A_ROW = 8
# Legal and trading names the houses pay under, beyond the house's own name and search terms. Added to as
# the state shows which names answer; a name that never answers costs one request a run.
LEGAL = {
    "gucci": ["Guccio Gucci S.p.A.", "Guccio Gucci SpA"], "dior": ["Christian Dior Couture", "Christian Dior"],
    "chanel": ["CHANEL", "Chanel SAS"], "louis_vuitton": ["Louis Vuitton Malletier", "Louis Vuitton"],
    "prada": ["PRADA S.p.A.", "Prada SpA"], "burberry": ["Burberry Limited", "Burberry"],
    "saint_laurent": ["Yves Saint Laurent SAS", "Saint Laurent"], "balenciaga": ["Balenciaga SA", "Balenciaga"],
    "bottega_veneta": ["Bottega Veneta SRL", "Bottega Veneta"], "hermes": ["Hermès International", "Hermes"],
    "fendi": ["FENDI S.r.l.", "Fendi"], "celine": ["CELINE", "Celine"], "loewe": ["LOEWE S.A.", "Loewe"],
    "givenchy": ["Givenchy SA", "Givenchy"], "valentino": ["Valentino S.p.A.", "Valentino"],
    "versace": ["Gianni Versace S.r.l.", "Versace"], "moncler": ["Moncler S.p.A.", "Moncler"],
    "armani": ["Giorgio Armani S.p.A.", "Giorgio Armani", "Armani"], "miu_miu": ["Miu Miu", "PRADA S.p.A."],
    "dolce_gabbana": ["Dolce & Gabbana S.r.l.", "Dolce & Gabbana", "Dolce&Gabbana"],
    "zegna": ["Ermenegildo Zegna", "Zegna"], "max_mara": ["Max Mara Fashion Group", "Max Mara"],
    "ferragamo": ["Salvatore Ferragamo S.p.A.", "Ferragamo"], "jacquemus": ["Jacquemus SAS", "Jacquemus"],
    "mcqueen": ["Alexander McQueen Trading Ltd", "Alexander McQueen"], "balmain": ["Balmain", "Pierre Balmain SA"],
    "stella_mccartney": ["Stella McCartney Ltd", "Stella McCartney"], "lanvin": ["Lanvin", "Jeanne Lanvin SA"],
    "schiaparelli": ["Schiaparelli", "Maison Schiaparelli"], "the_row": ["The Row", "The Row LLC"],
    "tom_ford": ["Tom Ford", "Tom Ford International"], "chloe": ["Chloé", "Chloe", "Chloé SAS"],
    "alaia": ["Alaïa", "Alaia", "Azzedine Alaïa"], "margiela": ["Maison Margiela", "Margiela"],
    "jil_sander": ["Jil Sander", "Jil Sander S.p.A."], "dries_van_noten": ["Dries Van Noten", "Van Noten Andries NV"],
    "loro_piana": ["Loro Piana S.p.A.", "Loro Piana"], "brunello_cucinelli": ["Brunello Cucinelli S.p.A.", "Brunello Cucinelli"],
    "issey_miyake": ["Issey Miyake", "Issey Miyake Europe"], "comme_des_garcons": ["Comme des Garçons", "Comme des Garcons"],
}


class Stop(RuntimeError):
    pass


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def is_house(house: registry.House, name: str) -> bool:
    b = _norm(name)
    if not b or any(w in b for w in NOT_THE_HOUSE):
        return False
    names = {_norm(n) for n in [house.name] + list(house.search_terms or []) + LEGAL.get(house.id, []) if len(_norm(n)) >= 4}
    return any(n in b for n in names)


def find(obj, *patterns: str, want=(str, int, float, list, dict), depth: int = 0):
    """The first value under a key matching one of the patterns (case and separators ignored), searched
    breadth first through nested dicts and lists."""
    if depth > 5 or not isinstance(obj, (dict, list)):
        return None
    items = obj.items() if isinstance(obj, dict) else [(None, v) for v in obj]
    for k, v in items:
        if k is not None and any(re.fullmatch(p, _norm(str(k))) for p in patterns) and isinstance(v, want):
            return v
    for _, v in items:
        got = find(v, *patterns, want=want, depth=depth + 1)
        if got is not None:
            return got
    return None


def urls_in(obj, depth: int = 0) -> list[tuple[str, str]]:
    """Every (key, url) of a media-looking address in a record."""
    out = []
    if depth > 6:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and v.startswith("http") and any(w in _norm(k) for w in ("media", "url", "src", "preview", "thumbnail", "video", "image")):
                out.append((str(k), v))
            else:
                out.extend(urls_in(v, depth + 1))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(urls_in(v, depth + 1))
    return out


def _day(v) -> str | None:
    s = str(v or "")
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    if re.fullmatch(r"\d{13}", s):   # milliseconds since the epoch
        return date.fromtimestamp(int(s) / 1000).isoformat()
    if re.fullmatch(r"\d{10}", s):
        return date.fromtimestamp(int(s)).isoformat()
    return None


class Source:
    name = "snap"
    lookback_days = 364
    advertiser_refresh_days = 30
    Stop = Stop

    def __init__(self, sess: requests.Session | None = None, sleep=time.sleep, pace_s: float = PACE_S):
        self.s = sess or requests.Session()
        self.s.headers.setdefault("User-Agent", UA)
        self.sleep, self.pace_s = sleep, pace_s
        self.requests, self.refused_in_a_row, self.last = 0, 0, 0.0
        self.names_answered: dict[str, list[str]] = {}

    def state_extra(self) -> dict:
        return {"names_answered": self.names_answered}

    def post(self, url: str, body: dict) -> dict:
        attempt = 0
        while True:
            attempt += 1
            wait = self.pace_s - (time.monotonic() - self.last)
            if wait > 0:
                self.sleep(wait)
            self.last = time.monotonic()
            self.requests += 1
            r = self.s.post(url, json=body, timeout=60, headers={"Content-Type": "application/json"})
            if r.status_code == 429 or r.status_code >= 500:
                self.refused_in_a_row += 1
                if self.refused_in_a_row >= MAX_429_IN_A_ROW:
                    raise Stop(f"Snap refused {self.refused_in_a_row} requests in a row (HTTP {r.status_code})")
                self.sleep(min(60.0, 2.0 * 2 ** attempt))
                continue
            self.refused_in_a_row = 0
            try:
                out = r.json()
            except ValueError:
                raise RuntimeError(f"Snap HTTP {r.status_code}: not JSON")
            if r.status_code != 200 or str(out.get("request_status", "SUCCESS")).upper() not in ("SUCCESS", "OK"):
                raise RuntimeError(f"Snap HTTP {r.status_code}: {str(out)[:200]}")
            return out

    # ---- the source protocol ----
    def advertisers(self, house: registry.House):
        names = []
        for n in [house.name] + list(house.search_terms or []) + LEGAL.get(house.id, []):
            if n and n not in names:
                names.append(n)
        return [{"id": n, "name": n} for n in names], []

    def search_ads(self, house: registry.House, keep: list[dict], start: date, end: date):
        seen: set[str] = set()
        for cand in keep:
            name = cand["name"]
            n_here = 0
            for status in ("ACTIVE", "PAUSED"):
                body = {"paying_advertiser_name": name, "countries": COUNTRIES, "status": status,
                        "start_date": f"{start.isoformat()}T00:00:00.000Z", "end_date": f"{end.isoformat()}T23:59:59.000Z"}
                url = SEARCH
                while url:
                    out = self.post(url, body)   # the next page is the same search at the address with the cursor
                    for wrap in out.get("ad_previews") or []:
                        item = wrap.get("ad_preview") if isinstance(wrap, dict) and "ad_preview" in wrap else wrap
                        if not isinstance(item, dict):
                            continue
                        ad_id = str(find(item, "id", "adid", "ad_id") or "")
                        if ad_id in seen:
                            continue
                        seen.add(ad_id)
                        n_here += 1
                        yield {"status_searched": status, "name_searched": name, **item}
                    url = ((out.get("paging") or {}).get("next_link") or "").replace("http://adsapisc.appspot.com", "https://adsapi.snapchat.com")
            if n_here:
                self.names_answered.setdefault(house.id, [])
                if name not in self.names_answered[house.id]:
                    self.names_answered[house.id].append(name)

    def is_houses(self, item: dict, house: registry.House, keep: list[dict]) -> bool:
        payer = find(item, "payingadvertisername", "payingadvertiser", "paidby", "paidfor.*", "advertisername", "advertiser", want=(str,))
        account = find(item, "adaccountname", "accountname", "brandname", want=(str,))
        if payer is None and account is None:   # no name in the record: the search was by the house's own name
            return True
        return is_house(house, payer or "") or (payer is None and is_house(house, account or ""))

    def ad_row(self, item: dict, house_id: str, run: str) -> dict:
        by_country = find(item, "impressionsbycountry", "impressions.*country.*", want=(dict, list))
        return {"ad_id": str(find(item, "id", "adid", "ad_id") or ""), "house_id": house_id,
                "advertiser": find(item, "payingadvertisername", "payingadvertiser", "paidby", "advertisername", want=(str,)),
                "account": find(item, "adaccountname", "accountname", want=(str,)),
                "first_shown": _day(find(item, "startdate", "start.*", "firstshown.*", want=(str, int))),
                "last_shown": _day(find(item, "enddate", "end.*", "lastshown.*", want=(str, int))),
                "status": find(item, "status", "adstatus", want=(str,)) or item.get("status_searched"),
                "impressions": find(item, "impressionstotal", "totalimpressions", "impressions", want=(int, str)),
                "impressions_by_country": by_country if isinstance(by_country, dict) else
                ({str(c.get("country") or c.get("countryCode") or c.get("country_code")): c.get("impressions") or c.get("count")
                  for c in by_country if isinstance(c, dict)} if isinstance(by_country, list) else None),
                "creative_type": find(item, "creativetype", "adtype", "topsnapmediatype", want=(str,)),
                "targeting": find(item, "targeting", "targets", want=(dict, list)),
                "last_run": run}

    def media_for(self, item: dict) -> list[dict]:
        got = []
        urls = urls_in(item)
        # the download address first, then any image, then video stills; at most five pictures and two videos
        ordered = sorted(urls, key=lambda kv: (0 if "download" in _norm(kv[0]) else 1 if "image" in _norm(kv[0]) or "thumbnail" in _norm(kv[0]) or "preview" in _norm(kv[0]) else 2))
        images = videos = 0
        for key, u in ordered:
            if images >= 5 and videos >= 2:
                break
            r = self.s.get(u, timeout=90)
            if r.status_code != 200 or not r.content:
                continue
            ctype = ((getattr(r, "headers", None) or {}).get("Content-Type") or "").lower()
            if ctype.startswith("video") or u.split("?")[0].lower().endswith((".mp4", ".mov", ".webm")):
                if videos >= 2 or len(r.content) > MAX_VIDEO_BYTES:
                    continue
                videos += 1
                for a, img in zip(FRAMES_AT, video_frames(r.content)):
                    buf = io.BytesIO()
                    img.convert("RGB").save(buf, format="JPEG", quality=92)
                    got.append({"kind": "frame", "at": a, "fetched": to_fetched(buf.getvalue())})
            elif images < 5:
                try:
                    got.append({"kind": "image", "fetched": to_fetched(r.content)})
                    images += 1
                except MediaError:
                    continue
        return got


def main(argv: list[str] | None = None) -> int:
    return adlibs.main_for("snap", Source, argv)


if __name__ == "__main__":
    sys.exit(main())
