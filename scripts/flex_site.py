"""Builds the flex section of the site (adtone/flex.py) from data/flex and the kept pictures, opened from the
flex-sealed branch into a folder outside the repository: the image layers as WebP, written to <out_dir> for
upload with the deployment, the data the page reads, and the section put into www/index.html between its
markers. Pictures never go into the repository.

    python3 scripts/flex_site.py <kept_png_dir> <out_dir> [--quality 78] [--edge 832] [--full]

The base picture is the start of both directions; each step is a patch over the picture before it, cut to
where the two differ. Every picture is cut into strips of a few kilobytes, each a file of its own, so they
can be uploaded one by one.
"""
import argparse
import io
import json

import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from adtone import flex as F  # noqa: E402

HERE = Path(__file__).resolve().parent / "flex_site"
NAMES = [("garments", "Garments"), ("accessories", "Accessories"), ("colour_main", "Main colour"),
         ("colour_second", "Second colour"), ("pattern", "Pattern"), ("skin_shown", "Skin shown"),
         ("hemline", "Hemline"), ("layers", "Layers"), ("silhouette", "Silhouette"), ("construction", "Construction"),
         ("finishing", "Finishing"), ("materials", "Materials"), ("formality", "Formality"),
         ("street_couture_axis", "Street to couture")]
LABELS = {"shirt_blouse": "shirt or blouse", "white_ivory": "white or ivory", "beige_camel": "beige or camel",
          "yellow_gold": "yellow or gold", "logo_letters": "logo or letters", "above_knee": "above the knee",
          "knee": "at the knee", "midi": "midi", "ankle_floor": "ankle or floor", "three_or_more": "three or more",
          "soft_cut": "soft cut", "sheer_lace": "sheer or lace", "satin_silk": "satin or silk",
          "plain_woven": "plain woven", "not_distinguishable": "can't tell", "formal_tailored": "formal tailored",
          "sport_street": "sport or street", "not_visible": "not visible", "not_applicable": "not applicable",
          "1": "1 of 5", "2": "2 of 5", "3": "3 of 5", "4": "4 of 5", "5": "5 of 5"}
COUNT = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
         10: "ten", 11: "eleven", 12: "twelve"}


def answers_of(reading: dict, use, options) -> dict:
    out = {}
    for q, _ in NAMES:
        if q not in use or reading is None or q not in reading:
            continue
        v = reading[q]
        if isinstance(v, list):
            out[q] = [x for x in v if x in options.get(q, v)]
        else:
            out[q] = str(v) if q == "street_couture_axis" else v
    return out


def webp(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=quality, method=6)
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kept")
    ap.add_argument("out")
    ap.add_argument("--quality", type=int, default=78)
    ap.add_argument("--edge", type=int, default=832, help="width of the pictures on the site")
    ap.add_argument("--full", action="store_true", help="a whole picture for every step, not patches")
    ap.add_argument("--max-bytes", type=int, default=4600, help="each picture file under this size, cut into strips")
    ap.add_argument("--alts", default=str(HERE / "alts.json"))
    a = ap.parse_args()
    kept, out = Path(a.kept), Path(a.out)
    (out / "flex").mkdir(parents=True, exist_ok=True)
    plan = json.loads(F.PLAN.read_text())
    frames = json.loads(F.FRAMES.read_text())
    use, options = F.standing()
    alts = json.loads(Path(a.alts).read_text()) if Path(a.alts).exists() else {}

    def chosen(arm, sid):
        rec = frames["arms"][arm][sid]
        c = next(c for c in rec["candidates"] if c["name"] == rec["chosen"])
        return c

    zero = chosen("prep", plan["prep"][-1]["id"]) if plan.get("prep") else None
    seq = [(0, "start", zero["name"], zero["reading"], None)]
    for arm, sign in (("decorated", -1), ("plainer", 1)):
        for i, st in enumerate(plan["arms"][arm], 1):
            c = chosen(arm, st["id"])
            seq.append((sign * i, st["id"], c["name"], c["reading"], st.get("what")))
    seq.sort()
    scale = a.edge / F.SIZE[0]
    size = (a.edge, round(F.SIZE[1] * scale))
    pics = {k: Image.open(kept / f"{name}.png").convert("RGB") for k, _, name, _, _ in seq}
    files, layers = {}, []

    def cut(img, box, prefix, k):
        """A region as horizontal strips each under the byte limit, each overlapping the next by two rows so no
        hairline shows where they meet."""
        x0, y0, x1, y1 = box
        region = img.crop(box)
        n = 1
        while True:
            edges = [round(i * (y1 - y0) / n) for i in range(n + 1)]
            parts = []
            for i in range(n):
                top, bottom = edges[i], min(y1 - y0, edges[i + 1] + (2 if i < n - 1 else 0))
                parts.append((top, bottom, webp(region.crop((0, top, x1 - x0, bottom)), a.quality)))
            if all(len(d) <= a.max_bytes for _, _, d in parts) or n >= 60:
                break
            n += 1
        for i, (top, bottom, data) in enumerate(parts):
            name = f"flex/{prefix}-{i + 1}.webp" if n > 1 else f"flex/{prefix}.webp"
            files[name] = data
            layers.append({"k": k, "src": name, "box": [x0, y0 + top, x1, y0 + bottom]})

    full0 = pics[0].resize(size, Image.Resampling.LANCZOS)
    cut(full0, (0, 0, size[0], size[1]), "look", 0)
    for k, sid, name, _, _ in seq:
        if k == 0:
            continue
        cur = pics[k]
        full = cur.resize(size, Image.Resampling.LANCZOS)
        if a.full:
            cut(full, (0, 0, size[0], size[1]), sid, k)
            continue
        prev = pics[k - (1 if k > 0 else -1)]
        d = np.abs(np.asarray(cur, dtype=np.int16) - np.asarray(prev, dtype=np.int16)).max(axis=2) > 2
        ys, xs = np.nonzero(d)
        pad = 12
        x0, x1 = max(0, xs.min() - pad), min(F.SIZE[0], xs.max() + 1 + pad)
        y0, y1 = max(0, ys.min() - pad), min(F.SIZE[1], ys.max() + 1 + pad)
        # whole site pixels, so a patch lies exactly over the picture below it
        s0 = [int(np.floor(v * scale)) for v in (x0, y0)]
        s1 = [min(lim, int(np.ceil(v * scale))) for v, lim in ((x1, size[0]), (y1, size[1]))]
        cut(full, (s0[0], s0[1], s1[0], s1[1]), sid, k)
    for p, data in files.items():
        (out / p).write_bytes(data)
    total = sum(len(v) for v in files.values())
    print(f"{len(files)} pictures, {total / 1024:.0f} KB, site size {size}")
    for p, data in sorted(files.items()):
        print(f"  {p} {len(data) / 1024:.1f} KB")

    # the page's data
    shares = F.shares(write=False)
    houses = [h["house"] for h in plan["houses"]]
    fx_frames, keys = [], set()
    for k, sid, name, reading, what in seq:
        ans = answers_of(reading, use, options)
        for q, v in ans.items():
            for x in (v if isinstance(v, list) else [v]):
                keys.add(f"{q}+{x}" if isinstance(v, list) else f"{q}={x}")
        n = abs(k)
        if k == 0:
            label = "As drawn"
        else:
            side = "More decorated" if k < 0 else "Plainer and darker"
            label = f"{side}, {n} of 5: {what}"
        fx_frames.append({"k": k, "id": sid, "label": label, "alt": alts.get(sid, label), "a": ans})
    # answers taken away also carry their shares
    sh = {}
    for kk in sorted(keys):
        row = []
        for h in houses:
            got = shares["houses"][h]["shares"].get(kk)
            if got is None and "=" in kk and kk.split("=")[0] == "street_couture_axis":
                got = shares["houses"][h]["shares"].get(f"street_couture_axis={int(kk.split('=')[1])}")
            row.append(got)
        sh[kk] = row
    fx = {"q": NAMES, "lists": ["garments", "accessories", "finishing", "materials"], "labels": LABELS,
          "shares": sh, "frames": fx_frames}
    hd = {h: shares["houses"][h] for h in houses}

    # how often the reader read each step's change as meant, over every version drawn
    met = drawn = 0
    for arm in ("decorated", "plainer"):
        for st in plan["arms"][arm]:
            for c in frames["arms"][arm][st["id"]]["candidates"]:
                drawn += 1
                met += int(st["headline"] in c["met"])
    spec = {h["house"]: h for h in plan["houses"]}
    years = lambda h: (min(r["date"][:4] for r in F.house_answers(spec[h])), max(r["date"][:4] for r in F.house_answers(spec[h])))
    dy, by = years("dior"), years("balenciaga")
    note_h = (f"The shares are the part of each house's runway looks given that answer by the same reader: "
              f"Dior's ready-to-wear under {hd['dior']['designer']}, {hd['dior']['looks']} looks from "
              f"{COUNT.get(hd['dior']['shows'], hd['dior']['shows'])} shows of {dy[0]} to {dy[1]}, and Balenciaga's under "
              f"{hd['balenciaga']['designer']}, {hd['balenciaga']['looks']} looks from {COUNT.get(hd['balenciaga']['shows'], hd['balenciaga']['shows'])} shows of "
              f"{by[0]} to {by[1]}. Like every description of the clothes, they are provisional until people have checked them.")
    note_m = (f"The pictures were made with open image models, Qwen-Image and Qwen-Image-Edit, each change held to its "
              f"own part of the picture so that nothing else moves. Each step was drawn three or four times and read; the "
              f"reader saw the change in {met} of the {drawn} versions, and the one shown is the version whose answers moved "
              f"most as meant and least otherwise. The other answers that change are the reader's own response to the new picture.")
    imgs = []
    for L in sorted(layers, key=lambda L: (L["k"] > 0, abs(L["k"]))):     # each step painted over the one before it
        x0, y0, x1, y1 = L["box"]
        style = (f"left:{100 * x0 / size[0]:.4f}%;top:{100 * y0 / size[1]:.4f}%;"
                 f"width:{100 * (x1 - x0) / size[0]:.4f}%")
        if L["k"] == 0:
            imgs.append(f'              <img src="{L["src"]}" style="{style}" width="{x1 - x0}" height="{y1 - y0}" alt="">')
        else:
            imgs.append(f'              <img class="fx-patch" data-k="{L["k"]}" src="{L["src"]}" style="{style}" '
                        f'width="{x1 - x0}" height="{y1 - y0}" alt="" hidden>')
    html = (HERE / "fx.html").read_text()
    zero_f = next(f for f in fx_frames if f["k"] == 0)
    html = (html.replace("@@IMGS@@", "\n".join(imgs)).replace("@@ALT0@@", zero_f["alt"])
            .replace("@@LABEL0@@", zero_f["label"]).replace("@@NOTE_HOUSES@@", note_h).replace("@@NOTE_METHOD@@", note_m))
    css = (HERE / "fx.css").read_text()
    js = (HERE / "fx.js").read_text().replace("/*FX*/null/*FX*/", "/*FX*/" + json.dumps(fx, separators=(",", ":")) + "/*FX*/")
    for s in (html, note_h, note_m, js):
        assert "—" not in s and "–" not in s, "a dash crept in"
    page = (REPO / "www" / "index.html").read_text()
    page = put(page, "  /*FX-CSS*/\n", "  /*FX-CSS-END*/\n", css, before="</style>")
    page = put(page, "        <!--FX-->\n", "        <!--FX-END-->\n", html,
               before='        <div class="narrow">\n          <p class="sub" style="margin-top:4rem">Read as luxury</p>')
    page = put(page, "<script>/*FX-JS*/\n", "</script>\n", js, before="</body>")
    (REPO / "www" / "index.html").write_text(page)
    (out / "fx-data.json").write_text(json.dumps({"fx": fx, "met": met, "drawn": drawn, "layers": layers}, indent=1))
    print("index.html", len(page.encode()), "bytes;", f"read as meant in {met} of {drawn}")


def put(page: str, start: str, end: str, body: str, before: str) -> str:
    """The section between its markers, or placed before `before` the first time."""
    block = start + body + ("" if body.endswith("\n") else "\n") + end
    if start in page:
        i = page.index(start)
        j = page.index(end, i) + len(end)
        return page[:i] + block + page[j:]
    k = page.index(before)
    return page[:k] + block + page[k:]


if __name__ == "__main__":
    main()
