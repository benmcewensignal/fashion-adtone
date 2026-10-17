"""Look at a public ad repository the way its own page does, and record how it works: the addresses the page
calls, the shape (keys and types, never values) of the JSON they return, the page's form controls and their
options, and whether a login wall or a block came up. For the repositories that have no documented API
(Pinterest, LinkedIn, Meta's library pages), one such look from the runner tells what a collector can read
and what it needs; nothing from any ad is stored.

    python -m adtone.explore --name pinterest --url "https://ads.pinterest.com/ads-repository/" [--scroll 3]
        [--type "input[name=q]=Gucci"] [--click "text=Search"] [--wait 8]
        [--do "select:#country=France" --do "type:#q=Gucci" --do "press:#q=Enter" --do "click:text=Search" --do "wait:6000"]

Writes data/state/explore_<name>.json. The look is made from the runner as it is, with no proxy and nothing
done to pass as a person: a repository that refuses the runner is recorded as refusing, and left alone.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from urllib.parse import parse_qsl, urlsplit

from . import adlibs, config, store

BLOCK_WORDS = ("log in to continue", "login to continue", "you must log in", "temporarily blocked", "unusual traffic",
               "verify you are human", "captcha", "access denied", "request blocked", "rate limit", "too many requests")


def explore(name: str, url: str, scroll: int = 2, wait_s: float = 8.0, types: list[str] = (), clicks: list[str] = (),
            actions: list[str] = (), headless: bool = True) -> dict:
    from playwright.sync_api import sync_playwright

    calls: list[dict] = []
    shapes: dict[str, dict] = {}
    statuses: dict[str, int] = {}
    out = {"name": name, "url": url, "at": store.utc_now()}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="en-GB")
        page = ctx.new_page()

        def on_response(resp):
            try:
                req = resp.request
                u = urlsplit(resp.url)
                key = f"{req.method} {u.scheme}://{u.netloc}{u.path}"
                statuses[key] = resp.status
                ctype = (resp.headers.get("content-type") or "").lower()
                entry = {"key": key, "status": resp.status, "type": req.resource_type, "content_type": ctype.split(";")[0],
                         "query_keys": sorted({k for k, _ in parse_qsl(u.query)})[:40]}
                for qk, qv in parse_qsl(u.query):
                    # a query value that is itself JSON (Pinterest's resource calls carry their options this way): its
                    # shape, and the addresses named in it, which say which resource the page asked for
                    if qv.startswith("{") and qk in ("data", "options", "variables", "params"):
                        try:
                            obj = json.loads(qv)
                        except ValueError:
                            continue
                        entry[f"{qk}_shape"] = adlibs.shape(obj)
                        urls = []

                        def walk(o, depth=0):
                            if depth > 5:
                                return
                            if isinstance(o, dict):
                                for k2, v2 in o.items():
                                    if isinstance(v2, str) and ("url" in str(k2).lower() or v2.startswith("/")) and len(v2) < 200:
                                        urls.append(f"{k2}={v2}")
                                    else:
                                        walk(v2, depth + 1)
                            elif isinstance(o, list):
                                for v2 in o:
                                    walk(v2, depth + 1)
                        walk(obj)
                        entry[f"{qk}_urls"] = urls[:12]
                if req.method == "POST" and (req.post_data or "").startswith("data="):
                    entry["post_data_shape"] = adlibs.shape(json.loads(dict(parse_qsl(req.post_data)).get("data", "{}")))
                if req.method == "POST":
                    body = req.post_data or ""
                    if body.startswith("{"):
                        try:
                            entry["post_json_shape"] = adlibs.shape(json.loads(body))
                        except ValueError:
                            pass
                    else:
                        entry["post_keys"] = sorted({k for k, _ in parse_qsl(body)})[:60]
                calls.append(entry)
                if ("json" in ctype or "javascript" in ctype and u.path.endswith(".json")) and resp.status == 200:
                    try:
                        data = resp.json()
                    except Exception:
                        data = None
                    if data is not None:
                        sh = adlibs.shape(data)
                        sub = key + " " + " ".join(entry.get("data_urls") or [])[:200]
                        if sub not in shapes and len(shapes) < 80:
                            shapes[sub] = sh
                        entry["response_shape_key"] = sub
            except Exception:
                pass

        page.on("response", on_response)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            out["goto_error"] = f"{e.__class__.__name__}: {str(e)[:200]}"
        page.wait_for_timeout(int(wait_s * 1000))
        for t in types:
            sel, _, text = t.partition("=")
            try:
                page.fill(sel, text, timeout=10000)
                page.wait_for_timeout(2500)
            except Exception as e:
                out.setdefault("action_errors", []).append(f"type {sel}: {e.__class__.__name__}: {str(e)[:120]}")
        for c in clicks:
            try:
                page.click(c, timeout=10000)
                page.wait_for_timeout(int(wait_s * 1000))
            except Exception as e:
                out.setdefault("action_errors", []).append(f"click {c}: {e.__class__.__name__}: {str(e)[:120]}")
        for step in actions:
            kind, _, rest = step.partition(":")
            try:
                if kind == "select":
                    sel, _, label = rest.partition("=")
                    page.select_option(sel, label=label, timeout=10000)
                elif kind == "type":
                    sel, _, text = rest.partition("=")
                    page.fill(sel, text, timeout=10000)
                elif kind == "press":
                    sel, _, key = rest.partition("=")
                    page.press(sel, key, timeout=10000)
                elif kind == "click":
                    page.click(rest, timeout=10000)
                elif kind == "wait":
                    page.wait_for_timeout(int(rest))
                elif kind == "scroll":
                    page.mouse.wheel(0, int(rest or 2400))
                else:
                    out.setdefault("action_errors", []).append(f"unknown step {step}")
                    continue
                page.wait_for_timeout(1500)
            except Exception as e:
                out.setdefault("action_errors", []).append(f"{step}: {e.__class__.__name__}: {str(e)[:160]}")
        for _ in range(scroll):
            page.mouse.wheel(0, 2400)
            page.wait_for_timeout(2500)
        try:
            out["title"] = page.title()
            out["final_url"] = page.url
            text = page.inner_text("body")[:200000].lower()
            out["body_chars"] = len(text)
            out["block_words"] = sorted({w for w in BLOCK_WORDS if w in text})
            out["login_wall"] = bool(re.search(r"\b(log in|sign in)\b", text[:3000])) and out["body_chars"] < 6000
            out["controls"] = page.evaluate("""() => {
                const out = [];
                for (const el of document.querySelectorAll('input, select, button, textarea, [role=combobox], [role=button], a[href*="?"]')) {
                    const o = {tag: el.tagName.toLowerCase(), type: el.type || null, name: el.name || null, id: el.id || null,
                               placeholder: el.placeholder || null, aria: el.getAttribute('aria-label'),
                               text: (el.innerText || el.value || '').trim().slice(0, 60) || null};
                    if (el.tagName === 'SELECT') o.options = Array.from(el.options).slice(0, 80).map(x => x.text.trim().slice(0, 40));
                    if (el.tagName === 'A') o.href = el.getAttribute('href').slice(0, 160);
                    out.push(o);
                }
                return out.slice(0, 250);
            }""")
            out["headings"] = page.evaluate("() => Array.from(document.querySelectorAll('h1,h2,h3')).slice(0,40).map(h => h.innerText.trim().slice(0,80))")
            out["text_tail"] = page.inner_text("body")[-700:]
            out["links_sample"] = page.evaluate("() => Array.from(document.querySelectorAll('a[href]')).map(a => a.getAttribute('href')).filter(h => h && h.length < 160).slice(0, 80)")
            out["api_mentions"] = page.evaluate("""() => Array.from(document.querySelectorAll('a[href]')).filter(a => /api|developer|docs/i.test(a.getAttribute('href') + ' ' + a.innerText)).slice(0, 20).map(a => a.innerText.trim().slice(0, 80) + ' -> ' + a.getAttribute('href').slice(0, 200))""")
            out["calls_by_key"] = sorted({c["key"] for c in calls if c["type"] in ("xhr", "fetch")})[:60]
            out["documents"] = [c for c in calls if c["type"] == "document"][:20]
            # the data a server-rendered page embeds for its scripts (Pinterest's __PWS_DATA__, Meta's data-sjs, Next's
            # __NEXT_DATA__): the shape of each, and the names and options of the resources it was rendered from
            embedded = page.evaluate("""() => Array.from(document.querySelectorAll('script[id], script[type="application/json"]')).map(s => ({id: s.id || null, type: s.type || null, text: (s.textContent || '').slice(0, 400000)}))""")
            emb = []
            for sc in embedded:
                t = (sc.get("text") or "").strip()
                if not t.startswith("{"):
                    continue
                try:
                    obj = json.loads(t)
                except ValueError:
                    continue
                item = {"id": sc.get("id"), "type": sc.get("type"), "top_keys": list(obj.keys())[:40] if isinstance(obj, dict) else "list",
                        "shape": adlibs.shape(obj) if len(t) < 300000 else "too large"}
                rr = None
                stack = [obj]
                while stack and rr is None:
                    cur = stack.pop()
                    if isinstance(cur, dict):
                        if "resourceResponses" in cur:
                            rr = cur["resourceResponses"]
                        else:
                            stack.extend(v for v in cur.values() if isinstance(v, (dict, list)))
                    elif isinstance(cur, list):
                        stack.extend(v for v in cur if isinstance(v, (dict, list)))
                if isinstance(rr, list):
                    item["resources"] = [{"name": r.get("name"), "options": adlibs.shape(r.get("options")),
                                          "option_strings": {k: v for k, v in (r.get("options") or {}).items() if isinstance(v, (str, int, bool)) and len(str(v)) < 60},
                                          "response_shape": adlibs.shape((r.get("response") or {}).get("data"))} for r in rr if isinstance(r, dict)][:30]
                emb.append(item)
            out["embedded"] = emb[:10]
        except Exception as e:
            out["read_error"] = f"{e.__class__.__name__}: {str(e)[:200]}"
        browser.close()
    seen = set()
    out["calls"] = [c for c in calls if not ((k := c["key"] + json.dumps(c.get("data_urls")) + json.dumps(c.get("post_data_shape"))) in seen or seen.add(k))][:200]
    out["json_shapes"] = shapes
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.explore")
    ap.add_argument("--name", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--scroll", type=int, default=2)
    ap.add_argument("--wait", type=float, default=8.0)
    ap.add_argument("--type", action="append", default=[], help="selector=text to type")
    ap.add_argument("--click", action="append", default=[], help="selector to click")
    ap.add_argument("--do", action="append", default=[], help="a step: select:<sel>=<label>, type:<sel>=<text>, press:<sel>=<key>, click:<sel>, wait:<ms>, scroll:<px>")
    ap.add_argument("--run", default=config.run_id())
    a = ap.parse_args(argv)
    t0 = time.monotonic()
    out = explore(a.name, a.url, a.scroll, a.wait, a.type, a.click, a.do)
    out["run"], out["seconds"] = a.run, round(time.monotonic() - t0, 1)
    path = config.STATE_DIR / f"explore_{a.name}.json"
    store.write_state(path, out)
    print(f"explore {a.name}: {len(out.get('calls', []))} calls, {len(out.get('json_shapes', {}))} JSON shapes, "
          f"login_wall={out.get('login_wall')} block_words={out.get('block_words')} -> {path.relative_to(config.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
