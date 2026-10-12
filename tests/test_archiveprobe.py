"""The archive probe: the view count a captured YouTube page shows, and each house's main Google account."""
from adtone import archiveprobe as P


def test_the_view_count_a_captured_watch_page_shows():
    html = 'var ytInitialPlayerResponse = {"videoDetails":{"videoId":"abc","viewCount":"123456","author":"Dior"}};'
    assert P.views_in(html) == 123456
    assert P.views_in("<html>no player</html>") is None and P.views_in(None) is None


def test_each_house_has_one_main_account():
    acc = P.main_accounts()
    assert acc["dior"] == "AR15269313249355497473" and acc["prada"] == "AR12455012082993070081"
    assert "zegna" not in acc and "miu_miu" not in acc                 # no account of its own, and doubt


def test_what_a_captured_data_call_was():
    got = P.call_of("https://adstransparency.google.com/anji/_/rpc/SearchService/SearchCreatives?authuser=0")
    assert got == {"call": "SearchService/SearchCreatives", "posted": False, "advertisers": []}
    import base64
    body = base64.b64encode(b'f.req={"1":{"2":"AR15269313249355497473"}}').decode().rstrip("=")
    got = P.call_of(f"https://adstransparency.google.com/anji/_/rpc/SearchService/SearchCreatives?__wb_method=post&__wb_post_data={body}")
    assert got["posted"] and got["advertisers"] == ["AR15269313249355497473"]


def test_house_accounts_include_the_doubtful_ones():
    acc = P.house_accounts()
    assert acc["AR15269313249355497473"] == "dior"
    assert len(acc) > len(P.main_accounts())
