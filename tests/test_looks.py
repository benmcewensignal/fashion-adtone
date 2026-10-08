"""The runway looks probe: which archived pages of a house's own site are likely to carry a show's looks,
and whether the archive kept their pictures. No network: the archive is a stand-in."""
import json
import re

from adtone import looks as L


def _java_to_py(pattern: str) -> re.Pattern:
    return re.compile(pattern)


def test_the_season_is_found_in_the_ways_the_houses_write_it():
    rx = _java_to_py(L.season_pattern("SS", 2026))
    for u in ["https://www.chanel.com/gb/fashion/collection/spring-summer-2026/",
              "https://www.louisvuitton.com/eng-gb/magazine/articles/women-spring-summer-2026-show",
              "https://www.gucci.com/uk/en_gb/st/stories/runway/ss26-womenswear",
              "https://www.dior.com/fr_fr/mode/defile-printemps-ete-2026",
              "https://www.prada.com/ww/en/pradasphere/fashion-shows/SS26-women.html"]:
        assert rx.fullmatch(u), u
    for u in ["https://www.chanel.com/gb/fashion/collection/spring-summer-2025/",
              "https://www.gucci.com/uk/en_gb/pr/women/bags/ss2610",            # no season: the year runs on
              "https://www.dior.com/en_gb/fashion/womens-fashion/fall-winter-2026"]:
        assert not rx.fullmatch(u), u
    aw = _java_to_py(L.season_pattern("AW", 2025))
    assert aw.fullmatch("https://www.fendi.com/it-it/sfilata/autunno-inverno-2025")
    assert aw.fullmatch("https://www.ysl.com/en-gb/fw25-womens-show")


def test_show_pages_rank_above_shop_and_beauty_pages():
    show = "https://www.chanel.com/gb/fashion/collection/spring-summer-2026-ready-to-wear/"
    shop = "https://www.chanel.com/gb/fashion/shop/spring-summer-2026/"
    beauty = "https://www.chanel.com/gb/makeup/spring-summer-2026-collection/"
    product = "https://www.dior.com/en_gb/fashion/products/collection-2026-1234567"
    assert L.score(show) > 0 and L.score(shop) < 0 and L.score(beauty) < 0 and L.score(product) < L.score(show)
    rows = [["20251020000000", "https://www.chanel.com/fr/mode/collection/spring-summer-2026/"],
            ["20251101000000", "https://www.chanel.com/gb/fashion/collection/spring-summer-2026/"],
            ["20251005000000", "https://www.chanel.com/gb/fashion/collection/spring-summer-2026/"],
            ["20251015000000", "https://www.chanel.com/us/fashion/collection/spring-summer-2026/"],
            ["20251005000000", "https://www.chanel.com/gb/fashion/shop/spring-summer-2026/"]]
    got = L.candidates(rows)
    assert len(got) == 2                                   # one per address, whatever the country
    assert got[0]["url"].startswith("https://www.chanel.com/gb/") and got[0]["ts"] == "20251101000000"


class _R:
    def __init__(self, status=200, text="", ctype="text/html", content=b"", url=""):
        self.status_code, self.text, self.content, self.url = status, text, content, url
        self.headers = {"Content-Type": ctype}

    def json(self):
        return json.loads(self.text)


class _C:
    """The archive: an index, one page with a gallery, and its pictures."""
    calls = 0

    def __init__(self, gallery=40, kept=True, index_status=200):
        self.gallery, self.kept, self.index_status = gallery, kept, index_status

    def get(self, url, params=None):
        self.calls += 1
        if url == L.CDX:
            if self.index_status != 200:
                return _R(self.index_status, "busy")
            rows = [["timestamp", "original"],
                    ["20251010120000", "https://www.chanel.com/gb/fashion/collection/spring-summer-2026/"]]
            return _R(200, json.dumps(rows), "application/json")
        if "im_/" in url:
            return _R(200 if self.kept else 404, "", "image/jpeg" if self.kept else "text/html", b"x" * 5000)
        imgs = "".join(f'<img src="https://www.chanel.com/images/look-{i}.jpg">' for i in range(self.gallery))
        return _R(200, f"<html><title>Spring-Summer 2026</title><body>{imgs}</body></html>", url=url)


def test_a_house_whose_archived_gallery_holds_its_looks_is_found(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "OUT", tmp_path / "probe.json")
    monkeypatch.setattr(L, "PROV", tmp_path / "prov.jsonl")
    out = L.probe("2026 SS", ["chanel"], c=_C())
    h = out["houses"]["chanel"]
    assert h["verdict"] == "looks found" and h["pages"][0]["pictures"] == 40 and h["pages"][0]["kept"] == 3
    assert json.loads((tmp_path / "probe.json").read_text())["summary"] == {"looks found": 1}


def test_a_gallery_the_archive_did_not_keep_and_a_silent_index_are_told_apart(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "OUT", tmp_path / "probe.json")
    monkeypatch.setattr(L, "PROV", tmp_path / "prov.jsonl")
    lost = L.probe("2026 SS", ["chanel"], c=_C(kept=False))["houses"]["chanel"]
    assert lost["verdict"] == "pages found, few pictures kept"
    (tmp_path / "probe.json").unlink()                       # a finished house is not asked again otherwise
    thin = L.probe("2026 SS", ["chanel"], c=_C(gallery=4))["houses"]["chanel"]
    assert thin["verdict"] == "pages found, few pictures kept"
    (tmp_path / "probe.json").unlink()
    down = L.probe("2026 SS", ["chanel"], c=_C(index_status=503))["houses"]["chanel"]
    assert down["verdict"] == "the index did not answer"


def test_a_probe_cut_short_is_continued_and_finished_houses_are_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "OUT", tmp_path / "probe.json")
    monkeypatch.setattr(L, "PROV", tmp_path / "prov.jsonl")
    monkeypatch.setattr(L, "sites", lambda: {"chanel": ["chanel.com"], "dior": ["dior.com"]})
    (tmp_path / "probe.json").write_text(json.dumps({"season": "2026 SS", "houses": {
        "chanel": {"verdict": "looks found", "marker": 1}, "dior": {"verdict": "not reached in the time budget"}}}))
    c = _C()
    out = L.probe("2026 SS", c=c)
    assert out["houses"]["chanel"] == {"verdict": "looks found", "marker": 1}       # kept, not asked again
    assert out["houses"]["dior"]["verdict"] == "looks found" and out["summary"] == {"looks found": 2}


class _Index:
    """The archive's index: one house has a show page in the fortnight after its show, one has nothing,
    one does not answer at all."""
    calls = 0

    def get(self, url, params=None):
        self.calls += 1
        dom = params["url"]
        if dom == "dior.com":
            return _R(503, "busy")
        rows = [["timestamp", "original"]]
        if dom == "chanel.com" and params["from"] <= "20251010" <= params["to"]:
            rows.append(["20251010120000", "https://www.chanel.com/gb/fashion/collection/spring-summer-2026/"])
        return _R(200, json.dumps(rows), "application/json")


def test_coverage_searches_the_fortnight_after_each_show_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "COVERAGE_OUT", tmp_path / "coverage.json")
    monkeypatch.setattr(L, "COVERAGE_PROV", tmp_path / "prov.jsonl")
    monkeypatch.setattr(L, "sites", lambda: {"chanel": ["chanel.com"], "dior": ["dior.com"], "celine": ["celine.com"]})
    shows = [{"house": "chanel", "date": "2025-10-07", "season": "2026 SS rtw", "category": "rtw"},
             {"house": "chanel", "date": "2025-03-11", "season": "2025 AW rtw", "category": "rtw"},
             {"house": "dior", "date": "2025-10-01", "season": "2026 SS rtw", "category": "rtw"},
             {"house": "celine", "date": "2025-10-03", "season": "2026 SS rtw", "category": "rtw"}]
    monkeypatch.setattr(L, "shows_on_file", lambda: shows)
    out = L.coverage(c=_Index())
    s = out["shows"]
    assert s["chanel:2025-10-07:rtw"]["status"] == "found"
    assert s["chanel:2025-10-07:rtw"]["candidates"][0]["url"].endswith("spring-summer-2026/")
    assert s["chanel:2025-03-11:rtw"]["status"] == "none" and s["celine:2025-10-03:rtw"]["status"] == "none"
    assert s["dior:2025-10-01:rtw"]["status"] == "index failed"
    assert out["summary"]["by_house"]["chanel"] == {"shows": 2, "found": 1}
    again = _Index()
    L.coverage(c=again)
    assert again.calls == 4               # only dior is asked again: the whole window, then its three slices
