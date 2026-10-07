"""YouTube: every film each house has published on its own channel, with its views, and the image
the house chose to stand for it, read by the reader.

    python -m adtone.youtube collect --run <id> [--budget-min 60] [--read-thumbs 300]
    python -m adtone.youtube probe --run <id>

Needs YOUTUBE_API_KEY: a free key from Google Cloud (enable "YouTube Data API v3", create an API
key). Without one the run records that it waited and stops. The API allows 10,000 units a day and
every call made here costs one: a channel's whole upload list is one unit per fifty films and their
statistics one more, so the first full pass over all 28 channels takes a few thousand units and a
weekly refresh a few hundred.

Channels come from reference/youtube_channels.csv: one official channel per house, found from the
house's own site where it links one, by search otherwise (the file says which). Each run checks every
channel's title against the house, so a wrong channel shows up in the probe rather than in the data.

Kept, per film: id, publication date, title, the first part of the description (where the house
credits the director, photographer and cast), duration, and the latest views, likes and comments.
Films published in the last 120 days also get a dated row of their counts each run, which gives
each film's early curve. Per channel, a dated row of subscribers, total views and film count.

The thumbnail is the frame or poster the house chose to represent the film: the same kind of
choice as a homepage image. Up to --read-thumbs new thumbnails a run go through the reader and the
fingerprint, newest films first; the image is held in memory only.
"""
from __future__ import annotations

import argparse
import csv
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

API = "https://www.googleapis.com/youtube/v3/{method}"
CHANNELS_FILE = config.ROOT / "reference" / "youtube_channels.csv"
UA = "fashion-adtone research (films by house; contact via the repository)"
QUOTA_PER_RUN = 9000          # units; the daily allowance is 10,000 and other runs may share the key
TRACK_DAYS = 120              # films this young get a dated row of their counts each run
DESC_CHARS = 1500
BATCH = 16
THUMB_ORDER = ("maxres", "standard", "high", "medium", "default")


class QuotaSpent(RuntimeError):
    """The key's daily allowance is used up, or this run's share of it."""


class KeyRefused(RuntimeError):
    """The key is missing, invalid, or not enabled for the YouTube Data API."""


def paths() -> dict[str, Path]:
    b = config.DATA / "youtube"
    return {"dir": b, "videos": b / "videos", "channels": b / "channels.jsonl", "stats": b / "stats.jsonl",
            "obs": b / "obs", "vectors": b / "vectors",
            "state": config.STATE_DIR / "youtube.json", "prov": config.PROV_DIR / "youtube.jsonl"}


def channels(path: Path | None = None) -> dict[str, dict]:
    with (path or CHANNELS_FILE).open(encoding="utf-8") as f:
        return {r["house"]: r for r in csv.DictReader(f) if r.get("channel_id")}


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s


class Client:
    """The Data API with its quota counted: one unit per list call."""

    def __init__(self, sess, key: str, budget: int = QUOTA_PER_RUN, sleep=time.sleep):
        self.s, self.key, self.budget, self.sleep = sess, key, budget, sleep
        self.units = 0

    def get(self, method: str, **params) -> dict:
        if self.units >= self.budget:
            raise QuotaSpent(f"this run's {self.budget} units are used")
        for attempt in range(4):
            self.units += 1
            r = self.s.get(API.format(method=method), params={**params, "key": self.key}, timeout=60)
            if r.status_code == 200:
                return r.json()
            reason = _reason(r)
            if r.status_code == 403 and reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
                if reason == "rateLimitExceeded" and attempt < 3:
                    self.sleep(10.0 * 2 ** attempt)
                    continue
                raise QuotaSpent(f"YouTube says {reason}")
            if r.status_code in (400, 403) and reason in ("keyInvalid", "accessNotConfigured", "forbidden",
                                                           "ipRefererBlocked", "keyExpired", "API_KEY_INVALID"):
                raise KeyRefused(f"YouTube refused the key: {reason}")
            if r.status_code >= 500 and attempt < 3:
                self.sleep(5.0 * 2 ** attempt)
                continue
            raise RuntimeError(f"YouTube HTTP {r.status_code} {reason}")
        raise RuntimeError("YouTube kept failing")


def _reason(r) -> str:
    try:
        err = r.json().get("error", {})
        errs = err.get("errors") or [{}]
        return errs[0].get("reason") or err.get("status") or ""
    except (ValueError, AttributeError):
        return ""


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def title_matches(house: registry.House, title: str) -> bool:
    """The channel's title names the house (accents, case, spacing and '&' ignored)."""
    t = _norm(title)
    names = [house.name] + list(house.search_terms or [])
    return any(_norm(n) and (_norm(n) in t or t in _norm(n)) for n in names)


def seconds(iso: str | None) -> int | None:
    """ISO 8601 duration (PT1H2M3S) in seconds."""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m or not iso:
        return None
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def best_thumb(thumbs: dict) -> str | None:
    for k in THUMB_ORDER:
        if (thumbs or {}).get(k, {}).get("url"):
            return thumbs[k]["url"]
    return None


def unletterbox(img):
    """Thumbnails below the top size are 4:3 with black bands around a 16:9 film: cut the bands off."""
    from PIL import ImageStat
    w, h = img.size
    if abs(w / h - 4 / 3) > 0.02:
        return img
    band = round((h - w * 9 / 16) / 2)
    if band < 4:
        return img
    top = ImageStat.Stat(img.convert("L").crop((0, 0, w, band))).mean[0]
    bottom = ImageStat.Stat(img.convert("L").crop((0, h - band, w, h))).mean[0]
    return img.crop((0, band, w, h - band)) if max(top, bottom) < 16 else img


def _int(x) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def video_row(item: dict, house: str) -> dict:
    sn, cd, st = item.get("snippet") or {}, item.get("contentDetails") or {}, item.get("statistics") or {}
    return {"video_id": item["id"], "house_id": house, "published": (sn.get("publishedAt") or "")[:10],
            "title": sn.get("title"), "description": (sn.get("description") or "")[:DESC_CHARS],
            "duration_s": seconds(cd.get("duration")), "thumb": best_thumb(sn.get("thumbnails") or {}),
            "views": _int(st.get("viewCount")), "likes": _int(st.get("likeCount")),
            "comments": _int(st.get("commentCount")), "language": sn.get("defaultAudioLanguage") or sn.get("defaultLanguage")}


def upload_ids(cl: Client, playlist: str, known: set[str], full: bool) -> tuple[list[str], bool]:
    """Film ids from the channel's uploads, newest first. A refresh stops at the first page holding only
    films already on file; a first pass reads to the end. Returns the ids and whether the end was reached."""
    ids, token = [], None
    while True:
        params = {"part": "contentDetails", "playlistId": playlist, "maxResults": 50}
        if token:
            params["pageToken"] = token
        page = cl.get("playlistItems", **params)
        got = [it["contentDetails"]["videoId"] for it in page.get("items", []) if it.get("contentDetails", {}).get("videoId")]
        ids += [v for v in got if v not in ids]
        token = page.get("nextPageToken")
        if not token:
            return ids, True
        if not full and got and all(v in known for v in got):
            return ids, False


def collect(sess, reg: registry.Registry, run: str, key: str, embedder=None, scorer=None, read_thumbs: int = 300,
            budget_s: float = 60 * 60, today: date | None = None, clock=time.monotonic, sleep=time.sleep) -> dict:
    P = paths()
    today = today or datetime.now(timezone.utc).date()
    st = store.read_state(P["state"])
    st.setdefault("houses", {})
    cl = Client(sess, key, sleep=sleep)
    houses = {h.id: h for h in reg.houses}
    chans = {h: r for h, r in channels().items() if h in houses}
    t0 = clock()
    counts = {"channels": 0, "new_films": 0, "films_refreshed": 0, "tracked": 0, "thumbs_read": 0, "invalid": 0}
    problems: dict[str, str] = {}
    stopped = None
    try:
        # 1. the channels: identity check and a dated row of their totals
        ids = [r["channel_id"] for r in chans.values()]
        found = {}
        for i in range(0, len(ids), 50):
            res = cl.get("channels", part="snippet,contentDetails,statistics", id=",".join(ids[i:i + 50]), maxResults=50)
            found.update({it["id"]: it for it in res.get("items", [])})
        rows = []
        for h, r in sorted(chans.items()):
            it = found.get(r["channel_id"])
            if not it:
                problems[h] = f"channel {r['channel_id']} not found"
                continue
            title = it.get("snippet", {}).get("title", "")
            if not title_matches(houses[h], title):
                problems[h] = f"channel title {title!r} does not name the house"
            s = it.get("statistics") or {}
            rows.append({"date": today.isoformat(), "house_id": h, "channel_id": r["channel_id"], "title": title,
                         "subscribers": _int(s.get("subscriberCount")), "views": _int(s.get("viewCount")),
                         "films": _int(s.get("videoCount")), "run": run})
            st["houses"].setdefault(h, {})["uploads"] = it.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
            counts["channels"] += 1
        store.append_jsonl(P["channels"], rows)

        # 2. each channel's films: new ones found, every film's counts refreshed
        for h in sorted(chans):
            if clock() - t0 > budget_s:
                stopped = "budget"
                break
            hs = st["houses"][h] if h in st["houses"] else {}
            if not hs.get("uploads") or h in problems and "not found" in problems[h]:
                continue
            path = P["videos"] / f"{h}.jsonl"
            films = {r["video_id"]: r for r in store.read_jsonl(path)}
            try:
                new_ids, reached_end = upload_ids(cl, hs["uploads"], set(films), full=not hs.get("complete"))
            except RuntimeError as e:
                problems[h] = str(e)[:200]
                continue
            want = list(dict.fromkeys(new_ids + list(films)))
            try:
                for i in range(0, len(want), 50):   # filed batch by batch, so a spent quota keeps what came back
                    res = cl.get("videos", part="snippet,contentDetails,statistics", id=",".join(want[i:i + 50]),
                                 maxResults=50)
                    for it in res.get("items", []):
                        row = video_row(it, h)
                        if it["id"] not in films:
                            counts["new_films"] += 1
                        else:
                            counts["films_refreshed"] += 1
                            row = {**films[it["id"]], **{k: v for k, v in row.items() if v is not None}}
                        row["stats_at"] = today.isoformat()
                        films[it["id"]] = row
                hs["complete"] = hs.get("complete") or reached_end
            finally:
                store.write_jsonl(path, sorted(films.values(), key=lambda r: (r.get("published") or "", r["video_id"]),
                                               reverse=True))
                hs.update({"films": len(films), "refreshed": today.isoformat()})
                st["houses"][h] = hs
            young = [r for r in films.values() if r.get("published") and r.get("stats_at") == today.isoformat()
                     and (today - date.fromisoformat(r["published"])).days <= TRACK_DAYS]
            store.append_jsonl(P["stats"], [{"date": today.isoformat(), "video_id": r["video_id"], "house_id": h,
                                             "views": r.get("views"), "likes": r.get("likes"),
                                             "comments": r.get("comments")} for r in young])
            counts["tracked"] += len(young)
    except QuotaSpent as e:
        stopped = f"quota: {e}"
    except KeyRefused:
        raise

    # 3. thumbnails through the reader, newest films first
    if embedder is not None and scorer is not None and read_thumbs > 0 and not (stopped or "").startswith("budget"):
        counts.update(read_thumbnails(sess, embedder, scorer, run, read_thumbs, budget_s - (clock() - t0), clock))
    st.update({"run": run, "updated_at": store.utc_now(), "problems": problems, "units": cl.units, "stopped": stopped})
    store.write_state(P["state"], st)
    out = {"run": run, "updated_at": st["updated_at"], **counts, "units": cl.units, "problems": problems,
           "stopped": stopped}
    store.append_jsonl(P["prov"], [out])
    return out


def read_thumbnails(sess, embedder, scorer, run: str, limit: int, budget_s: float, clock=time.monotonic) -> dict:
    from .embed import VectorStore
    P = paths()
    obs_path = P["obs"] / f"{scorer.rubric.version}.jsonl"
    done = {r["video_id"] for r in store.read_jsonl(obs_path) if r.get("instrument") == scorer.instrument}
    vectors = VectorStore(embedder.tag, root=P["vectors"])
    films = [r for p in sorted(P["videos"].glob("*.jsonl")) for r in store.read_jsonl(p)
             if r.get("thumb") and r["video_id"] not in done]
    films.sort(key=lambda r: r.get("published") or "", reverse=True)
    t0, counts, pending, new_obs = clock(), {"thumbs_read": 0, "invalid": 0, "thumb_errors": 0}, [], []

    def flush():
        if not pending:
            return
        answers = score_batch(scorer, [jpeg_for_model(f.image) for f, _ in pending])
        for (f, film), ans in zip(pending, answers):
            ob = {"video_id": film["video_id"], "house_id": film["house_id"], "published": film.get("published"),
                  "sha": f.sha, "instrument": scorer.instrument, "rubric_version": scorer.rubric.version,
                  "run_id": run, "scored_at": store.utc_now()}
            if isinstance(ans, ScoreError):
                ob["status"], ob["error"] = "invalid", str(ans)[:300]
                counts["invalid"] += 1
            else:
                ob["status"], ob["output"] = "ok", ans
                counts["thumbs_read"] += 1
            new_obs.append(ob)
            f.image.close()
        pending.clear()

    try:
        for film in films[:limit]:
            if clock() - t0 > budget_s:
                break
            try:
                r = sess.get(film["thumb"], timeout=30)
                if r.status_code != 200:
                    counts["thumb_errors"] += 1
                    continue
                f = to_fetched(r.content)
                img = unletterbox(f.image)
                if img is not f.image:
                    f.image = img
            except (requests.RequestException, MediaError):
                counts["thumb_errors"] += 1
                continue
            if f.sha not in vectors:
                vectors.add(f.sha, embedder.embed(f.image), (film.get("published") or "0000")[:4])
            pending.append((f, film))
            if len(pending) >= BATCH:
                flush()
        flush()
    except ScoreError as e:
        counts["stopped_on"] = str(e)[:200]
    store.append_jsonl(obs_path, new_obs)
    vectors.save()
    return counts


def probe(run: str) -> list[str]:
    st = store.read_state(paths()["state"])
    if st.get("run") != run:
        return [f"no youtube run {run} on file"]
    errs = [f"{h}: {why}" for h, why in sorted((st.get("problems") or {}).items())]
    if not any((v or {}).get("films") for v in (st.get("houses") or {}).values()):
        errs.append("no films on file for any house")
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.youtube")
    ap.add_argument("stage", choices=["collect", "probe"])
    ap.add_argument("--run", default=config.run_id())
    ap.add_argument("--budget-min", type=float, default=60)
    ap.add_argument("--read-thumbs", type=int, default=300)
    a = ap.parse_args(argv)
    if a.stage == "probe":
        errs = probe(a.run)
        for e in errs:
            print(f"::warning::{e}")
        return 0
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key:
        store.append_jsonl(paths()["prov"], [{"run": a.run, "updated_at": store.utc_now(), "waiting": "no YOUTUBE_API_KEY"}])
        print("::notice::youtube: no YOUTUBE_API_KEY secret yet; nothing collected")
        return 0
    embedder = scorer = None
    if a.read_thumbs > 0:
        try:
            from .embed import OpenClipEmbedder
            from .score import load_rubric, make_scorer
            embedder, scorer = OpenClipEmbedder(), make_scorer(load_rubric())
        except Exception as e:   # the films are worth having without the reader
            print(f"::warning::youtube: reader unavailable, films only: {e.__class__.__name__}: {str(e)[:200]}")
    try:
        out = collect(session(), registry.load(), a.run, key, embedder, scorer, a.read_thumbs, a.budget_min * 60)
    except Exception as e:
        out = {"run": a.run, "updated_at": store.utc_now(), "crashed": f"{e.__class__.__name__}: {str(e)[:300]}"}
        store.append_jsonl(paths()["prov"], [out])
        print(f"::error::youtube crashed, recorded in provenance: {out['crashed']}")
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
