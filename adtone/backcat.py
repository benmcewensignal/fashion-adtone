"""Back catalogue: older campaigns, from sources that keep them.

    python -m adtone.backcat discover     # models.com: each house's campaigns, with crew, date and venue
    python -m adtone.backcat images       # Wayback Machine: what those campaigns showed on the houses' sites
    python -m adtone.backcat probe --run <run id>

models.com supplies the spine (crew, publication date, the page the campaign was published on,
picture and film counts) but serves no pixels to an anonymous reader. Pixels come from the
Wayback Machine's captures of that source page, made around the publication date. Campaigns
published on Instagram stay metadata-only: the archive holds login walls there, not images.

T1 rule throughout: derived rows only, images held in memory and discarded. Nothing here feeds
the v1 pre-registered tests, which use the Meta repository alone. The back catalogue is for
later registered questions, spliced on through `adtone.calibrate`.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import unicodedata
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests

from . import config, registry, store
from .media import MediaError, download, jpeg_for_model
from .score import ScoreError

MODELS = "https://models.com"
CDX = "https://web.archive.org/cdx/search/cdx"
WAYBACK_IMG = "https://web.archive.org/web/{ts}im_/{url}"
WAYBACK_RAW = "https://web.archive.org/web/{ts}id_/{url}"
UA = "fashion-adtone research crawler (contact via the repository)"
NO_PIXEL_VENUES = ("instagram.com", "facebook.com", "tiktok.com", "x.com", "twitter.com")
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")
IMG_SKIP = ("logo", "icon", "sprite", "favicon", "placeholder", ".svg")
KEEP_PER_CAMPAIGN = 8


def paths() -> dict[str, Path]:
    b = config.DATA / "backcat"
    return {"campaigns": b / "campaigns", "media": b / "media", "obs": b / "obs", "vectors": b / "vectors",
            "state": config.STATE_DIR / "backcat.json", "prov": config.PROV_DIR / "backcat.jsonl"}


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


class _Page(HTMLParser):
    BLOCK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "tr", "section", "article", "ul", "ol", "header", "footer"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[dict] = []
        self.meta: dict[str, str] = {}
        self.imgs: list[str] = []
        self.iframes = 0
        self.title = ""
        self._a: dict | None = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a":
            self._a = {"href": a.get("href", ""), "text": ""}
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content"):
                self.meta[key] = a["content"]
        elif tag == "iframe":
            self.iframes += 1
        elif tag in ("img", "source"):
            for k in ("src", "data-src", "data-lazy-src"):
                if a.get(k):
                    self.imgs.append(a[k])
            if a.get("srcset"):
                self.imgs.append(_largest(a["srcset"]))
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "a" and self._a is not None:
            self.links.append(self._a)
            self._a = None
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        self.parts.append(data)
        if self._a is not None:
            self._a["text"] += data
        if self._in_title:
            self.title += data

    @property
    def text(self) -> str:
        lines = (re.sub(r"\s+", " ", l).strip() for l in "".join(self.parts).split("\n"))
        return "\n".join(l for l in lines if l)


def _largest(srcset: str) -> str:
    best, best_w = "", -1
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        w = int(bits[1][:-1]) if len(bits) > 1 and bits[1].endswith("w") and bits[1][:-1].isdigit() else 0
        if w >= best_w:
            best, best_w = bits[0], w
    return best


def _parse_date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:   # models.com writes dates US-style, month first
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat()
    try:
        from datetime import datetime
        return datetime.strptime(s, "%B %Y").date().isoformat()
    except ValueError:
        return None


def work_url(href: str) -> str | None:
    m = re.match(r"^(?:https?://(?:www\.)?models\.com)?/[Ww]ork/([A-Za-z0-9-]+)/?$", href.strip())
    return f"{MODELS}/work/{m.group(1).lower()}" if m else None


def parse_client(html: str) -> list[str]:
    p = _Page()
    p.feed(html)
    return list(dict.fromkeys(u for u in (work_url(l["href"]) for l in p.links) if u))


def parse_work(html: str, url: str) -> dict:
    p = _Page()
    p.feed(html)
    t = p.text
    src = re.search(r"Source:\s*([A-Za-z0-9.-]+\.[A-Za-z]{2,})", t)
    pub = re.search(r"Published:\s*(\d{1,2}/\d{1,2}/\d{4}|[A-Z][a-z]+ \d{4})", t)
    kind = re.search(r"All people in this ([a-z][a-z ]*?):", t)
    people: dict[str, list[str]] = {}
    for m in re.finditer(r"^(?:•\s*)?([^\n:•]{2,60}?) - ([A-Z][A-Za-z/ ]{1,40})$", t, re.M):
        name, role = m.group(1).strip(), m.group(2).strip()
        if name not in people.setdefault(role, []):
            people[role].append(name)
    brands = []
    for m in re.finditer(r"Brands in this picture:\s*([^\n]+)", t):
        for b in m.group(1).split(","):
            if b.strip() and b.strip() not in brands:
                brands.append(b.strip())
    source_url = next((l["href"] for l in p.links if "complete story" in l["text"].lower() and l["href"].startswith("http")), None)
    return {
        "campaign_id": url.rstrip("/").rsplit("/", 1)[-1].lower(), "url": url,
        "title": (p.meta.get("og:title") or p.title or "").strip()[:200],
        "kind": kind.group(1) if kind else None,
        "published": _parse_date(pub.group(1) if pub else None),
        "source_domain": src.group(1).lower() if src else None,
        "source_url": source_url, "people": people, "brands": brands,
        "picture_credit_lines": len(re.findall(r"Credits for this picture:", t)), "films": p.iframes,
    }


def archived_image_urls(html: str, original_url: str, ts: str, cap: int = 12) -> list[str]:
    p = _Page()
    p.feed(html)
    cands = [p.meta.get("og:image"), p.meta.get("twitter:image")] + p.imgs + re.findall(r'"image"\s*:\s*"([^"]+)"', html)
    out, seen = [], set()
    for u in cands:
        if not u or u.startswith("data:"):
            continue
        absu = urljoin(original_url, u.replace("\\/", "/"))
        parsed = urlparse(absu)
        low = parsed.path.lower()
        if parsed.scheme not in ("http", "https") or any(s in low for s in IMG_SKIP):
            continue
        if not low.endswith(IMG_EXT) and "image" not in absu.lower():
            continue
        key = parsed.netloc + parsed.path
        if key not in seen:
            seen.add(key)
            out.append(WAYBACK_IMG.format(ts=ts, url=absu))
        if len(out) >= cap:
            break
    return out


class Crawler:
    """Polite fetching: robots.txt honoured, a pause between requests, one identifiable user agent."""

    def __init__(self, session: requests.Session | None = None, pause: float = 2.0, sleep=time.sleep):
        self.s = session or requests.Session()
        self.s.headers.setdefault("User-Agent", UA)
        self.pause, self.sleep = pause, sleep
        self._robots: dict[str, robotparser.RobotFileParser] = {}
        self.calls = 0

    def allowed(self, url: str) -> bool:
        host = urlparse(url)
        base = f"{host.scheme}://{host.netloc}"
        if base not in self._robots:
            rp = robotparser.RobotFileParser()
            try:
                r = self.s.get(f"{base}/robots.txt", timeout=30)
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except requests.RequestException:
                rp.parse([])
            self._robots[base] = rp
        return self._robots[base].can_fetch(UA, url)

    def get(self, url: str, params: dict | None = None):
        self.calls += 1
        if self.pause:
            self.sleep(self.pause)
        return self.s.get(url, params=params, timeout=60)


def cdx_prefix(c: Crawler, prefix: str, limit: int = 5000) -> list[str]:
    r = c.get(CDX, {"url": prefix, "matchType": "prefix", "output": "json", "fl": "original",
                    "collapse": "urlkey", "filter": "statuscode:200", "limit": str(limit)})
    rows = r.json() if r.status_code == 200 and r.text.strip() else []
    return [row[0] for row in rows[1:]]


def captures(c: Crawler, url: str, around: date, window_days: int = 75, limit: int = 6) -> list[str]:
    r = c.get(CDX, {"url": url, "output": "json", "fl": "timestamp", "limit": str(limit),
                    "from": (around - timedelta(days=window_days)).strftime("%Y%m%d"),
                    "to": (around + timedelta(days=window_days)).strftime("%Y%m%d"),
                    "filter": ["statuscode:200", "mimetype:text/html"]})
    rows = r.json() if r.status_code == 200 and r.text.strip() else []
    return [row[0] for row in rows[1:]]


def discover(c: Crawler, reg: registry.Registry, rid: str, max_reads: int = 400) -> dict:
    table = store.ShardedTable(paths()["campaigns"], "campaign_id")
    found, read, skipped = {}, 0, 0
    for h in reg.houses:
        slug = h.models_slug or slugify(h.name)
        urls: list[str] = []
        client = f"{MODELS}/client/{slug}"
        if c.allowed(client):
            r = c.get(client)
            if r.status_code == 200:
                urls += parse_client(r.text)
        for u in cdx_prefix(c, f"models.com/work/{slug}-{slug}-"):
            w = work_url(urlparse(u).path)
            if w:
                urls.append(w)
        urls = [u for u in dict.fromkeys(urls) if u.rsplit("/", 1)[-1] not in table]
        found[h.id] = len(urls)
        for u in urls:
            if read >= max_reads:
                break
            if not c.allowed(u):
                skipped += 1
                continue
            r = c.get(u)
            read += 1
            if r.status_code != 200:
                continue
            row = parse_work(r.text, u)
            row.update({"house_id": h.id, "read_run": rid})
            table.upsert({k: v for k, v in row.items() if v not in (None, [], {})} | {"campaign_id": row["campaign_id"]},
                         shard=h.id)
    table.save()
    return {"new_urls": found, "pages_read": read, "robots_skipped": skipped, "campaigns_on_file": len(table.rows)}


def images(c: Crawler, embedder, scorer, rid: str, max_campaigns: int = 150, budget_s: float = 5400,
           clock=time.monotonic) -> dict:
    from .embed import VectorStore
    P = paths()
    table = store.ShardedTable(P["campaigns"], "campaign_id")
    media = store.ShardedTable(P["media"], "campaign_id")
    vectors = VectorStore(embedder.tag, root=P["vectors"])
    obs_path = P["obs"] / f"{scorer.rubric.version}.jsonl"
    scored = {(r["sha"], r["instrument"]) for r in store.read_jsonl(obs_path)}
    month = date.today().strftime("%Y-%m")
    todo = [r for r in table.rows.values() if r.get("kind") == "campaign" and r.get("published")
            and (r["campaign_id"] not in media or media.get(r["campaign_id"])["status"] == "error")]
    todo.sort(key=lambda r: r["published"], reverse=True)   # the overlap year first, so calibration can start
    counts = {"campaigns": 0, "resolved": 0, "no_pixel_venue": 0, "no_source": 0, "no_capture": 0, "no_images": 0,
              "error": 0, "images": 0, "scored_ok": 0}
    new_obs, t0 = [], clock()
    for row in todo[:max_campaigns]:
        if clock() - t0 > budget_s:
            break
        m = {"campaign_id": row["campaign_id"], "house_id": row["house_id"], "processed_run": rid}
        src, dom = row.get("source_url"), row.get("source_domain") or ""
        try:
            if any(dom.endswith(v) for v in NO_PIXEL_VENUES):
                m["status"] = "no_pixel_venue"
            elif not src:
                m["status"] = "no_source"
            else:
                stamps = captures(c, src, date.fromisoformat(row["published"]))
                if not stamps:
                    m["status"] = "no_capture"
                else:
                    got = []
                    for ts in stamps[:3]:
                        r = c.get(WAYBACK_RAW.format(ts=ts, url=src))
                        if r.status_code != 200:
                            continue
                        urls = archived_image_urls(r.text, src, ts)
                        got = download(urls, c.s, keep=KEEP_PER_CAMPAIGN) if urls else []
                        if got:
                            m["capture"] = ts
                            break
                    if not got:
                        m["status"] = "no_images"
                    else:
                        for f in got:
                            counts["images"] += 1
                            if f.sha not in vectors:
                                vectors.add(f.sha, embedder.embed(f.image), month)
                            if (f.sha, scorer.instrument) not in scored:
                                ob = {"sha": f.sha, "instrument": scorer.instrument, "rubric_version": scorer.rubric.version,
                                      "run_id": rid, "scored_at": store.utc_now()}
                                try:
                                    ob["output"], ob["status"] = scorer.score(jpeg_for_model(f.image)), "ok"
                                    counts["scored_ok"] += 1
                                except ScoreError as e:
                                    if str(e).startswith("API:"):
                                        raise
                                    ob["status"], ob["error"] = "invalid", str(e)[:300]
                                new_obs.append(ob)
                                scored.add((f.sha, scorer.instrument))
                            f.image.close()
                        m["status"] = "resolved"
                        m["images"] = [{"sha": f.sha, "phash": f.phash, "w": f.w, "h": f.h} for f in got]
        except ScoreError as e:
            counts["stopped_on"] = str(e)
            break
        except (requests.RequestException, MediaError, ValueError) as e:
            m["status"], m["error"] = "error", str(e)[:300]
        counts[m["status"]] += 1
        counts["campaigns"] += 1
        media.upsert(m, shard=month)
    store.append_jsonl(obs_path, new_obs)
    media.save()
    vectors.save()
    return counts


def probe(run: str, instrument: str, embed_tag: str) -> list[str]:
    from .embed import VectorStore
    P = paths()
    rows = [r for p in sorted(P["media"].glob("*.jsonl")) for r in store.read_jsonl(p) if r.get("processed_run") == run]
    if not rows:
        print(f"probe backcat: run {run} processed no campaigns")
        return []
    shas = {i["sha"] for r in rows if r["status"] == "resolved" for i in r.get("images", [])}
    scored = {r["sha"] for r in store.read_jsonl(P["obs"] / f"{instrument.split('@')[0]}.jsonl") if r["instrument"] == instrument}
    vecs = VectorStore(embed_tag, root=P["vectors"]).vecs
    status: dict[str, int] = {}
    for r in rows:
        status[r["status"]] = status.get(r["status"], 0) + 1
    print(f"probe backcat: {len(rows)} campaigns {status}; {len(shas)} images")
    errs = []
    archive_eligible = sum(v for k, v in status.items() if k in ("resolved", "no_images", "no_capture", "error"))
    if archive_eligible >= 20 and status.get("resolved", 0) == 0:
        errs.append(f"none of {archive_eligible} archive-eligible campaigns yielded an image: check the capture parsing")
    if shas and sum(s not in scored for s in shas) > 0.1 * len(shas):
        errs.append("more than 10% of this run's images have no observation row")
    if shas and sum(s not in vecs for s in shas) > 0.1 * len(shas):
        errs.append("more than 10% of this run's images have no vector")
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.backcat")
    ap.add_argument("stage", choices=["discover", "images", "probe"])
    ap.add_argument("--run", default=None)
    ap.add_argument("--max", type=int, default=150)
    ap.add_argument("--budget-min", type=float, default=90)
    a = ap.parse_args(argv)
    rid = a.run or config.run_id()
    reg = registry.load()
    if a.stage == "probe":
        errs = probe(rid, f"{config.RUBRIC_VERSION}@{config.CLAUDE_MODEL}", config.EMBED_TAG)
        for e in errs:
            print(f"::error::{e}")
        return 1 if errs else 0
    c = Crawler()
    if a.stage == "discover":
        out = discover(c, reg, rid, max_reads=a.max * 3)
    else:
        from .embed import OpenClipEmbedder
        from .score import ClaudeScorer, load_rubric
        out = images(c, OpenClipEmbedder(), ClaudeScorer(load_rubric()), rid, a.max, a.budget_min * 60)
    out.update({"run_id": rid, "stage": a.stage, "finished_at": store.utc_now(), "requests": c.calls})
    store.append_jsonl(paths()["prov"], [out])
    st = store.read_state(paths()["state"])
    st[f"last_{a.stage}"] = out["finished_at"]
    store.write_state(paths()["state"], st)
    print({k: v for k, v in out.items() if k != "new_urls"})
    if out.get("stopped_on"):
        print(f"::error::scoring API failed, progress saved: {out['stopped_on']}")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
