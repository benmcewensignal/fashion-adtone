"""TikTok: the ads each house ran in Europe, from TikTok's Commercial Content API (its ad library
under the Digital Services Act), read by the reader like the Meta ads.

    python -m adtone.tiktok collect --run <id> [--budget-min 60] [--max-requests 800]
    python -m adtone.tiktok probe --run <id>

Needs TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET from an approved Commercial Content API application
(developers.tiktok.com, product "Commercial Content API", scope research.adlib.basic; TikTok says it
answers within two working days). Without them the run records that it waited and stops.

What the API gives, from its documentation (developers.tiktok.com/doc/commercial-content-api-query-ads,
read 7 October 2026): ads shown in the EU, EEA, Switzerland and the UK, searchable for a year after
they last ran, ten a request, with first and last shown dates, status, reach, and the addresses of
the ad's videos and images. Those addresses are signed and expire, so pictures are fetched in the
run that finds the ad. There is no filter by advertiser, so each house's ads are searched by its
name and kept only when the advertiser is the house: advertisers are matched by name once a month
and every match and refusal is recorded in the state for checking. Beauty and fragrance licensees
(L'Oréal's YSL Beauté, Coty's Gucci fragrances and the like) are refused: they are not the house.

Images go through the reader and the fingerprint. A video ad is read from three frames (a tenth of
the way in, half way, nine tenths), decoded in memory with PyAV when it is installed; nothing is
written to disk. TikTok's history is a year deep, so this source grows forward, like Meta's.
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from . import config, registry, store
from .media import MediaError, jpeg_for_model, to_fetched
from .score import ScoreError, score_batch

TOKEN = "https://open.tiktokapis.com/v2/oauth/token/"
ADS = "https://open.tiktokapis.com/v2/research/adlib/ad/query/"
ADVERTISERS = "https://open.tiktokapis.com/v2/research/adlib/advertiser/query/"
AD_FIELDS = ("ad.id,ad.first_shown_date,ad.last_shown_date,ad.status,ad.status_statement,ad.videos,ad.image_urls,"
             "ad.reach,advertiser.business_id,advertiser.business_name,advertiser.paid_for_by")
COUNTRIES = ("AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE "
             "NO IS LI GB CH").split()
UA = "fashion-adtone research (TikTok ad library; contact via the repository)"
LOOKBACK_DAYS = 364           # the library keeps an ad for a year after it last ran
ADVERTISER_REFRESH_DAYS = 30
REQUESTS_PER_RUN = 800        # TikTok's research APIs allow about a thousand requests a day
FRAMES_AT = (0.1, 0.5, 0.9)
MAX_VIDEO_BYTES = 60_000_000
BATCH = 16
NOT_THE_HOUSE = ("beaute", "beauty", "parfum", "fragrance", "cosmetic", "loreal", "coty", "esteelauder",
                 "interparfums", "shiseido", "outlet", "vintage", "resale", "secondhand", "preloved")


class Stop(RuntimeError):
    """The day's requests are used, or TikTok refuses the application's credentials."""


def paths() -> dict[str, Path]:
    b = config.DATA / "tiktok"
    return {"ads": b / "ads", "obs": b / "obs", "vectors": b / "vectors",
            "state": config.STATE_DIR / "tiktok.json", "prov": config.PROV_DIR / "tiktok.jsonl"}


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def is_house(house: registry.House, business_name: str) -> bool:
    """The advertiser is the house itself: its name contains the house's name, and it is not a beauty or
    fragrance licensee or a reseller."""
    b = _norm(business_name)
    if not b or any(w in b for w in NOT_THE_HOUSE):
        return False
    names = {_norm(n) for n in [house.name] + list(house.search_terms or []) if len(_norm(n)) >= 4}
    return any(n in b for n in names)


class Client:
    def __init__(self, sess, key: str, secret: str, max_requests: int = REQUESTS_PER_RUN, sleep=time.sleep):
        self.s, self.key, self.secret, self.sleep = sess, key, secret, sleep
        self.max_requests, self.requests, self.token = max_requests, 0, None
        self.country_filter = None

    def _token(self) -> str:
        r = self.s.post(TOKEN, data={"client_key": self.key, "client_secret": self.secret,
                                     "grant_type": "client_credentials"},
                        headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=60)
        body = r.json() if r.status_code == 200 else {}
        if not body.get("access_token"):
            err = body.get("error_description") or body.get("error") or f"HTTP {r.status_code}"
            raise Stop(f"TikTok refused the credentials: {err}")
        self.token = body["access_token"]
        return self.token

    def post(self, url: str, fields: str, body: dict) -> dict:
        for attempt in range(4):
            if self.requests >= self.max_requests:
                raise Stop(f"this run's {self.max_requests} requests are used")
            self.requests += 1
            r = self.s.post(url, params={"fields": fields}, json=body, timeout=60,
                            headers={"Authorization": f"Bearer {self.token or self._token()}",
                                     "Content-Type": "application/json"})
            try:
                out = r.json()
            except ValueError:
                out = {}
            code = ((out.get("error") or {}).get("code") or "").lower()
            if r.status_code == 200 and code in ("ok", ""):
                return out.get("data") or {}
            if code in ("access_token_invalid", "access_token_expired") or r.status_code == 401:
                self.token = None
                if attempt < 3:
                    continue
            if code == "rate_limit_exceeded" or r.status_code == 429:
                raise Stop("TikTok: daily request limit reached")
            if r.status_code >= 500 and attempt < 3:
                self.sleep(5.0 * 2 ** attempt)
                continue
            msg = (out.get("error") or {}).get("message") or f"HTTP {r.status_code}"
            raise RuntimeError(f"TikTok {code or r.status_code}: {str(msg)[:200]}")
        raise RuntimeError("TikTok kept failing")


def advertisers(cl: Client, house: registry.House) -> tuple[list[dict], list[dict]]:
    """The house's own advertiser accounts, and the near misses refused, from a search by its name."""
    data = cl.post(ADVERTISERS, "business_id,business_name,country_code", {"search_term": house.name[:50], "max_count": 50})
    keep, refused = [], []
    for a in data.get("advertisers") or []:
        row = {"business_id": str(a.get("business_id")), "business_name": a.get("business_name"),
               "country": a.get("country_code")}
        (keep if is_house(house, a.get("business_name") or "") else refused).append(row)
    return keep, refused


def _filters(cl: Client, start: date, end: date) -> dict:
    f = {"ad_published_date_range": {"min": start.strftime("%Y%m%d"), "max": end.strftime("%Y%m%d")}}
    if cl.country_filter == "country_code_list":
        f["country_code_list"] = list(COUNTRIES)
    else:
        f["country_code"] = "ALL"
    return f


def search_ads(cl: Client, house: registry.House, start: date, end: date):
    """Every ad found by the house's name in the window, ten at a time. The documentation shows two
    forms of the country filter; the other is tried once if the first is refused."""
    search_id = None
    while True:
        body = {"filters": _filters(cl, start, end), "search_term": house.name[:50], "max_count": 10}
        if search_id:
            body["search_id"] = search_id
        try:
            data = cl.post(ADS, AD_FIELDS, body)
        except RuntimeError as e:
            if cl.country_filter is None and "country" in str(e).lower():
                cl.country_filter = "country_code_list"
                continue
            raise
        if cl.country_filter is None:
            cl.country_filter = "country_code"
        for item in data.get("ads") or []:
            yield item
        has_more = data.get("has_more")
        search_id = data.get("search_id")
        if not (has_more is True or str(has_more).lower() == "true") or not search_id:
            return


def _day(v) -> str | None:
    s = str(v or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if re.fullmatch(r"\d{8}", s) else (s[:10] or None)


def ad_row(item: dict, house: str, run: str) -> dict:
    ad, adv = item.get("ad") or {}, item.get("advertiser") or {}
    reach = ad.get("reach") or {}
    seen = reach.get("unique_users_seen", reach.get("unique_user_seen"))
    return {"ad_id": str(ad.get("id")), "house_id": house, "business_id": str(adv.get("business_id", adv.get("buisness_id"))),
            "business_name": adv.get("business_name"), "paid_by": adv.get("paid_by") or adv.get("paid_for_by"),
            "first_shown": _day(ad.get("first_shown_date")), "last_shown": _day(ad.get("last_shown_date")),
            "status": ad.get("status"), "reach": seen, "n_videos": len(ad.get("videos") or []),
            "n_images": len(ad.get("image_urls") or []), "last_run": run}


def video_frames(data: bytes, at=FRAMES_AT) -> list:
    """Frames at the given fractions of the video's length, as PIL images, decoded in memory one at a
    time (seek, then decode to the frame). Empty when PyAV is not installed."""
    try:
        import av
    except ImportError:
        return []
    out = []
    try:
        with av.open(io.BytesIO(data)) as box:
            stream = box.streams.video[0]
            if stream.duration and stream.time_base:
                dur = float(stream.duration * stream.time_base)
            else:
                dur = (box.duration or 0) / 1_000_000
            if dur <= 0:   # no length on record: the first frame only
                for frame in box.decode(stream):
                    return [frame.to_image()]
                return []
            for a in at:
                t = a * dur
                box.seek(int(t / stream.time_base), stream=stream, backward=True, any_frame=False)
                for frame in box.decode(stream):
                    if frame.time is None or frame.time >= t - 0.05:
                        out.append(frame.to_image())
                        break
    except Exception as e:   # PyAV raises its own error types for damaged files
        raise MediaError(f"video: {e.__class__.__name__}") from None
    return out


def media_for(sess, item: dict) -> list[dict]:
    """The ad's pictures: its images, and three frames of each video. In memory only."""
    ad = item.get("ad") or {}
    got = []
    for u in (ad.get("image_urls") or [])[:5]:
        r = sess.get(u, timeout=45)
        if r.status_code == 200 and r.content:
            got.append({"kind": "image", "fetched": to_fetched(r.content)})
    for v in (ad.get("videos") or [])[:2]:
        url = v.get("url") if isinstance(v, dict) else v
        if not url:
            continue
        r = sess.get(url, timeout=90)
        if r.status_code != 200 or not r.content or len(r.content) > MAX_VIDEO_BYTES:
            continue
        for a, img in zip(FRAMES_AT, video_frames(r.content)):
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=92)
            got.append({"kind": "frame", "at": a, "fetched": to_fetched(buf.getvalue())})
    return got


def collect(sess, reg: registry.Registry, run: str, key: str, secret: str, embedder=None, scorer=None,
            budget_s: float = 60 * 60, max_requests: int = REQUESTS_PER_RUN, today: date | None = None,
            clock=time.monotonic, sleep=time.sleep) -> dict:
    from .embed import VectorStore
    P = paths()
    today = today or datetime.now(timezone.utc).date()
    st = store.read_state(P["state"])
    st.setdefault("advertisers", {})
    cl = Client(sess, key, secret, max_requests, sleep=sleep)
    cl.country_filter = st.get("country_filter")
    t0 = clock()
    counts = {"houses": 0, "ads_found": 0, "new_ads": 0, "updated_ads": 0, "pictures": 0, "read_ok": 0, "invalid": 0,
              "media_errors": 0}
    problems: dict[str, str] = {}
    stopped = None
    vectors = VectorStore(embedder.tag, root=P["vectors"]) if embedder is not None else None
    obs_path = P["obs"] / f"{scorer.rubric.version}.jsonl" if scorer is not None else None
    scored = {(r["sha"], r["instrument"]) for r in store.read_jsonl(obs_path)} if obs_path else set()
    new_obs: list[dict] = []
    pending: list[tuple] = []

    def flush():
        if not pending:
            return
        answers = score_batch(scorer, [jpeg_for_model(f.image) for f, _ in pending])
        for (f, ob), ans in zip(pending, answers):
            ob["scored_at"] = store.utc_now()
            if isinstance(ans, ScoreError):
                ob["status"], ob["error"] = "invalid", str(ans)[:300]
                counts["invalid"] += 1
            else:
                ob["status"], ob["output"] = "ok", ans
                counts["read_ok"] += 1
            new_obs.append(ob)
            scored.add((f.sha, scorer.instrument))
            f.image.close()
        pending.clear()

    try:
        for h in reg.houses:
            if clock() - t0 > budget_s:
                stopped = "budget"
                break
            rec = st["advertisers"].get(h.id) or {}
            if not rec.get("checked") or (today - date.fromisoformat(rec["checked"])).days >= ADVERTISER_REFRESH_DAYS:
                try:
                    keep, refused = advertisers(cl, h)
                except RuntimeError as e:
                    problems[h.id] = f"advertisers: {str(e)[:200]}"
                    continue
                rec = {"checked": today.isoformat(), "keep": keep, "refused": refused[:20]}
                st["advertisers"][h.id] = rec
            ids = {a["business_id"] for a in rec.get("keep") or []}
            path = P["ads"] / f"{h.id}.jsonl"
            ads = {r["ad_id"]: r for r in store.read_jsonl(path)}
            try:
                for item in search_ads(cl, h, today - timedelta(days=LOOKBACK_DAYS), today):
                    adv = item.get("advertiser") or {}
                    bid = str(adv.get("business_id", adv.get("buisness_id")))
                    if not (bid in ids or (not ids and is_house(h, adv.get("business_name") or ""))):
                        continue
                    counts["ads_found"] += 1
                    row = ad_row(item, h.id, run)
                    if row["ad_id"] in ads:
                        ads[row["ad_id"]] = {**ads[row["ad_id"]], **{k: v for k, v in row.items() if v is not None}}
                        counts["updated_ads"] += 1
                        continue
                    row["found"] = today.isoformat()
                    try:
                        media = media_for(sess, item)
                    except (requests.RequestException, MediaError) as e:
                        media, row["media_error"] = [], f"{e.__class__.__name__}: {str(e)[:120]}"
                        counts["media_errors"] += 1
                    row["media"] = []
                    for m in media:
                        f = m["fetched"]
                        counts["pictures"] += 1
                        row["media"].append({"kind": m["kind"], **({"at": m["at"]} if "at" in m else {}),
                                             "sha": f.sha, "phash": f.phash, "w": f.w, "h": f.h})
                        if vectors is not None and f.sha not in vectors:
                            vectors.add(f.sha, embedder.embed(f.image), (row["first_shown"] or today.isoformat())[:7])
                        if scorer is not None and (f.sha, scorer.instrument) not in scored \
                                and all(f.sha != g.sha for g, _ in pending):
                            pending.append((f, {"sha": f.sha, "instrument": scorer.instrument, "ad_id": row["ad_id"],
                                                "house_id": h.id, "kind": m["kind"], "rubric_version": scorer.rubric.version,
                                                "run_id": run}))
                            if len(pending) >= BATCH:
                                flush()
                        else:
                            f.image.close()
                    ads[row["ad_id"]] = row
                    counts["new_ads"] += 1
            except RuntimeError as e:
                if isinstance(e, Stop):
                    raise
                problems[h.id] = f"ads: {str(e)[:200]}"
            finally:
                store.write_jsonl(path, sorted(ads.values(), key=lambda r: (r.get("first_shown") or "", r["ad_id"]),
                                               reverse=True))
            counts["houses"] += 1
        flush()
    except Stop as e:
        stopped = str(e)
        try:
            flush()
        except ScoreError as e2:
            stopped += f"; reader: {str(e2)[:120]}"
    except ScoreError as e:
        stopped = f"reader: {str(e)[:200]}"
    if obs_path is not None:
        store.append_jsonl(obs_path, new_obs)
    if vectors is not None:
        vectors.save()
    st.update({"run": run, "updated_at": store.utc_now(), "problems": problems, "stopped": stopped,
               "requests": cl.requests, "country_filter": cl.country_filter})
    store.write_state(P["state"], st)
    out = {"run": run, "updated_at": st["updated_at"], **counts, "requests": cl.requests, "problems": problems,
           "stopped": stopped}
    store.append_jsonl(P["prov"], [out])
    return out


def probe(run: str) -> list[str]:
    st = store.read_state(paths()["state"])
    if st.get("run") != run:
        return [f"no tiktok run {run} on file"]
    errs = [f"{h}: {why}" for h, why in sorted((st.get("problems") or {}).items())]
    none = sorted(h for h, r in (st.get("advertisers") or {}).items() if not r.get("keep"))
    if none:
        errs.append(f"no advertiser account matched for: {', '.join(none)}")
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.tiktok")
    ap.add_argument("stage", choices=["collect", "probe"])
    ap.add_argument("--run", default=config.run_id())
    ap.add_argument("--budget-min", type=float, default=60)
    ap.add_argument("--max-requests", type=int, default=REQUESTS_PER_RUN)
    ap.add_argument("--no-reader", action="store_true")
    a = ap.parse_args(argv)
    if a.stage == "probe":
        for e in probe(a.run):
            print(f"::warning::{e}")
        return 0
    key, secret = os.environ.get("TIKTOK_CLIENT_KEY", "").strip(), os.environ.get("TIKTOK_CLIENT_SECRET", "").strip()
    if not (key and secret):
        store.append_jsonl(paths()["prov"], [{"run": a.run, "updated_at": store.utc_now(),
                                             "waiting": "no TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET"}])
        print("::notice::tiktok: no approved Commercial Content API credentials yet; nothing collected")
        return 0
    embedder = scorer = None
    if not a.no_reader:
        try:
            from .embed import OpenClipEmbedder
            from .score import load_rubric, make_scorer
            embedder, scorer = OpenClipEmbedder(), make_scorer(load_rubric())
        except Exception as e:
            print(f"::warning::tiktok: reader unavailable, ads only: {e.__class__.__name__}: {str(e)[:200]}")
    try:
        out = collect(session(), registry.load(), a.run, key, secret, embedder, scorer, a.budget_min * 60, a.max_requests)
    except Exception as e:
        out = {"run": a.run, "updated_at": store.utc_now(), "crashed": f"{e.__class__.__name__}: {str(e)[:300]}"}
        store.append_jsonl(paths()["prov"], [out])
        print(f"::error::tiktok crashed, recorded in provenance: {out['crashed']}")
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
