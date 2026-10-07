"""Homepage history: what each brand put on its front page, month by month, from the Wayback Machine.

    python -m adtone.homepages collect --run <id> [--max-captures 600] [--budget-min 90] [--workers 5]
    python -m adtone.homepages probe --run <id>

A brand's homepage carries the image it chose for the moment: the season's campaign, a show, a film
still. The Wayback Machine has captured the homepages of these houses several times a month for
years, which gives a long and regular history of each brand's chosen image that no ad library holds.

For each brand and month this reads one capture of the homepage: the page's share image (og:image,
the picture the brand chose to represent the page) and its largest images, fetched from the archive
as it held them then. Each image not seen before goes through the reader and the fingerprint. Images
are held in memory only; the reader's answers and the fingerprint's numbers are kept. Months are
worked newest first and across every brand before going further back, so a run cut short by its time
budget leaves an even history; the next run resumes.

What the first run taught:
  - A capture often cannot be used: the archive answers 403 or 500 for it, or what it holds is the
    site's bot wall. So each month keeps up to four captures spread across it (the index is kept in
    data/homepages/index/<house>.json and only its newest months are asked for again), and a month
    that fails is tried again on its next capture, up to three.
  - Several houses answer the bare domain with a country chooser, a refresh or a script redirect and
    no pictures. When a page has no usable image, one step is taken: the refresh or redirect target,
    or else the house's own British page (then the American, then an English one).
  - Modern luxury sites draw much of the page with scripts, so pictures also hide in style
    backgrounds, preload hints and inline data; those are read too. A month whose captures hold no
    image twice is left: the archive did not keep the pictures.
  - Fetching was most of the time. It now runs in a few parallel workers, and the reader takes the
    images in batches.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from . import config, registry, store
from .backcat import CDX, IMG_SKIP, WAYBACK_IMG, Crawler, _cdx_rows, _Page, archived_image_urls
from .media import MediaError, download, jpeg_for_model
from .score import ScoreError, score_batch

FIRST_MONTH = "2014-01"
REPLAY = "https://web.archive.org/web/{ts}/{url}"
SITES_FILE = config.ROOT / "reference" / "brand_sites.csv"
KEEP_PER_CAPTURE = 4
CANDIDATES_PER_MONTH = 4
MAX_ATTEMPTS = 3        # captures tried for a month before it is left: the archive does not have it
NO_IMAGE_ATTEMPTS = 2   # captures holding no picture: the archive did not keep the page's pictures
TRIES_PER_RUN = 2       # a month whose capture fails is tried on its next capture in the same run
IMAGE_CANDIDATES = 10
CHUNK = 12              # months fetched together; the reader is called once per batch of images
BATCH = 16
WORKERS = 5
REWRITE = re.compile(r"(?:https?:)?(?://web\.archive\.org)?/web/\d{1,14}(?:[a-z]{2}_)?/(?=https?://|//)")
WRAPPED = re.compile(r"^https://web\.archive\.org/web/\d{1,14}(?:[a-z]{2}_)?/")
BACKGROUND = re.compile(r"background(?:-image)?\s*:\s*url\(\s*['\"]?([^'\")\s]+)", re.I)
PRELOAD = re.compile(r"<link\b[^>]*\bas=[\"']?image[\"']?[^>]*>", re.I)
HREF = re.compile(r"\b(?:href|imagesrcset)=[\"']([^\"']+)[\"']", re.I)
DATA_IMAGE = re.compile(r"[\"']((?:https?:)?//[^\"'\s<>]+?\.(?:jpe?g|png|webp)(?:\?[^\"'\s<>]*)?)[\"']", re.I)
REFRESH = re.compile(r"<meta\b[^>]*http-equiv=[\"']?refresh[\"']?[^>]*content=[\"'][^\"']*?url=([^\"'>\s]+)", re.I)
SCRIPT_GO = re.compile(r"(?:window\.|document\.|top\.)?location(?:\.href)?\s*=\s*[\"']([^\"']+)[\"']"
                       r"|location\.replace\(\s*[\"']([^\"']+)[\"']\s*\)", re.I)
LOCALE = re.compile(r"^/(?:[a-z]{2,3}(?:[-_][a-z0-9]{2})?)(?:/[a-z]{2,3}(?:[-_][a-z0-9]{2})?)?"
                    r"(?:/(?:home|homepage|index)(?:\.html?)?)?/?$", re.I)


def paths() -> dict[str, Path]:
    b = config.DATA / "homepages"
    return {"captures": b / "captures", "obs": b / "obs", "vectors": b / "vectors", "index": b / "index",
            "state": config.STATE_DIR / "homepages.json", "prov": config.PROV_DIR / "homepages.jsonl"}


def sites(path: Path | None = None) -> dict[str, list[str]]:
    with (path or SITES_FILE).open(encoding="utf-8") as f:
        return {r["house"]: [d.strip() for d in r["domains"].split(";") if d.strip()] for r in csv.DictReader(f)}


def unrewrite(html: str) -> str:
    """Archive replay rewrites every link to point into the archive; put the original addresses back."""
    return REWRITE.sub("", html)


def final_url(replayed: str, requested: str) -> str:
    """The page's own address after the archive followed its redirects (for resolving relative links)."""
    m = re.search(r"/web/\d{1,14}(?:[a-z]{2}_)?/(https?://.+)$", replayed)
    return m.group(1) if m else requested


def _site(host: str) -> str:
    return ".".join(host.lower().split(":")[0].split(".")[-2:])


def _original(u: str) -> str:
    return WRAPPED.sub("", u)


def page_images(html: str, page_url: str, ts: str, cap: int = IMAGE_CANDIDATES + 4) -> list[str]:
    """Image addresses in the archive at `ts`: the share image first, then the page's images in order,
    then pictures set as style backgrounds, preloaded, or named in the page's inline data."""
    html = unrewrite(html)
    out = archived_image_urls(html, page_url, ts, cap=cap)
    seen = {(lambda p: p.netloc + p.path)(urlparse(_original(u))) for u in out}
    found = BACKGROUND.findall(html)
    for tag in PRELOAD.findall(html):
        for v in HREF.findall(tag):
            found.append(v.split(",")[0].strip().split(" ")[0])
    found += DATA_IMAGE.findall(html.replace("\\/", "/"))
    for u in found:
        if len(out) >= cap:
            break
        if not u or u.startswith("data:"):
            continue
        absu = urljoin(page_url, u)
        p = urlparse(absu)
        if p.scheme not in ("http", "https") or any(s in p.path.lower() for s in IMG_SKIP):
            continue
        if (p.netloc + p.path) in seen:
            continue
        seen.add(p.netloc + p.path)
        out.append(WAYBACK_IMG.format(ts=ts, url=absu))
    return out[:cap]


def _locale_rank(path: str) -> float:
    toks = [t for t in re.split(r"[/_\-.]", path.lower()) if t]
    if any(t in ("gb", "uk") for t in toks):
        r = 4.0
    elif any(t in ("us", "usa") for t in toks):
        r = 3.0
    elif any(t in ("en", "eng", "int", "intl", "ww", "e1") for t in toks):
        r = 2.0
    else:
        return 0.0
    return r + (0.5 if any(t in ("en", "eng") for t in toks) else 0.0)


def next_page(html: str, page_url: str) -> tuple[str, str] | None:
    """Where a page with no pictures points the reader: its refresh or script redirect, or else the
    house's own British (then American, then English) page from a country chooser."""
    html = unrewrite(html)
    here = urlparse(page_url)

    def ok(u: str) -> str | None:
        absu = urljoin(page_url, u.strip())
        p = urlparse(absu)
        if p.scheme not in ("http", "https") or _site(p.netloc) != _site(here.netloc):
            return None
        if (p.netloc, p.path.rstrip("/")) == (here.netloc, here.path.rstrip("/")):
            return None
        return absu
    m = REFRESH.search(html)
    if m and ok(m.group(1)):
        return ok(m.group(1)), "refresh"
    for m in SCRIPT_GO.finditer(html):
        u = ok(m.group(1) or m.group(2) or "")
        if u:
            return u, "script redirect"
    p = _Page()
    p.feed(html)
    best, best_rank = None, 0.0
    for link in p.links:
        u = ok(link.get("href") or "")
        if not u:
            continue
        path = urlparse(u).path or "/"
        if not LOCALE.match(path):
            continue
        rank = _locale_rank(path)
        if rank > best_rank:
            best, best_rank = u, rank
    return (best, "country page") if best and best_rank >= 2 else None


# ---------- the index of captures ----------

def _spread(rows: list, k: int = CANDIDATES_PER_MONTH) -> list:
    """Up to k captures spread across the month, the month's first capture first."""
    if len(rows) <= k:
        return rows
    idx = sorted({round(i * (len(rows) - 1) / (k - 1)) for i in range(k)})
    return [rows[i] for i in idx]


def _index_path(house: str) -> Path:
    return paths()["index"] / f"{house}.json"


def load_index(house: str) -> dict[str, list[list[str]]]:
    p = _index_path(house)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_index(house: str, idx: dict[str, list[list[str]]]) -> None:
    p = _index_path(house)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({m: idx[m] for m in sorted(idx)}, separators=(",", ":")) + "\n", encoding="utf-8")


def refresh_index(c: Crawler, domains: list[str], idx: dict, first: str = FIRST_MONTH) -> tuple[dict, dict]:
    """Ask the archive for the months from the newest one on file onwards (it may have been part-filled)
    and merge: month -> up to four [timestamp, original url, status]. The current domain comes first; an
    older one fills months the current one lacks. Returns the index and, for any domain that gave no
    rows on a full query, what the archive said."""
    since = max(idx) if idx else first
    per_domain: list[dict[str, list[list[str]]]] = []
    diag: dict[str, dict] = {}
    for d in domains:
        r = c.get(CDX, {"url": d, "output": "json", "fl": "timestamp,original,statuscode",
                        "from": since.replace("-", ""), "collapse": "timestamp:8",
                        "filter": "statuscode:(200|301|302|307|308)"})
        rows = _cdx_rows(r)[1:]
        months: dict[str, list[list[str]]] = {}
        for row in rows:
            ts, original, status = row[0], row[1], (row[2] if len(row) > 2 else "")
            months.setdefault(f"{ts[:4]}-{ts[4:6]}", []).append([ts, original, status])
        if not rows and since == first:
            diag[d] = {"http": getattr(r, "status_code", None), "body": (getattr(r, "text", "") or "")[:200]}
        per_domain.append(months)
    out = {m: v for m, v in idx.items() if m < since}
    for months in per_domain:
        for m, rows in months.items():
            if m not in out or (m >= since and not out[m]):
                out[m] = _spread(sorted(rows))
    return out, diag


# ---------- reading one month ----------

def _key(house: str, month: str) -> str:
    return f"{house}:{month}"


def _done(row: dict | None, n_cands: int) -> bool:
    if not row:
        return False
    if row.get("status") == "resolved":
        return True
    limit = NO_IMAGE_ATTEMPTS if row.get("status") == "no_images" else MAX_ATTEMPTS
    return row.get("attempts", 0) >= min(limit, max(n_cands, 1))


def plan(index: dict[str, dict[str, list]], rows: dict[str, dict]) -> list[tuple[str, str]]:
    """(house, month) still to read: newest month first, every house before going further back."""
    months = sorted({m for per in index.values() for m in per}, reverse=True)
    out = []
    for m in months:
        for house in sorted(index):
            cands = index[house].get(m)
            if cands and not _done(rows.get(_key(house, m)), len(cands)):
                out.append((house, m))
    return out


def read_capture(c: Crawler, ts: str, url: str) -> dict:
    """One capture: the page, its pictures, and one step onwards when it has none."""
    r = c.get(REPLAY.format(ts=ts, url=url))
    ctype = (getattr(r, "headers", None) or {}).get("Content-Type", "text/html")
    if r.status_code != 200 or "html" not in ctype:
        return {"status": "error", "error": f"HTTP {r.status_code} {ctype[:40]}", "images": []}
    page = final_url(getattr(r, "url", "") or "", url)
    urls = page_images(r.text, page, ts)
    got = download(urls, c.s, keep=KEEP_PER_CAPTURE, max_candidates=IMAGE_CANDIDATES) if urls else []
    if got:
        return {"status": "resolved", "page": page, "images": got}
    step = next_page(r.text, page)
    if step:
        nxt, why = step
        r2 = c.get(REPLAY.format(ts=ts, url=nxt))
        ctype2 = (getattr(r2, "headers", None) or {}).get("Content-Type", "text/html")
        if r2.status_code == 200 and "html" in ctype2:
            page2 = final_url(getattr(r2, "url", "") or "", nxt)
            urls = page_images(r2.text, page2, ts)
            got = download(urls, c.s, keep=KEEP_PER_CAPTURE, max_candidates=IMAGE_CANDIDATES) if urls else []
            if got:
                return {"status": "resolved", "page": page2, "via": why, "images": got}
        return {"status": "no_images", "page": page, "followed": why, "images": []}
    return {"status": "no_images", "page": page, "images": []}


def read_month(c: Crawler, house: str, month: str, cands: list, prev: dict | None, rid: str) -> tuple[dict, list]:
    """Try the month's next untried capture, and one more if that fails, keeping the first that works."""
    prev = prev or {}
    tried = list(prev.get("tried") or [])
    start = prev.get("attempts", 0)
    row: dict = {"key": _key(house, month), "house_id": house, "month": month, "run": rid}
    res: dict = {"status": "error", "error": "no capture left", "images": []}
    for i in range(start, min(len(cands), start + TRIES_PER_RUN, MAX_ATTEMPTS)):
        ts, url = cands[i][0], cands[i][1]
        try:
            res = read_capture(c, ts, url)
        except (requests.RequestException, MediaError, ValueError) as e:
            res = {"status": "error", "error": f"{e.__class__.__name__}: {str(e)[:200]}", "images": []}
        tried.append(ts)
        row.update({"capture": ts, "url": url, "attempts": i + 1})
        if res["status"] == "resolved" or (res["status"] == "no_images" and i + 1 >= NO_IMAGE_ATTEMPTS):
            break
    images = res.pop("images")
    row.update({k: v for k, v in res.items()})
    row["tried"] = tried
    return row, images


# ---------- the run ----------

def collect(c: Crawler, reg: registry.Registry, embedder, scorer, rid: str, max_captures: int = 600,
            budget_s: float = 90 * 60, clock=time.monotonic, workers: int = 1, make_crawler=None) -> dict:
    from .embed import VectorStore
    P = paths()
    table = store.ShardedTable(P["captures"], "key")
    vectors = VectorStore(embedder.tag, root=P["vectors"])
    obs_path = P["obs"] / f"{scorer.rubric.version}.jsonl"
    scored = {(r["sha"], r["instrument"]) for r in store.read_jsonl(obs_path)}
    known = {h.id for h in reg.houses}
    domains = {h: ds for h, ds in sites().items() if h in known}
    t0 = clock()
    counts = {"captures": 0, "resolved": 0, "no_images": 0, "error": 0, "images": 0, "new_images": 0, "scored_ok": 0,
              "invalid": 0, "later_capture": 0, "stepped": 0, "reader_calls": 0}
    index_errors: dict[str, str] = {}
    index_diag: dict[str, dict] = {}

    index: dict[str, dict[str, list]] = {}
    for house, ds in sorted(domains.items()):
        idx = load_index(house)
        try:
            idx, diag = refresh_index(c, ds, idx)
            if diag and not idx:
                index_diag[house] = diag
            save_index(house, idx)
        except (requests.RequestException, ValueError) as e:
            index_errors[house] = f"{e.__class__.__name__}: {str(e)[:160]}"
        index[house] = idx
    todo = plan(index, table.rows)[:max_captures]

    made: list[Crawler] = []
    local = threading.local()
    lock = threading.Lock()

    def crawler() -> Crawler:
        if make_crawler is None:
            return c
        if not hasattr(local, "c"):
            local.c = make_crawler()
            with lock:
                made.append(local.c)
        return local.c

    def work(item):
        house, month = item
        return read_month(crawler(), house, month, index[house][month], table.get(_key(house, month)), rid)

    new_obs: list[dict] = []
    stopped = None
    finished_chunks = 0

    def finish(futs) -> None:
        """Fingerprint and read the chunk's new images, then file its months. If the reader fails this
        raises before anything from the chunk is filed, so those months are read again next run."""
        nonlocal new_obs, finished_chunks
        results = [f.result() for f in futs]
        pending: list[tuple[object, dict]] = []
        queued: set[str] = set()
        for row, images in results:
            for f in images:
                counts["images"] += 1
                if f.sha not in vectors:
                    vectors.add(f.sha, embedder.embed(f.image), row["month"][:4])
                    counts["new_images"] += 1
                if (f.sha, scorer.instrument) not in scored and f.sha not in queued:
                    queued.add(f.sha)
                    pending.append((f, {"sha": f.sha, "instrument": scorer.instrument,
                                        "rubric_version": scorer.rubric.version, "run_id": rid,
                                        "house_id": row["house_id"], "month": row["month"]}))
        obs = []
        for i in range(0, len(pending), BATCH):
            part = pending[i:i + BATCH]
            counts["reader_calls"] += 1
            answers = score_batch(scorer, [jpeg_for_model(f.image) for f, _ in part])   # raises on a reader failure
            for (f, ob), ans in zip(part, answers):
                ob["scored_at"] = store.utc_now()
                if isinstance(ans, ScoreError):
                    ob["status"], ob["error"] = "invalid", str(ans)[:300]
                    counts["invalid"] += 1
                else:
                    ob["output"], ob["status"] = ans, "ok"
                    counts["scored_ok"] += 1
                obs.append(ob)
        scored.update((sha, scorer.instrument) for sha in queued)
        new_obs += obs
        for row, images in results:
            if images:
                row["images"] = [{"sha": f.sha, "phash": f.phash, "w": f.w, "h": f.h} for f in images]
            if row["status"] == "resolved" and row.get("attempts", 1) > 1:
                counts["later_capture"] += 1
            if row.get("via"):
                counts["stepped"] += 1
            counts[row["status"]] += 1
            counts["captures"] += 1
            table.upsert(row, shard=row["house_id"])
            for f in images:
                f.image.close()
        finished_chunks += 1
        if finished_chunks % 4 == 0:   # a run cut off by its job timeout keeps most of its work
            table.save()
            store.append_jsonl(obs_path, new_obs)
            new_obs = []
            vectors.save()

    chunks = [todo[i:i + CHUNK] for i in range(0, len(todo), CHUNK)]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        in_flight = None
        for chunk in chunks:
            if clock() - t0 > budget_s:
                stopped = "budget"
                break
            futs = [ex.submit(work, item) for item in chunk]
            if in_flight is not None:
                try:
                    finish(in_flight)
                except ScoreError as e:
                    stopped = f"reader: {str(e)[:200]}"
                    for f in futs:
                        f.cancel()
                    in_flight = None
                    break
            in_flight = futs
        if in_flight is not None:
            try:
                finish(in_flight)
            except ScoreError as e:
                stopped = f"reader: {str(e)[:200]}"
    table.save()
    store.append_jsonl(obs_path, new_obs)
    vectors.save()
    months_known = {h: len(v) for h, v in index.items()}
    out = {"run_id": rid, "finished_at": store.utc_now(), **counts,
           "remaining": len(plan(index, table.rows)), "months_in_archive": months_known,
           "index_errors": index_errors, "index_diag": index_diag, "stopped": stopped,
           "requests": c.calls + sum(m.calls for m in made), "workers": workers}
    st = store.read_state(P["state"])
    st.update({"last_run": rid, "finished_at": out["finished_at"], "remaining": out["remaining"],
               "months_in_archive": months_known, "index_diag": index_diag})
    store.write_state(P["state"], st)
    return out


def probe(run: str) -> list[str]:
    P = paths()
    rows = [r for p in sorted(P["captures"].glob("*.jsonl")) for r in store.read_jsonl(p) if r.get("run") == run]
    if not rows:
        return [f"homepages: run {run} read no captures"]
    status: dict[str, int] = {}
    for r in rows:
        status[r["status"]] = status.get(r["status"], 0) + 1
    print(f"probe homepages: {len(rows)} captures {status}")
    errs = []
    if len(rows) >= 30 and not status.get("resolved"):
        errs.append(f"none of {len(rows)} captures yielded an image: check the page parsing")
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.homepages")
    ap.add_argument("stage", choices=["collect", "probe"])
    ap.add_argument("--run", default=None)
    ap.add_argument("--max-captures", type=int, default=600)
    ap.add_argument("--budget-min", type=float, default=90)
    ap.add_argument("--workers", type=int, default=WORKERS)
    a = ap.parse_args(argv)
    rid = a.run or config.run_id()
    if a.stage == "probe":
        errs = probe(rid)
        for e in errs:
            print(f"::error::{e}")
        return 1 if errs else 0
    c = Crawler(pause=1.0)
    try:
        from .embed import OpenClipEmbedder
        from .score import load_rubric, make_scorer
        out = collect(c, registry.load(), OpenClipEmbedder(), make_scorer(load_rubric()), rid, a.max_captures,
                      a.budget_min * 60, workers=a.workers, make_crawler=lambda: Crawler(pause=1.0))
    except Exception as e:   # leave a record whatever happens
        out = {"run_id": rid, "finished_at": store.utc_now(), "crashed": f"{e.__class__.__name__}: {str(e)[:300]}",
               "requests": c.calls}
    store.append_jsonl(paths()["prov"], [out])
    print({k: v for k, v in out.items() if k != "months_in_archive"})
    if out.get("crashed"):
        print(f"::error::homepages crashed, recorded in provenance: {out['crashed']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
