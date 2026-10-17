"""One collection loop for every ad library read the same way as TikTok's: find the house's own advertiser
accounts, search its ads for the past year, keep the ones that are the house's, fetch each new ad's pictures
in memory, read them with the reader and the fingerprint, and store one row per ad with no copy text.

A source is a small object (see snap.py, msads.py, pinterest.py, metaweb.py, linkedin.py, xads.py) with:

    name                        folder under data/ and the name of its state and provenance files
    lookback_days               how far back the library is searched (a year for the DSA repositories)
    advertiser_refresh_days     how often a house's advertiser accounts are matched again
    advertisers(house)          -> (keep, refused): the house's own accounts and the near misses, each
                                   {"id", "name", ...}; an empty keep means search by name and judge each ad
    search_ads(house, keep, start, end)   yields raw ad records
    is_houses(item, house, keep)          whether a raw record is the house's own ad
    ad_row(item, house_id, run)           -> the stored row: ad_id, house_id, advertiser, first_shown, last_shown,
                                             reach, and whatever the library gives; never the ad's text
    media_for(item)             -> [{"kind": "image"|"frame", "at"?: float, "fetched": FetchedImage}]
    media_urls(item)            optional -> (urls, fetch keywords): the addresses behind media_for, so a run
                                   without the reader can keep them aside and the read stage fetch them later
    Stop                        exception class: the run's budget or the library's limit is reached, keep what came
    close()                     optional

Pictures are never written to disk. Rows and readings go to data/<name>/, the state to data/state/<name>.json,
and every run to data/provenance/<name>.jsonl, so a run that found nothing still says so.

A run without the reader keeps each new ad's picture addresses in data/<name>/pending/<house>.jsonl instead of
fetching them, and the ad's row says media_pending; the read stage (python -m adtone.<name> read) fetches and
reads them later, in its own budget, and the row gets its pictures as if they had been read at collection.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import os
import requests

from . import config, registry, store
from .media import MediaError, jpeg_for_model
from .score import ScoreError, score_batches

BATCH = 16                                              # pictures per call to the reader
PARALLEL = int(os.environ.get("ADTONE_READ_PARALLEL") or 6)   # calls in flight at once, each its own container


def paths(name: str) -> dict[str, Path]:
    b = config.DATA / name
    return {"ads": b / "ads", "obs": b / "obs", "vectors": b / "vectors", "pending": b / "pending",
            "state": config.STATE_DIR / f"{name}.json", "prov": config.PROV_DIR / f"{name}.jsonl"}


def merge_shapes(a, b):
    """Two shapes as one: every key either has, a type that varies shown as both."""
    if isinstance(a, dict) and isinstance(b, dict):
        return {k: merge_shapes(a.get(k), b.get(k)) if k in a and k in b else (a.get(k) if k in a else b.get(k)) for k in list(a) + [k for k in b if k not in a]}
    if isinstance(a, list) and isinstance(b, list):
        return [merge_shapes(a[0], b[0])] if a and b else (a or b)
    if a == b or b is None:
        return a
    if a is None or a == "NoneType":
        return b
    if b == "NoneType":
        return a
    return a if isinstance(a, str) and isinstance(b, str) and b in a else (f"{a}|{b}" if isinstance(a, str) and isinstance(b, str) else a)


def shape(obj, depth: int = 0, max_depth: int = 6):
    """The keys and types of a record, without its values: what a library actually returns, kept in the
    state after the first live run so the row can be tuned to it without storing any ad's text."""
    if depth > max_depth:
        return "..."
    if isinstance(obj, dict):
        return {str(k): shape(v, depth + 1, max_depth) for k, v in list(obj.items())[:60]}
    if isinstance(obj, list):
        return [shape(obj[0], depth + 1, max_depth)] if obj else []
    return type(obj).__name__


def fetch_media(sess, urls: list[str], max_images: int = 5, max_videos: int = 2, max_video_bytes: int = 60_000_000) -> list[dict]:
    """The pictures at the addresses, and three frames of each video, in memory: [{"kind", "at"?, "fetched"}].
    An address that is not a picture or a video is skipped."""
    import io

    from .media import MediaError, to_fetched
    from .tiktok import FRAMES_AT, video_frames

    got, images, videos, shas = [], 0, 0, set()
    for u in dict.fromkeys(urls):
        if images >= max_images and videos >= max_videos:
            break
        r = sess.get(u, timeout=90)
        if r.status_code != 200 or not r.content:
            continue
        ctype = ((getattr(r, "headers", None) or {}).get("Content-Type") or "").lower()
        if ctype.startswith("video") or u.split("?")[0].lower().endswith((".mp4", ".mov", ".webm", ".m4v")):
            if videos >= max_videos or len(r.content) > max_video_bytes:
                continue
            videos += 1
            for a, img in zip(FRAMES_AT, video_frames(r.content)):
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=92)
                got.append({"kind": "frame", "at": a, "fetched": to_fetched(buf.getvalue())})
        elif images < max_images:
            try:
                f = to_fetched(r.content)
            except MediaError:
                continue
            if f.sha in shas:       # the same picture at a second address (a resized copy, say)
                f.image.close()
                continue
            shas.add(f.sha)
            got.append({"kind": "image", "fetched": f})
            images += 1
    return got


class _Reading:
    """One rubric's reader: its observations file, what it has read, and the pictures queued for it."""

    def __init__(self, scorer, P: dict):
        self.scorer = scorer
        self.obs_path = P["obs"] / f"{scorer.rubric.version}.jsonl"
        self.scored = {(r["sha"], r["instrument"]) for r in store.read_jsonl(self.obs_path)}
        self.pending: list[tuple] = []
        self.new_obs: list[dict] = []


class Taker:
    """The pictures of one ad, taken into the row: fingerprinted into the vectors, and queued for each
    reader (the eighteen questions, and the clothes), which are called in parallel batches. Shared by
    collection and by the read stage. `scorer` may be one scorer or a list of them."""

    def __init__(self, P: dict, run: str, embedder=None, scorer=None, counts: dict | None = None):
        from .embed import VectorStore
        self.run, self.embedder = run, embedder
        self.counts = counts if counts is not None else {"pictures": 0, "read_ok": 0, "invalid": 0}
        self.vectors = VectorStore(embedder.tag, root=P["vectors"]) if embedder is not None else None
        scorers = [] if scorer is None else (list(scorer) if isinstance(scorer, (list, tuple)) else [scorer])
        self.readings = [_Reading(sc, P) for sc in scorers]
        self.holds: dict[str, int] = {}      # sha -> readers still to take the picture, before it is closed

    @property
    def scorer(self):
        return self.readings[0].scorer if self.readings else None

    def take(self, row: dict, media: list[dict], house_id: str, today: date) -> None:
        row["media"] = []
        for m in media:
            f = m["fetched"]
            self.counts["pictures"] += 1
            row["media"].append({"kind": m["kind"], **({"at": m["at"]} if "at" in m else {}),
                                 "sha": f.sha, "phash": f.phash, "w": f.w, "h": f.h})
            if self.vectors is not None and f.sha not in self.vectors:
                self.vectors.add(f.sha, self.embedder.embed(f.image), (row.get("first_shown") or today.isoformat())[:7])
            queued = 0
            for rd in self.readings:
                sc = rd.scorer
                if (f.sha, sc.instrument) in rd.scored or any(f.sha == g.sha for g, _ in rd.pending):
                    continue
                rd.pending.append((f, {"sha": f.sha, "instrument": sc.instrument, "ad_id": row["ad_id"],
                                       "house_id": house_id, "kind": m["kind"], "rubric_version": sc.rubric.version,
                                       "run_id": self.run}))
                queued += 1
            if queued:
                self.holds[f.sha] = self.holds.get(f.sha, 0) + queued
                for rd in self.readings:
                    if len(rd.pending) >= BATCH * PARALLEL:
                        self._flush(rd)
            else:
                f.image.close()

    def _flush(self, rd: _Reading) -> None:
        if not rd.pending:
            return
        jpegs = [jpeg_for_model(f.image) for f, _ in rd.pending]
        answers = score_batches(rd.scorer, [jpegs[i:i + BATCH] for i in range(0, len(jpegs), BATCH)])
        for (f, ob), ans in zip(rd.pending, answers):
            ob["scored_at"] = store.utc_now()
            if isinstance(ans, ScoreError):
                ob["status"], ob["error"] = "invalid", str(ans)[:300]
                self.counts["invalid"] += 1
            else:
                ob["status"], ob["output"] = "ok", ans
                self.counts["read_ok"] += 1
                k = f"ok_{rd.scorer.rubric.version}"
                self.counts[k] = self.counts.get(k, 0) + 1
            rd.new_obs.append(ob)
            rd.scored.add((f.sha, rd.scorer.instrument))
            left = self.holds.get(f.sha, 1) - 1
            if left <= 0:
                self.holds.pop(f.sha, None)
                f.image.close()
            else:
                self.holds[f.sha] = left
        rd.pending.clear()

    def flush(self) -> None:
        for rd in self.readings:
            self._flush(rd)

    def save(self) -> None:
        for rd in self.readings:
            if rd.new_obs:
                store.append_jsonl(rd.obs_path, rd.new_obs)
                rd.new_obs = []
        if self.vectors is not None:
            self.vectors.save()


def collect(src, reg: registry.Registry, run: str, embedder=None, scorer=None, budget_s: float = 60 * 60,
            today: date | None = None, clock=time.monotonic) -> dict:
    P = paths(src.name)
    today = today or datetime.now(timezone.utc).date()
    st = store.read_state(P["state"])
    st.setdefault("advertisers", {})
    t0 = clock()
    counts = {"houses": 0, "ads_found": 0, "new_ads": 0, "updated_ads": 0, "pictures": 0, "read_ok": 0, "invalid": 0,
              "media_errors": 0, "deferred": 0}
    problems: dict[str, str] = {}
    stopped = None
    taker = Taker(P, run, embedder, scorer, counts)
    flush = taker.flush
    # without the reader, a source that can name its picture addresses keeps them for the read stage
    defer = embedder is None and not taker.readings and hasattr(src, "media_urls")
    sample_shape, shaped = None, 0      # the shape is taken afresh each run

    st.setdefault("searched", {})        # house -> the day its year was last searched to the end
    skip_days = getattr(src, "house_refresh_days", 6)
    try:
        for h in reg.houses:
            if clock() - t0 > budget_s:
                stopped = "budget"
                break
            done = st["searched"].get(h.id)
            if done and (today - date.fromisoformat(done)).days < skip_days:
                continue                  # searched to the end this week already: a run cut short picks up the rest
            rec = st["advertisers"].get(h.id) or {}
            if not rec.get("checked") or (today - date.fromisoformat(rec["checked"])).days >= src.advertiser_refresh_days:
                try:
                    keep, refused = src.advertisers(h)
                except src.Stop:
                    raise
                except Exception as e:   # one house's lookup failing must not end the run; it is recorded
                    problems[h.id] = f"advertisers: {e.__class__.__name__}: {str(e)[:200]}"
                    continue
                rec = {"checked": today.isoformat(), "keep": keep, "refused": refused[:20]}
                st["advertisers"][h.id] = rec
            keep = rec.get("keep") or []
            path = P["ads"] / f"{h.id}.jsonl"
            ads = {r["ad_id"]: r for r in store.read_jsonl(path)}
            if hasattr(src, "row_is_kept"):
                before = len(ads)
                ads = {k: r for k, r in ads.items() if src.row_is_kept(r, keep)}
                if len(ads) != before:
                    counts["dropped"] = counts.get("dropped", 0) + before - len(ads)
                    store.write_jsonl(path, sorted(ads.values(), key=lambda r: (r.get("first_shown") or "", r["ad_id"]), reverse=True))
            pend_path = P["pending"] / f"{h.id}.jsonl"
            pend = {r["ad_id"]: r for r in store.read_jsonl(pend_path)} if defer else {}
            try:
                for item in src.search_ads(h, keep, today - timedelta(days=src.lookback_days), today):
                    if clock() - t0 > budget_s:
                        raise src.Stop("budget")      # mid-search: what came back is kept, the rest waits for next week
                    if sample_shape is None:
                        sample_shape, shaped = shape(item), 1
                    elif shaped < 60:     # the first sixty records' keys together: a field one record lacks, another has
                        sample_shape, shaped = merge_shapes(sample_shape, shape(item)), shaped + 1
                    if not src.is_houses(item, h, keep):
                        continue
                    counts["ads_found"] += 1
                    row = src.ad_row(item, h.id, run)
                    if not row.get("ad_id"):
                        continue
                    if row["ad_id"] in ads:
                        ads[row["ad_id"]] = {**ads[row["ad_id"]], **{k: v for k, v in row.items() if v is not None}}
                        counts["updated_ads"] += 1
                        continue
                    row["found"] = today.isoformat()
                    if defer:
                        urls, kw = src.media_urls(item)
                        row["media"], row["media_pending"] = [], bool(urls)
                        if urls:
                            pend[row["ad_id"]] = {"ad_id": row["ad_id"], "house_id": h.id, "urls": list(urls), "kw": dict(kw),
                                                  "first_shown": row.get("first_shown"), "found": row["found"]}
                            counts["deferred"] += 1
                    else:
                        try:
                            media = src.media_for(item)
                        except (requests.RequestException, MediaError, OSError, ValueError) as e:
                            media, row["media_error"] = [], f"{e.__class__.__name__}: {str(e)[:120]}"
                            counts["media_errors"] += 1
                        taker.take(row, media, h.id, today)
                    ads[row["ad_id"]] = row
                    counts["new_ads"] += 1
                st["searched"][h.id] = today.isoformat()
            except src.Stop:
                raise
            except Exception as e:
                problems[h.id] = f"ads: {e.__class__.__name__}: {str(e)[:200]}"
            finally:
                store.write_jsonl(path, sorted(ads.values(), key=lambda r: (r.get("first_shown") or "", r["ad_id"]),
                                               reverse=True))
                if pend:
                    store.write_jsonl(pend_path, sorted(pend.values(), key=lambda r: r["ad_id"]))
            counts["houses"] += 1
        flush()
    except src.Stop as e:
        stopped = str(e)
        try:
            flush()
        except ScoreError as e2:
            stopped += f"; reader: {str(e2)[:120]}"
    except ScoreError as e:
        stopped = f"reader: {str(e)[:200]}"
    finally:
        if hasattr(src, "close"):
            try:
                src.close()
            except Exception:
                pass
    taker.save()
    st.update({"run": run, "updated_at": store.utc_now(), "problems": problems, "stopped": stopped,
               "requests": getattr(src, "requests", None), "shape": sample_shape, **getattr(src, "state_extra", lambda: {})()})
    store.write_state(P["state"], st)
    out = {"run": run, "updated_at": st["updated_at"], **counts, "requests": getattr(src, "requests", None),
           "problems": problems, "stopped": stopped}
    store.append_jsonl(P["prov"], [out])
    return out


def read_pending(name: str, reg: registry.Registry, run: str, embedder, scorer, budget_s: float = 60 * 60,
                 sess=None, today: date | None = None, clock=time.monotonic) -> dict:
    """The pictures kept aside by runs without the reader: fetched, fingerprinted and read, house by house,
    each ad's row completed as it goes; what the budget leaves waits for the next run."""
    P = paths(name)
    today = today or datetime.now(timezone.utc).date()
    sess = sess or requests.Session()
    sess.headers.setdefault("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36")
    t0 = clock()
    counts = {"houses": 0, "ads_read": 0, "ads_left": 0, "pictures": 0, "read_ok": 0, "invalid": 0, "media_errors": 0}
    taker = Taker(P, run, embedder, scorer, counts)
    stopped = None
    try:
        for h in reg.houses:
            pend_path = P["pending"] / f"{h.id}.jsonl"
            pend = {r["ad_id"]: r for r in store.read_jsonl(pend_path)}
            if not pend:
                continue
            if clock() - t0 > budget_s:
                stopped = "budget"
                counts["ads_left"] += len(pend)
                continue
            path = P["ads"] / f"{h.id}.jsonl"
            ads = {r["ad_id"]: r for r in store.read_jsonl(path)}
            done = []
            try:
                for ad_id, pr in sorted(pend.items(), key=lambda kv: (kv[1].get("first_shown") or "", kv[0]), reverse=True):
                    if clock() - t0 > budget_s:
                        stopped = "budget"
                        break
                    row = ads.get(ad_id)
                    if row is None:          # the ad's row is gone: nothing to complete
                        done.append(ad_id)
                        continue
                    try:
                        media = fetch_media(sess, pr["urls"], **(pr.get("kw") or {}))
                    except (requests.RequestException, MediaError, OSError, ValueError) as e:
                        media, row["media_error"] = [], f"{e.__class__.__name__}: {str(e)[:120]}"
                        counts["media_errors"] += 1
                    taker.take(row, media, h.id, today)
                    row.pop("media_pending", None)
                    counts["ads_read"] += 1
                    done.append(ad_id)
            finally:
                taker.flush()
                store.write_jsonl(path, sorted(ads.values(), key=lambda r: (r.get("first_shown") or "", r["ad_id"]),
                                               reverse=True))
                for ad_id in done:
                    pend.pop(ad_id, None)
                store.write_jsonl(pend_path, sorted(pend.values(), key=lambda r: r["ad_id"]))
                counts["ads_left"] += len(pend)
                taker.save()
            counts["houses"] += 1
    except ScoreError as e:
        stopped = f"reader: {str(e)[:200]}"
    taker.save()
    out = {"run": run, "stage": "read", "updated_at": store.utc_now(), **counts, "stopped": stopped}
    store.append_jsonl(P["prov"], [out])
    return out


def probe(name: str, run: str) -> list[str]:
    st = store.read_state(paths(name)["state"])
    if st.get("run") != run:
        return [f"no {name} run {run} on file"]
    errs = [f"{h}: {why}" for h, why in sorted((st.get("problems") or {}).items())]
    none = sorted(h for h, r in (st.get("advertisers") or {}).items() if not r.get("keep"))
    if none:
        errs.append(f"no advertiser account matched for: {', '.join(none)}")
    if st.get("stopped"):
        errs.append(f"stopped: {st['stopped']}")
    return errs


def waiting(name: str, run: str, why: str) -> int:
    store.append_jsonl(paths(name)["prov"], [{"run": run, "updated_at": store.utc_now(), "waiting": why}])
    print(f"::notice::{name}: {why}; nothing collected")
    return 0


def main_for(name: str, make_source, argv: list[str] | None = None, needs: tuple[str, ...] = ()) -> int:
    """The command line every source shares: collect or probe, with the reader unless --no-reader, waiting
    (and saying so in provenance) when a needed secret is absent."""
    import argparse
    import os

    ap = argparse.ArgumentParser(prog=f"adtone.{name}")
    ap.add_argument("stage", choices=["collect", "read", "probe"])
    ap.add_argument("--run", default=config.run_id())
    ap.add_argument("--budget-min", type=float, default=60)
    ap.add_argument("--no-reader", action="store_true")
    ap.add_argument("--no-clothes", action="store_true", help="the eighteen questions only, not the clothes")
    ap.add_argument("--houses", default="", help="comma-separated house ids, for a trial run")
    ap.add_argument("--fresh", action="store_true", help="search every house again, even one searched to the end this week")
    ap.add_argument("--rematch", action="store_true", help="match every house's advertiser accounts again first")
    a = ap.parse_args(argv)
    if a.stage == "probe":
        for e in probe(name, a.run):
            print(f"::warning::{e}")
        return 0
    missing = [k for k in needs if not os.environ.get(k, "").strip()]
    if missing:
        return waiting(name, a.run, "no " + " / ".join(missing))
    embedder = scorer = None
    if not a.no_reader:
        try:
            from .embed import OpenClipEmbedder
            from .score import load_rubric, make_scorer
            embedder, scorer = OpenClipEmbedder(), make_scorer(load_rubric())
        except Exception as e:
            print(f"::warning::{name}: reader unavailable, ads only: {e.__class__.__name__}: {str(e)[:200]}")
        if scorer is not None and not a.no_clothes:
            try:
                from .score import clothes_scorer
                scorer = [scorer, clothes_scorer()]
            except Exception as e:
                print(f"::warning::{name}: clothes reader unavailable, the eighteen questions only: {e.__class__.__name__}: {str(e)[:200]}")
    reg = registry.load()
    if a.houses:
        want = {x.strip() for x in a.houses.split(",") if x.strip()}
        reg = registry.Registry(version=reg.version, status=reg.status, houses=[h for h in reg.houses if h.id in want])
    if a.stage == "read":
        if scorer is None:
            return waiting(name, a.run, "no reader for the read stage")
        try:
            out = read_pending(name, reg, a.run, embedder, scorer, a.budget_min * 60)
        except Exception as e:
            out = {"run": a.run, "stage": "read", "updated_at": store.utc_now(), "crashed": f"{e.__class__.__name__}: {str(e)[:300]}"}
            store.append_jsonl(paths(name)["prov"], [out])
            print(f"::error::{name} read crashed, recorded in provenance: {out['crashed']}")
            return 1
        print(out)
        return 0
    try:
        src = make_source()
        if a.fresh:
            src.house_refresh_days = 0
        if a.rematch:
            src.advertiser_refresh_days = 0
        out = collect(src, reg, a.run, embedder, scorer, a.budget_min * 60)
    except Exception as e:
        out = {"run": a.run, "updated_at": store.utc_now(), "crashed": f"{e.__class__.__name__}: {str(e)[:300]}"}
        store.append_jsonl(paths(name)["prov"], [out])
        print(f"::error::{name} crashed, recorded in provenance: {out['crashed']}")
        return 1
    print(out)
    return 0
