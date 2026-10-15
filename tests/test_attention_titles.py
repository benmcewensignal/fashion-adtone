"""The English articles: asked for in requests the API accepts, and the first good candidate kept."""
from adtone import attention


class _Resp:
    def __init__(self, body):
        self.status_code, self._body = 200, body

    def json(self):
        return self._body


class _Wiki:
    """Answers like the API: more than 50 titles is refused whole; a redirect is followed; one page is a
    disambiguation page and one is missing."""
    def __init__(self):
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        titles = params["titles"].split("|")
        if len(titles) > 50:
            return _Resp({"error": {"code": "toomanyvalues", "info": "The limit is 50."}})
        redirects = [{"from": t, "to": t + " (moved)"} for t in titles if t.startswith("Moved")]
        pages = []
        for t in titles:
            if t.startswith("Moved"):
                pages.append({"title": t + " (moved)"})
            elif t.startswith("Gone"):
                pages.append({"title": t, "missing": True})
            elif t == "Many things":
                pages.append({"title": t, "pageprops": {"disambiguation": ""}})
            else:
                pages.append({"title": t})
        return _Resp({"query": {"redirects": redirects, "pages": pages}})


def test_seventy_houses_are_resolved_in_requests_the_api_accepts():
    wanted = {f"h{i}": [f"Gone {i}", f"House {i}"] for i in range(35)}
    wanted |= {"moved": ["Moved house"], "many": ["Many things", "Many things (brand)"]}
    sess = _Wiki()
    got = attention.resolve(sess, wanted)
    assert sess.calls == 2                       # 73 titles, in two requests of at most 40
    assert got["h0"] == "House 0" and got["h34"] == "House 34"
    assert got["moved"] == "Moved house (moved)"
    assert got["many"] == "Many things (brand)"  # a disambiguation page is passed over


def test_a_refused_lookup_says_why_rather_than_finding_nothing():
    class Refuses(_Wiki):
        def get(self, url, params=None, timeout=None):
            return _Resp({"error": {"code": "ratelimited", "info": "slow down"}})
    try:
        attention.resolve(Refuses(), {"a": ["A"]})
    except RuntimeError as e:
        assert "ratelimited" in str(e)
    else:
        raise AssertionError("a refused lookup must not pass for an empty one")
