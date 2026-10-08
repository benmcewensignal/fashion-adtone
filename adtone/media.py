"""Turn an ad id into the images it showed.

The Ad Library API returns no media, only a render page. Two resolvers read it:

  static   fetch the render page's HTML and pull image URLs out of it (default, cheap)
  browser  render the page in headless Chromium and read the images it actually shows

Neither could be run against Meta from the build environment. If the static resolver
finds nothing on the first live run, the completeness probe fails the run, and the
answer is to set ADTONE_RESOLVER=browser.

Images live in memory only. Nothing here writes pixels to disk.
"""
from __future__ import annotations

import hashlib
import http.client
import io
import re
from dataclasses import dataclass

import numpy as np
import requests
import urllib3
from PIL import Image, UnidentifiedImageError

from . import config
from .adlib import scrub

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
_FBCDN = re.compile(r"https://(?:scontent|external)[\w.-]*\.fbcdn\.net/[^\"'\s<>\\)]+")
_SKIP = ("rsrc.php", "/p50x50/", "/s60x60/", "/p60x60/", "/s100x100/", "emoji", "static.xx.fbcdn")
# Field names in the snapshot data Meta embeds in its Ad Library pages, as seen in the output of
# commercial Ad Library scrapers. Creative images and video stills are named; so is the avatar.
_KEYED = re.compile(r'"(original_image_url|video_preview_image_url|resized_image_url|page_profile_picture_url)"'
                    r'\s*:\s*"(https://[^"]+)"')


class MediaError(Exception):
    pass


@dataclass
class FetchedImage:
    sha: str
    phash: str
    w: int
    h: int
    image: Image.Image


def render_url(ad_id: str, token: str) -> str:
    return config.SNAPSHOT_URL.format(ad_id=ad_id, token=token)


def _dedupe(urls: list[str], drop: set[str]) -> list[str]:
    out, seen = [], set()
    for u in urls:
        p = u.split("?", 1)[0]
        if p not in seen and p not in drop:
            seen.add(p)
            out.append(u)
    return out


def candidate_urls(html: str) -> list[str]:
    """Image URLs in a render page.

    Named snapshot fields come first: full-size creatives and video stills, then the 600px
    resized copies only if no full-size image is named. The page avatar is always dropped.
    If no named fields exist, every fbcdn image URL is considered, creatives before link
    previews, with known avatar and icon paths skipped.
    """
    text = (html.replace("\\/", "/").replace("\\u0025", "%").replace("&amp;", "&")
            .replace('\\"', '"'))
    keyed: dict[str, list[str]] = {}
    for m in _KEYED.finditer(text):
        keyed.setdefault(m.group(1), []).append(m.group(2))
    avatars = {u.split("?", 1)[0] for u in keyed.get("page_profile_picture_url", [])}
    named = _dedupe(keyed.get("original_image_url", []) + keyed.get("video_preview_image_url", []), avatars)
    if named:
        return named
    if keyed.get("resized_image_url"):
        return _dedupe(keyed["resized_image_url"], avatars)
    seen_paths: set[str] = set(avatars)
    primary, secondary = [], []
    for m in _FBCDN.finditer(text):
        url = m.group(0).rstrip(".,;")
        if any(s in url for s in _SKIP):
            continue
        path = url.split("?", 1)[0]
        if path in seen_paths or re.search(r"\.(mp4|m3u8|js|css)$", path, re.I):
            continue
        seen_paths.add(path)
        (secondary if "//external" in url else primary).append(url)
    return primary + secondary


class StaticResolver:
    name = "static"

    def __init__(self, token: str, session: requests.Session | None = None):
        self.token = token
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", UA)

    def resolve(self, ad_id: str) -> list[str]:
        try:
            r = self.session.get(render_url(ad_id, self.token), timeout=45)
        except requests.RequestException as e:
            raise MediaError(scrub(f"render page: {e}")) from None
        if r.status_code != 200:
            raise MediaError(f"render page HTTP {r.status_code}")
        return candidate_urls(r.text)

    def close(self) -> None:
        pass


class BrowserResolver:
    """Headless Chromium. Needs `pip install playwright && playwright install --with-deps chromium`."""
    name = "browser"

    def __init__(self, token: str):
        from playwright.sync_api import sync_playwright
        self.token = token
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch()

    def resolve(self, ad_id: str) -> list[str]:
        page = self._browser.new_page(viewport={"width": 1200, "height": 1800}, user_agent=UA)
        try:
            page.goto(render_url(ad_id, self.token), wait_until="networkidle", timeout=45_000)
            srcs = page.eval_on_selector_all(
                "img", f"els => els.filter(e => e.naturalWidth >= {config.MIN_IMAGE_SIDE} && "
                       f"e.naturalHeight >= {config.MIN_IMAGE_SIDE}).map(e => e.currentSrc || e.src)")
            posters = page.eval_on_selector_all("video", "els => els.map(e => e.poster).filter(Boolean)")
        except Exception as e:  # playwright raises its own error types
            raise MediaError(scrub(f"browser render: {e}")) from None
        finally:
            page.close()
        out, seen = [], set()
        for u in list(srcs) + list(posters):
            p = u.split("?", 1)[0]
            if u.startswith("http") and p not in seen:
                seen.add(p)
                out.append(u)
        return out

    def close(self) -> None:
        self._browser.close()
        self._pw.stop()


class AutoResolver:
    """Static first; headless Chromium only for ads the static reader cannot resolve.

    The browser starts the first time it is needed. If Playwright is not installed the
    resolver carries on static-only and the probe reports the gap.
    """
    name = "auto"

    def __init__(self, token: str, session: requests.Session | None = None, browser_factory=None):
        self.static = StaticResolver(token, session)
        self._factory = browser_factory or (lambda: BrowserResolver(token))
        self._browser = None
        self._browser_unavailable = False
        self.used = {"static": 0, "browser": 0}

    def resolve(self, ad_id: str) -> list[str]:
        try:
            urls = self.static.resolve(ad_id)
        except MediaError:
            urls = []
        if urls:
            self.used["static"] += 1
            return urls
        if self._browser is None and not self._browser_unavailable:
            try:
                self._browser = self._factory()
            except Exception:  # Playwright missing or Chromium failed to start
                self._browser_unavailable = True
        if self._browser is None:
            return []
        self.used["browser"] += 1
        return self._browser.resolve(ad_id)

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()


def phash(img: Image.Image, hash_size: int = 8, factor: int = 4) -> str:
    """64-bit DCT perceptual hash as 16 hex characters."""
    n = hash_size * factor
    a = np.asarray(img.convert("L").resize((n, n), Image.Resampling.LANCZOS), dtype=np.float64)
    k = np.arange(n)
    c = np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n))
    c[0, :] *= 1 / np.sqrt(2)
    c *= np.sqrt(2 / n)
    low = (c @ a @ c.T)[:hash_size, :hash_size]
    bits = (low > np.median(low)).flatten()
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def open_image(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        raise MediaError(f"not an image: {e}") from None
    return img.convert("RGB")


def to_fetched(data: bytes) -> FetchedImage:
    img = open_image(data)
    return FetchedImage(sha=hashlib.sha256(data).hexdigest(), phash=phash(img), w=img.width, h=img.height, image=img)


def download(urls: list[str], session: requests.Session | None = None, min_side: int = config.MIN_IMAGE_SIDE,
             max_candidates: int = config.MAX_CANDIDATES, keep: int = config.KEEP_IMAGES_PER_AD) -> list[FetchedImage]:
    """Fetch candidates, drop small ones (avatars, icons), keep the largest distinct images."""
    s = session or requests.Session()
    s.headers.setdefault("User-Agent", UA)
    got: dict[str, FetchedImage] = {}
    for url in urls[:max_candidates]:
        try:
            r = s.get(url, timeout=45, stream=True)
            if r.status_code != 200:
                continue
            data = r.raw.read(config.MAX_IMAGE_BYTES + 1, decode_content=True) if hasattr(r, "raw") and r.raw else r.content
            if len(data) > config.MAX_IMAGE_BYTES:
                continue
            fi = to_fetched(data)
        except (requests.RequestException, MediaError, urllib3.exceptions.HTTPError, http.client.HTTPException):
            continue          # a picture cut off mid-transfer is skipped, not allowed to stop the run
        if min(fi.w, fi.h) >= min_side:
            got.setdefault(fi.sha, fi)
    return sorted(got.values(), key=lambda f: f.w * f.h, reverse=True)[:keep]


def jpeg_for_model(img: Image.Image, max_edge: int = config.MODEL_MAX_EDGE) -> bytes:
    im = img.copy()
    im.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    return buf.getvalue()
