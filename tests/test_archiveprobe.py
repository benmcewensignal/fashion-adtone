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
