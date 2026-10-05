from PIL import Image

from adtone import media
from adtone.synth import FakeResponse, FakeSession, jpeg_bytes, render_page, toned_image

CREATIVE = "https://scontent-cdg4-1.xx.fbcdn.net/v/t39.35426-6/12345_n.jpg?stp=dst-jpg&oh=abc&oe=def"
CREATIVE2 = "https://scontent-cdg4-2.xx.fbcdn.net/v/t39.35426-6/67890_n.jpg?oh=1&oe=2"
AVATAR = "https://scontent-cdg4-1.xx.fbcdn.net/v/t39.30808-1/p50x50/avatar.jpg?oh=x"
EXTERNAL = "https://external-cdg4-1.xx.fbcdn.net/emg1/v/t13/preview.jpg?url=x"


def test_candidates_exclude_avatars_and_static_assets_and_order_creatives_first():
    html = render_page([EXTERNAL, CREATIVE, CREATIVE2], avatar=AVATAR)
    urls = media.candidate_urls(html)
    assert urls == [CREATIVE, CREATIVE2, EXTERNAL]


def test_candidates_found_inside_escaped_script_json():
    urls = media.candidate_urls(render_page([CREATIVE], escaped=True))
    assert urls == [CREATIVE]


def test_candidates_dedupe_by_path():
    other_sig = CREATIVE.replace("oh=abc", "oh=zzz")
    assert media.candidate_urls(render_page([CREATIVE, other_sig])) == [CREATIVE]


def test_download_drops_small_images_and_keeps_largest_distinct():
    big = jpeg_bytes(toned_image(0.7, size=(900, 1100), seed=1))
    mid = jpeg_bytes(toned_image(0.3, size=(600, 600), seed=2))
    tiny = jpeg_bytes(toned_image(0.5, size=(60, 60), seed=3))
    files = {"u/big": big, "u/mid": mid, "u/tiny": tiny, "u/dup": big, "u/bad": b"not an image"}
    s = FakeSession(lambda url, params: FakeResponse(200, content=files[url]))
    got = media.download(["u/tiny", "u/mid", "u/big", "u/dup", "u/bad"], session=s)
    assert [(g.w, g.h) for g in got] == [(900, 1100), (600, 600)]
    assert len({g.sha for g in got}) == 2


def _scene(kind: int) -> Image.Image:
    from PIL import ImageDraw
    im = Image.linear_gradient("L").resize((800, 1000)).convert("RGB")
    d = ImageDraw.Draw(im)
    if kind == 0:
        d.ellipse((200, 250, 600, 750), fill=(30, 30, 30))
    else:
        d.rectangle((0, 0, 400, 1000), fill=(240, 240, 240))
        d.rectangle((450, 100, 750, 300), fill=(10, 10, 10))
    return im


def test_phash_is_stable_under_resize_and_separates_different_images():
    a = _scene(0)
    a_small = a.resize((400, 500))
    b = _scene(1)
    ha, hs, hb = media.phash(a), media.phash(a_small), media.phash(b)
    assert len(ha) == 16
    assert media.hamming(ha, hs) <= 6
    assert media.hamming(ha, hb) > 6


def test_model_jpeg_respects_the_edge_limit():
    data = media.jpeg_for_model(toned_image(0.5, size=(3000, 2000)))
    im = Image.open(__import__("io").BytesIO(data))
    assert max(im.size) == 1568


def test_render_url_is_built_from_the_id():
    assert media.render_url("42", "T") == "https://www.facebook.com/ads/archive/render_ad/?id=42&access_token=T"


def test_named_snapshot_fields_win_and_the_avatar_is_dropped():
    blob = ('{"snapshot":{"page_profile_picture_url":"https:\\/\\/scontent-a.xx.fbcdn.net\\/v\\/t39.30808-1\\/av.jpg?oh=1",'
            '"cards":[{"original_image_url":"https:\\/\\/scontent-a.xx.fbcdn.net\\/v\\/t39.35426-6\\/c1_n.jpg?oh=2",'
            '"resized_image_url":"https:\\/\\/scontent-a.xx.fbcdn.net\\/v\\/t39.35426-6\\/c1_n.jpg?stp=s600x600&oh=3"},'
            '{"original_image_url":"https:\\/\\/scontent-a.xx.fbcdn.net\\/v\\/t39.35426-6\\/c2_n.jpg?oh=4"}],'
            '"videos":[{"video_preview_image_url":"https:\\/\\/scontent-a.xx.fbcdn.net\\/v\\/t39.35426-6\\/v1_n.jpg?oh=5"}]}}')
    urls = media.candidate_urls(f"<html><script>var d = {blob};</script></html>")
    assert [u.split("?")[0].rsplit("/", 1)[1] for u in urls] == ["c1_n.jpg", "c2_n.jpg", "v1_n.jpg"]


def test_resized_copies_are_used_only_when_no_full_size_image_is_named():
    blob = '{"resized_image_url":"https://scontent-a.xx.fbcdn.net/v/t39.35426-6/r_n.jpg?stp=s600x600"}'
    assert media.candidate_urls(blob) == ["https://scontent-a.xx.fbcdn.net/v/t39.35426-6/r_n.jpg?stp=s600x600"]
