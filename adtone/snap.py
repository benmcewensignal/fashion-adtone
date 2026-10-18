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

import re
import sys
import time
import unicodedata
from datetime import date, timedelta

import requests

from . import adlibs, registry
from .tiktok import MAX_VIDEO_BYTES, NOT_THE_HOUSE

SEARCH = "https://adsapi.snapchat.com/v1/ads_library/ads/search"
COUNTRIES = "de be fi pt bg dk lt lu hr lv fr hu se si sk ie ee el mt it es at cy cz pl ro nl".split()
UA = "fashion-adtone research (Snap Ads Gallery API; contact via the repository)"
PACE_S = 2.0
MAX_429_IN_A_ROW = 8
DATE_FORMS = ("year", "year_to_yesterday", "quarter", "month", "none")
MAX_PAGES = 400               # pages of one search; a cursor that comes back again ends the search
# Legal and trading names the houses pay under, beyond the house's own name and search terms. Added to as
# the state shows which names answer; a name that never answers costs one request a run.
LEGAL = {
    "gucci": ["Guccio Gucci S.p.A.", "Guccio Gucci SpA"], "dior": ["Christian Dior Couture", "Christian Dior"],
    "chanel": ["CHANEL", "Chanel SAS"], "louis_vuitton": ["Louis Vuitton Malletier", "Louis Vuitton"],
    "prada": ["PRADA S.p.A.", "Prada SpA", "Prada spa"], "burberry": ["Burberry Limited", "Burberry"],
    "saint_laurent": ["Yves Saint Laurent SAS", "Saint Laurent"], "balenciaga": ["Balenciaga SA", "Balenciaga"],
    "bottega_veneta": ["Bottega Veneta SRL", "Bottega Veneta"], "hermes": ["Hermès International", "Hermes"],
    "fendi": ["FENDI S.r.l.", "Fendi"], "celine": ["CELINE", "Celine"], "loewe": ["LOEWE S.A.", "Loewe"],
    "givenchy": ["Givenchy SA", "Givenchy"], "valentino": ["Valentino S.p.A.", "Valentino"],
    "versace": ["Gianni Versace S.r.l.", "Versace"], "moncler": ["Moncler S.p.A.", "Moncler"],
    "armani": ["Giorgio Armani S.p.A.", "Giorgio Armani", "Armani"], "miu_miu": ["Miu Miu", "PRADA S.p.A.", "Prada spa"],
    "dolce_gabbana": ["Dolce & Gabbana S.r.l.", "Dolce & Gabbana", "Dolce&Gabbana"],
    "zegna": ["Ermenegildo Zegna", "Zegna"], "max_mara": ["Max Mara Fashion Group", "Max Mara"],
    "ferragamo": ["Salvatore Ferragamo S.p.A.", "Ferragamo"], "jacquemus": ["Jacquemus SAS", "Jacquemus"],
    "mcqueen": ["Alexander McQueen Trading Ltd", "Alexander McQueen"], "balmain": ["Balmain", "Pierre Balmain SA"],
    "stella_mccartney": ["Stella McCartney Ltd", "Stella McCartney"], "lanvin": ["Lanvin", "Jeanne Lanvin SA"],
    "schiaparelli": ["Schiaparelli", "Maison Schiaparelli"], "the_row": ["The Row", "The Row LLC"],
    "tom_ford": ["Tom Ford", "Tom Ford International"], "chloe": ["Chloé", "Chloe", "Chloé SAS"],
    "alaia": ["Alaïa", "Alaia", "Azzedine Alaïa", "Maison Alaïa"], "margiela": ["Maison Margiela", "Margiela"],
    "jil_sander": ["Jil Sander", "Jil Sander S.p.A."], "dries_van_noten": ["Dries Van Noten", "Van Noten Andries NV"],
    "loro_piana": ["Loro Piana S.p.A.", "Loro Piana"], "brunello_cucinelli": ["Brunello Cucinelli S.p.A.", "Brunello Cucinelli"],
    "issey_miyake": ["Issey Miyake", "Issey Miyake Europe"], "comme_des_garcons": ["Comme des Garçons", "Comme des Garcons"],
}


class Stop(RuntimeError):
    pass


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def _words(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii").lower()
    return " " + re.sub(r"[^a-z0-9]+", " ", s).strip() + " "


def is_house(house: registry.House, name: str) -> bool:
    """The advertiser is the house: its name appears in the advertiser's as whole words (Guccio Gucci S.p.A.,
    not Guccione), and the advertiser is not a beauty or fragrance licensee or a reseller."""
    b = _norm(name)
    if not b or any(w in b for w in NOT_THE_HOUSE):
        return False
    bw = _words(name)
    names = {_words(n) for n in [house.name] + list(house.search_terms or []) + LEGAL.get(house.id, []) if len(_norm(n)) >= 4}
    return any(n in bw for n in names)


SUFFIX = {"spa", "sas", "srl", "sa", "ag", "nv", "bv", "ltd", "llc", "inc", "gmbh", "limited", "international", "europe", "emea",
          "italia", "italy", "france", "paris", "uk", "usa", "us", "japan", "korea", "china", "global", "eur", "group", "couture",
          "malletier", "official", "milano", "london", "deutschland", "espana", "spain", "nederland",
          # the lines a house's own account may be named for; the division then sorts them out of the fashion count
          "eyewear", "lunettes", "occhiali", "watches", "jewellery", "jewelry", "joaillerie", "horlogerie"}
# a place after the house's name, accepted from a page or account with a few ads at least (Loewe Greece): one with
# any other word is someone else's (Alaïa Bay, a surf park; Hermes Logistik, a carrier; Alaia Muse, a shop)
PLACES = {"greece", "hellas", "germany", "austria", "osterreich", "switzerland", "schweiz", "suisse", "belgium", "belgique",
          "netherlands", "holland", "sweden", "sverige", "denmark", "danmark", "norway", "norge", "finland", "suomi", "poland",
          "polska", "portugal", "ireland", "czech", "czechia", "hungary", "romania", "bulgaria", "croatia", "slovenia", "slovakia",
          "luxembourg", "monaco", "cyprus", "malta", "estonia", "latvia", "lithuania", "iceland", "turkey", "turkiye", "israel",
          "uae", "dubai", "qatar", "kuwait", "ksa", "mena", "canada", "mexico", "brasil", "brazil", "australia", "india",
          "singapore", "taiwan", "thailand", "vietnam", "malaysia", "indonesia", "philippines", "hongkong", "macau", "asia",
          "apac", "americas", "nordics", "benelux", "dach", "iberia", "roma", "rome", "madrid", "barcelona", "berlin", "munich",
          "munchen", "vienna", "wien", "zurich", "geneve", "geneva", "amsterdam", "brussels", "bruxelles", "stockholm",
          "copenhagen", "oslo", "helsinki", "warsaw", "lisbon", "lisboa", "dublin", "athens", "prague", "budapest", "nyc", "la"}
_SELF_SERVE_APP = re.compile(r"in[- ]?app|ios|android|app aus", re.I)      # Snap's self-serve accounts from the phone app
# the licensees that pay for a house's beauty or eyewear advertising under its name (L'Oréal for Prada, Miu Miu,
# Armani, Valentino, Saint Laurent and Margiela; Estée Lauder for Tom Ford; Coty for Gucci, Chloé, Burberry and Jil
# Sander; EuroItalia for Versace; Shiseido for Issey Miyake; Luxottica, Thélios, Marcolin and Safilo for eyewear)
_DIVISION = (("beauty", ("beaute", "beauty", "parfum", "perfume", "fragrance", "cosmetic", "pcd", "fnb", "f b", "makeup", "skincare",
                         "loreal", "esteelauder", "coty", "euroitalia", "shiseido")),
             ("eyewear", ("eyewear", "lunettes", "occhiali", "optical", "luxottica", "thelios", "marcolin", "safilo")),
             ("watches", ("watch", "horlogerie", "montres", "jewel", "joaillerie", "gioielli", "wfj", "haute horlogerie", "vertime")))


def is_own(house: registry.House, name: str, ads: int | None = None) -> bool:
    """The advertiser is the house itself: the name is the house's or one of its legal names (Gucci,
    GUCCI, Guccio Gucci S.p.A., CHANEL 샤넬), or the house's name with one more word that is a company
    suffix or a line (Prada spa, Prada Spa Eyewear), or a place with a few ads at least (Loewe Greece), and
    never a beauty or fragrance licensee or a reseller. A person with the name in theirs (Celine Anders,
    Franco Armani, Chloe Kate), a shop (De Valentino's Den Haag, Alaia Muse), a business that shares the
    name (Alaïa Bay, Hermes Logistik, Hermes Clinics) or a name with emoji in it (Celine ✌🏼, Dior Ndiaye👸)
    is not the house."""
    if not is_house(house, name):
        return False
    if any(unicodedata.category(ch) in ("So", "Sk") or 0x1F000 <= ord(ch) <= 0x1FAFF for ch in name):
        return False
    w = _words(name).strip()
    names = {_words(n).strip() for n in [house.name] + list(house.search_terms or []) + LEGAL.get(house.id, []) if len(_norm(n)) >= 4}
    if w in names:
        return True
    for n in sorted(names, key=len, reverse=True):
        if w.startswith(n + " "):
            rest = w[len(n) + 1:].split()
            return len(rest) == 1 and (rest[0] in SUFFIX or (rest[0] in PLACES and ads is not None and ads >= 3))
    return False


def division(*names: str) -> str:
    """What the account or payer name says the ad sells: fashion unless it names beauty, eyewear or
    watches and jewellery (Parfums Christian Dior, PCD_PC_FRA, OMG_CHANEL_Social_BE_FNB, Prada Spa Eyewear)."""
    text = " ".join(_words(n) for n in names if n)
    for div, words in _DIVISION:
        if any(f" {wd} " in text or (len(wd) > 4 and wd in text.replace(" ", "")) for wd in words):
            return div
    return "fashion"


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
        self.date_form: str | None = None

    def state_extra(self) -> dict:
        return {"names_answered": self.names_answered, "date_form": self.date_form}

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

    def _dates(self, form: str, start: date, end: date) -> dict:
        """The date fields in one of the forms tried: Snap's reference shows T00:00:00.000Z and refused a
        year-long window ending today on the first runs, so shorter windows and no window at all are tried."""
        iso = lambda d: f"{d.isoformat()}T00:00:00.000Z"
        if form == "year":
            return {"start_date": iso(start), "end_date": iso(end)}
        if form == "year_to_yesterday":
            return {"start_date": iso(start), "end_date": iso(end - timedelta(days=1))}
        if form == "quarter":
            return {"start_date": iso(end - timedelta(days=90)), "end_date": iso(end - timedelta(days=1))}
        if form == "month":
            return {"start_date": iso(end - timedelta(days=30)), "end_date": iso(end - timedelta(days=1))}
        return {}

    def search_ads(self, house: registry.House, keep: list[dict], start: date, end: date):
        seen: set[str] = set()
        for cand in keep:
            name = cand["name"]
            n_here = 0
            for status in ("ACTIVE", "PAUSED"):
                forms = [self.date_form] if self.date_form else DATE_FORMS
                url, pages, seen_urls = SEARCH, 0, set()
                while url and pages < MAX_PAGES and url not in seen_urls:
                    seen_urls.add(url)
                    pages += 1
                    body = None
                    for form in forms:
                        body = {"paying_advertiser_name": name, "countries": COUNTRIES, "status": status, **self._dates(form, start, end)}
                        try:
                            out = self.post(url, body)   # the next page is the same search at the address with the cursor
                        except RuntimeError as e:
                            if "time is not valid" in str(e).lower() or "date" in str(e).lower():
                                continue
                            raise
                        if self.date_form != form:
                            self.date_form = form
                        break
                    else:
                        raise RuntimeError("Snap refused every form of the dates: " + ", ".join(forms))
                    forms = [self.date_form]
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
        return self.own(house, payer, account)

    @staticmethod
    def own(house: registry.House, payer: str | None, account: str | None) -> bool:
        """The ad is the house's own: paid for in its name (not a person's with the name in it), from a
        company account rather than the phone app's self-serve one; Prada's accounts for Miu Miu are
        Miu Miu's, and not Prada's."""
        if account and _SELF_SERVE_APP.search(account):
            return False
        acc = _words(account or "")
        if house.id == "miu_miu":
            return " miu miu " in acc and (is_own(house, payer or "") or is_own(house, account or ""))
        if house.id == "prada" and " miu miu " in acc:
            return False
        return is_own(house, payer or "") or (payer is None and is_own(house, account or ""))

    def row_is_kept(self, row: dict, keep: list[dict], house: registry.House) -> bool:
        if row.get("advertiser") is None and row.get("account") is None:
            return True
        return self.own(house, row.get("advertiser"), row.get("account"))

    @staticmethod
    def row_division(row: dict) -> str:
        return division(row.get("account") or "", row.get("advertiser") or "")

    def ad_row(self, item: dict, house_id: str, run: str) -> dict:
        """The row, from the fields the first live run showed (17 October 2026): paying_advertiser_name,
        ad_account_name, impressions_total, impressions_map by country, start_date, status, targeting_v2,
        creative_type, headline and name (kept only as features and the line the ad sells)."""
        from . import copyfeat, thread
        by_country = find(item, "impressionsmap", "impressionsbycountry", "impressions.*country.*", want=(dict, list))
        texts = [t for t in (item.get("headline"), item.get("name")) if isinstance(t, str) and t.strip()]
        tv2 = item.get("targeting_v2") if isinstance(item.get("targeting_v2"), dict) else None
        demo = (tv2 or {}).get("demographics") or []
        payer = find(item, "payingadvertisername", "payingadvertiser", "paidby", "advertisername", want=(str,))
        account = find(item, "adaccountname", "accountname", want=(str,))
        return {"ad_id": str(find(item, "id", "adid", "ad_id") or ""), "house_id": house_id,
                "advertiser": payer, "account": account, "division": division(account, payer),
                "first_shown": _day(find(item, "startdate", "start.*", "firstshown.*", want=(str, int))),
                "last_shown": _day(find(item, "enddate", "end.*", "lastshown.*", want=(str, int))),
                "status": find(item, "status", "adstatus", want=(str,)) or item.get("status_searched"),
                "review_status": item.get("review_status"),
                "impressions": find(item, "impressionstotal", "totalimpressions", "impressions", want=(int, str)),
                "impressions_by_country": {k: v for k, v in by_country.items() if v} if isinstance(by_country, dict) else
                ({str(c.get("country") or c.get("countryCode") or c.get("country_code")): c.get("impressions") or c.get("count")
                  for c in by_country if isinstance(c, dict)} if isinstance(by_country, list) else None),
                "creative_type": find(item, "creativetype", "adtype", "topsnapmediatype", want=(str,)),
                "ad_type": item.get("ad_type"),
                "targeting": ({"min_age": next((d.get("min_age") for d in demo if isinstance(d, dict) and d.get("min_age")), None),
                               "age_groups": sorted({g for d in demo if isinstance(d, dict) for g in (d.get("age_groups") or [])}) or None,
                               "devices": tv2.get("devices") or None, "regulated": tv2.get("regulated_content")} if tv2
                              else find(item, "targeting.*", "targets", want=(dict, list))),
                "copy": copyfeat.features(texts) if texts else None,
                "line": thread.ad_line(texts) if texts else None,
                "season": thread.ad_season(texts) if texts else None,
                "last_run": run}

    def media_urls(self, item: dict) -> tuple[list[str], dict]:
        # the download address first, then any image, then video stills; at most five pictures and two videos
        urls = [(k, u) for k, u in urls_in(item) if not any(w in _norm(k) for w in ("profile", "logo", "icon", "avatar"))]
        ordered = sorted(urls, key=lambda kv: (0 if "download" in _norm(kv[0]) else 1 if any(w in _norm(kv[0]) for w in ("image", "thumbnail", "preview")) else 2))
        return [u for _, u in ordered], {"max_video_bytes": MAX_VIDEO_BYTES}

    def media_for(self, item: dict) -> list[dict]:
        urls, kw = self.media_urls(item)
        return adlibs.fetch_media(self.s, urls, **kw)


def main(argv: list[str] | None = None) -> int:
    return adlibs.main_for("snap", Source, argv)


if __name__ == "__main__":
    sys.exit(main())
