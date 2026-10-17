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
    Stop                        exception class: the run's budget or the library's limit is reached, keep what came
    close()                     optional

Pictures are never written to disk. Rows and readings go to data/<name>/, the state to data/state/<name>.json,
and every run to data/provenance/<name>.jsonl, so a run that found nothing still says so.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from . import config, registry, store
from .media import MediaError, jpeg_for_model
from .score import ScoreError, score_batch

BATCH = 16


def paths(name: str) -> dict[str, Path]:
    b = config.DATA / name
    return {"ads": b / "ads", "obs": b / "obs", "vectors": b / "vectors",
            "state": config.STATE_DIR / f"{name}.json", "prov": config.PROV_DIR / f"{name}.jsonl"}


def shape(obj, depth: int = 0):
    """The keys and types of a record, without its values: what a library actually returns, kept in the
    state after the first live run so the row can be tuned to it without storing any ad's text."""
    if depth > 6:
        return "..."
    if isinstance(obj, dict):
        return {str(k): shape(v, depth + 1) for k, v in list(obj.items())[:60]}
    if isinstance(obj, list):
        return [shape(obj[0], depth + 1)] if obj else []
    return type(obj).__name__


def collect(src, reg: registry.Registry, run: str, embedder=None, scorer=None, budget_s: float = 60 * 60,
            today: date | None = None, clock=time.monotonic) -> dict:
    from .embed import VectorStore
    P = paths(src.name)
    today = today or datetime.now(timezone.utc).date()
    st = store.read_state(P["state"])
    st.setdefault("advertisers", {})
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
    sample_shape = st.get("shape")

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
            try:
                for item in src.search_ads(h, keep, today - timedelta(days=src.lookback_days), today):
                    if sample_shape is None:
                        sample_shape = shape(item)
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
                    try:
                        media = src.media_for(item)
                    except (requests.RequestException, MediaError, OSError, ValueError) as e:
                        media, row["media_error"] = [], f"{e.__class__.__name__}: {str(e)[:120]}"
                        counts["media_errors"] += 1
                    row["media"] = []
                    for m in media:
                        f = m["fetched"]
                        counts["pictures"] += 1
                        row["media"].append({"kind": m["kind"], **({"at": m["at"]} if "at" in m else {}),
                                             "sha": f.sha, "phash": f.phash, "w": f.w, "h": f.h})
                        if vectors is not None and f.sha not in vectors:
                            vectors.add(f.sha, embedder.embed(f.image), (row.get("first_shown") or today.isoformat())[:7])
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
            except src.Stop:
                raise
            except Exception as e:
                problems[h.id] = f"ads: {e.__class__.__name__}: {str(e)[:200]}"
            finally:
                store.write_jsonl(path, sorted(ads.values(), key=lambda r: (r.get("first_shown") or "", r["ad_id"]),
                                               reverse=True))
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
    if obs_path is not None:
        store.append_jsonl(obs_path, new_obs)
    if vectors is not None:
        vectors.save()
    st.update({"run": run, "updated_at": store.utc_now(), "problems": problems, "stopped": stopped,
               "requests": getattr(src, "requests", None), "shape": sample_shape, **getattr(src, "state_extra", lambda: {})()})
    store.write_state(P["state"], st)
    out = {"run": run, "updated_at": st["updated_at"], **counts, "requests": getattr(src, "requests", None),
           "problems": problems, "stopped": stopped}
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
    ap.add_argument("stage", choices=["collect", "probe"])
    ap.add_argument("--run", default=config.run_id())
    ap.add_argument("--budget-min", type=float, default=60)
    ap.add_argument("--no-reader", action="store_true")
    ap.add_argument("--houses", default="", help="comma-separated house ids, for a trial run")
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
    reg = registry.load()
    if a.houses:
        want = {x.strip() for x in a.houses.split(",") if x.strip()}
        reg = registry.Registry(version=reg.version, status=reg.status, houses=[h for h in reg.houses if h.id in want])
    try:
        out = collect(make_source(), reg, a.run, embedder, scorer, a.budget_min * 60)
    except Exception as e:
        out = {"run": a.run, "updated_at": store.utc_now(), "crashed": f"{e.__class__.__name__}: {str(e)[:300]}"}
        store.append_jsonl(paths(name)["prov"], [out])
        print(f"::error::{name} crashed, recorded in provenance: {out['crashed']}")
        return 1
    print(out)
    return 0
