"""Page views in ten languages, with renamed articles joined to their old titles.

    python -m adtone.wikiviews collect --run <id> [--budget-min 45]
    python -m adtone.wikiviews probe --run <id>

The English series in adtone.attention counts one title per house, which leaves two gaps. Views in
other languages, which say where attention comes from, are missed. And a renamed article loses its
history: Wikimedia counts a view under the title that was asked for, so the years before a move sit
under the old title, which is now a redirect. Six houses start late in the English series for that
reason.

Here each house's English article leads to its Wikidata item, and the item to the article in each
language. For each article the redirects pointing at it are listed and each redirect's monthly views
read once; every title that carried real traffic (the article, any former name, a common alternative
spelling) is kept, and daily views are summed across those titles. A view of a redirect is counted
under the redirect's own title and never under the article's, so the sum does not double count.

The scan of redirects is repeated monthly; weekly runs between scans only refresh the daily series.
Wikimedia's APIs are free and need no key, only a descriptive User-Agent; runs are paced and stop at
a time budget, resuming where they left off.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

from . import attention, config, registry, store

LANGS = ("en", "fr", "it", "de", "es", "ja", "ko", "zh", "pt", "ar")
START = date(2015, 7, 1)
WIKIDATA = "https://www.wikidata.org/w/api.php"
API = "https://{lang}.wikipedia.org/w/api.php"
BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{project}/all-access/user/{title}"
UA = "fashion-adtone research (page views by language; contact via the repository)"
SHARE_MIN = 0.01        # a redirect is counted when its views reach 1% of the article's own
MAX_REDIRECTS = 80      # per article: the long tail of misspellings carries nothing
RESCAN_DAYS = 30        # how often each article's redirects are scanned again
PAUSE_S = 0.4


def paths() -> dict[str, Path]:
    return {"dir": config.DATA / "wikiviews", "state": config.STATE_DIR / "wikiviews.json",
            "prov": config.PROV_DIR / "wikiviews.jsonl"}


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s


def _get(sess, url: str, params: dict | None = None, sleep=time.sleep, retries: int = 5):
    """One request; Wikimedia's 429 and 5xx are waited out, honouring Retry-After."""
    r = None
    for attempt in range(retries + 1):
        r = sess.get(url, params=params, timeout=60)
        if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            sleep(attention._wait(r, attempt))
            continue
        break
    return r


def items_for(sess, titles: dict[str, str], sleep=time.sleep) -> dict[str, str]:
    """House id -> Wikidata item, from the English article each house resolved to."""
    out: dict[str, str] = {}
    by_title = {t: h for h, t in titles.items() if t}
    names = sorted(by_title)
    for i in range(0, len(names), 40):
        chunk = names[i:i + 40]
        r = _get(sess, API.format(lang="en"), {"action": "query", "format": "json", "formatversion": "2", "redirects": "1",
                                              "prop": "pageprops", "ppprop": "wikibase_item", "titles": "|".join(chunk)},
                 sleep=sleep)
        if r is None or r.status_code != 200:
            raise RuntimeError(f"Wikidata item lookup failed with HTTP {getattr(r, 'status_code', '?')}")
        q = r.json().get("query", {})
        hop = {n["from"]: n["to"] for n in q.get("normalized", [])}
        hop.update({n["from"]: n["to"] for n in q.get("redirects", [])})
        item = {p["title"]: (p.get("pageprops") or {}).get("wikibase_item") for p in q.get("pages", [])}
        for t in chunk:
            cur, seen = t, set()
            while cur in hop and cur not in seen:
                seen.add(cur)
                cur = hop[cur]
            if item.get(cur):
                out[by_title[t]] = item[cur]
    return out


def entities(sess, qids: list[str], props: str = "sitelinks", sleep=time.sleep) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(qids), 40):
        chunk = qids[i:i + 40]
        r = _get(sess, WIKIDATA, {"action": "wbgetentities", "format": "json", "ids": "|".join(chunk), "props": props,
                                  "languages": "en"}, sleep=sleep)
        if r is None or r.status_code != 200:
            raise RuntimeError(f"Wikidata entity read failed with HTTP {getattr(r, 'status_code', '?')}")
        out.update(r.json().get("entities", {}))
    return out


def sitelinks(entity: dict, langs=LANGS) -> dict[str, str]:
    links = entity.get("sitelinks") or {}
    return {lang: links[f"{lang}wiki"]["title"] for lang in langs if f"{lang}wiki" in links}


def redirects(sess, lang: str, title: str, cap: int = MAX_REDIRECTS, sleep=time.sleep) -> list[str]:
    out: list[str] = []
    params = {"action": "query", "format": "json", "formatversion": "2", "prop": "redirects", "titles": title,
              "rdlimit": "max", "rdnamespace": "0", "rdprop": "title"}
    while len(out) < cap:
        r = _get(sess, API.format(lang=lang), params, sleep=sleep)
        if r is None or r.status_code != 200:
            raise RuntimeError(f"redirects of {lang}:{title} failed with HTTP {getattr(r, 'status_code', '?')}")
        body = r.json()
        for p in body.get("query", {}).get("pages", []):
            out += [x["title"] for x in p.get("redirects", []) or []]
        cont = body.get("continue")
        if not cont:
            break
        params = {**params, **cont}
    return out[:cap]


def _views(sess, lang: str, title: str, grain: str, start: date, end: date, sleep=time.sleep) -> dict[str, int]:
    first = start.strftime("%Y%m%d00") if grain == "daily" else start.strftime("%Y%m0100")
    url = BASE.format(project=f"{lang}.wikipedia", title=quote(title.replace(" ", "_"), safe="")) + \
        f"/{grain}/{first}/{end.strftime('%Y%m%d00')}"
    r = _get(sess, url, sleep=sleep)
    if r is None or r.status_code == 404:
        return {}
    if r.status_code != 200:
        raise RuntimeError(f"{grain} views of {lang}:{title} failed with HTTP {r.status_code}")
    out = {}
    for it in r.json().get("items", []):
        ts = it["timestamp"]
        key = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}" if grain == "daily" else f"{ts[:4]}-{ts[4:6]}"
        out[key] = int(it["views"])
    return out


def counted_titles(article_monthly: dict[str, int], redirect_monthly: dict[str, dict[str, int]],
                   share: float = SHARE_MIN) -> list[str]:
    """Redirects whose total views reach `share` of the article's own: former names and common spellings."""
    base = sum(article_monthly.values())
    keep = []
    for t, series in redirect_monthly.items():
        total = sum(series.values())
        if total and total >= share * max(base, 1):
            keep.append(t)
    return sorted(keep, key=lambda t: -sum(redirect_monthly[t].values()))


def _due(scanned_at: str | None, today: date) -> bool:
    if not scanned_at:
        return True
    return (today - date.fromisoformat(scanned_at[:10])).days >= RESCAN_DAYS


def collect(sess, reg: registry.Registry, run: str, end: date | None = None, budget_s: float = 45 * 60,
            sleep=time.sleep, clock=time.monotonic) -> dict:
    """Resolve items and articles, scan redirects when due, refresh every daily series. Resumable."""
    end = end or (datetime.now(timezone.utc).date() - timedelta(days=1))
    today = end + timedelta(days=1)
    P = paths()
    st = store.read_state(P["state"])
    st.setdefault("articles", {})
    t0 = clock()
    failed: dict[str, str] = {}

    # The English titles the attention collector settled on; resolved afresh when it has not run.
    en_titles = (store.read_state(config.STATE_DIR / "attention.json").get("titles") or {})
    if not all(en_titles.get(h.id) for h in reg.houses):
        en_titles = {**attention.resolve_all(sess, reg)[0], **{k: v for k, v in en_titles.items() if v}}
    items = items_for(sess, en_titles, sleep=sleep)
    links = entities(sess, sorted(set(items.values())), "sitelinks", sleep=sleep)
    st["items"] = items
    st["unresolved"] = sorted(h.id for h in reg.houses if h.id not in items)

    # (house, lang) pairs, round-robin by language so every market fills before any gets its tail
    pairs = [(h.id, lang) for lang in LANGS for h in reg.houses if h.id in items]
    done, scanned, stopped = 0, 0, False
    for hid, lang in pairs:
        if clock() - t0 > budget_s:
            stopped = True
            break
        title = sitelinks(links.get(items[hid], {})).get(lang)
        key = f"{hid}:{lang}"
        rec = st["articles"].get(key) or {}
        if not title:
            st["articles"][key] = {"article": None}
            continue
        try:
            if rec.get("article") != title or _due(rec.get("scanned_at"), today):
                month_end = today.replace(day=1) - timedelta(days=1)
                own = _views(sess, lang, title, "monthly", START, month_end, sleep=sleep)
                sleep(PAUSE_S)
                reds = {}
                for t in redirects(sess, lang, title, sleep=sleep):
                    reds[t] = _views(sess, lang, t, "monthly", START, month_end, sleep=sleep)
                    sleep(PAUSE_S)
                rec = {"article": title, "counted": counted_titles(own, reds), "redirects_seen": len(reds),
                       "scanned_at": store.utc_now()}
                scanned += 1
            total: dict[str, int] = {}
            for t in [title] + rec["counted"]:
                for d, v in _views(sess, lang, t, "daily", START, end, sleep=sleep).items():
                    total[d] = total.get(d, 0) + v
                sleep(PAUSE_S)
            n_titles = 1 + len(rec["counted"])
            store.write_jsonl(P["dir"] / lang / f"{hid}.jsonl",
                              [{"date": d, "views": v, "titles": n_titles} for d, v in sorted(total.items())])
            rec.update({"days": len(total), "first": min(total) if total else None, "refreshed_to": end.isoformat()})
            st["articles"][key] = rec
            done += 1
        except (RuntimeError, requests.RequestException, ValueError, KeyError) as e:
            failed[key] = f"{e.__class__.__name__}: {str(e)[:200]}"
    st.update({"run": run, "updated_at": store.utc_now(), "end": end.isoformat(), "langs": list(LANGS),
               "failed": failed, "stopped_on_budget": stopped})
    store.write_state(P["state"], st)
    out = {"run": run, "updated_at": st["updated_at"], "end": st["end"], "series_written": done,
           "articles_scanned": scanned, "failed": len(failed), "stopped_on_budget": stopped,
           "unresolved": st["unresolved"]}
    store.append_jsonl(P["prov"], [out])
    return out


def load_series(house: str, lang: str) -> dict[date, int]:
    return {date.fromisoformat(r["date"]): int(r["views"])
            for r in store.read_jsonl(paths()["dir"] / lang / f"{house}.jsonl")}


def probe(run: str) -> list[str]:
    st = store.read_state(paths()["state"])
    if st.get("run") != run:
        return [f"no wikiviews run {run} on file"]
    errs = [f"{h}: no Wikidata item found" for h in st.get("unresolved", [])]
    errs += [f"{k}: {msg}" for k, msg in st.get("failed", {}).items()]
    arts = st.get("articles", {})
    en = [k for k, v in arts.items() if k.endswith(":en") and v.get("days")]
    if not en:
        errs.append("no English series written")
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.wikiviews")
    ap.add_argument("stage", choices=["collect", "probe"])
    ap.add_argument("--run", default=config.run_id())
    ap.add_argument("--budget-min", type=float, default=45)
    a = ap.parse_args(argv)
    if a.stage == "collect":
        try:
            out = collect(session(), registry.load(), a.run, budget_s=a.budget_min * 60)
        except Exception as e:   # leave a record whatever happens: the run's log is not always readable
            out = {"run": a.run, "updated_at": store.utc_now(), "crashed": f"{e.__class__.__name__}: {str(e)[:300]}"}
            store.append_jsonl(paths()["prov"], [out])
            print(f"::error::wikiviews crashed, recorded in provenance: {out['crashed']}")
            return 1
        print(out)
        return 0
    errs = probe(a.run)
    for e in errs:
        print(f"::warning::{e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
