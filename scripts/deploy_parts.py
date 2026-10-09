"""Prepare the site for a Vercel deployment made file by file through the API.

    python3 scripts/deploy_parts.py <out_dir> [--pictures <dir of flex/*.webp>] [--max-bytes 4500]

The page goes up gzipped and cut into parts of a few kilobytes, so each part can be uploaded on its own and
checked against its SHA-1; a one-line build in `vercel.json` joins and unzips them into `public/index.html`.
Everything else is served from `public/`. `private.js` and `eighteen-questions.webp` are listed by SHA-1 and
left out of upload.txt, as Vercel already holds them; a changed one has to be uploaded again. Writes to <out_dir>:

    gz/NN.part      the parts
    upload.txt      for each part and picture: name, size, SHA-1, then its base64 in lines of 1,000
    files.json      the deployment's file list, every file by SHA-1 and size except vercel.json
    vercel.json     the build

The pictures are never in the repository; pass the folder that holds them outside it.
"""
import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WWW = ROOT / "www"
STATIC = ["private.js", "eighteen-questions.webp"]
BUILD = ("node -e 'const f=require(`fs`),z=require(`zlib`);f.mkdirSync(`public`,{recursive:true});"
         "f.writeFileSync(`public/index.html`,z.gunzipSync(Buffer.concat(f.readdirSync(`gz`).sort()"
         ".map(n=>f.readFileSync(`gz/`+n)))))'")


def sha1(b):
    return hashlib.sha1(b).hexdigest()


def parts(page, max_bytes):
    """The page gzipped without a name or time, so the same page always gives the same parts."""
    gz = gzip.compress(page, compresslevel=9, mtime=0)
    assert gzip.decompress(gz) == page
    return [gz[i:i + max_bytes] for i in range(0, len(gz), max_bytes)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--pictures", help="folder holding the slider's flex/*.webp, outside the repository")
    ap.add_argument("--max-bytes", type=int, default=4500)
    a = ap.parse_args()
    out = Path(a.out)
    (out / "gz").mkdir(parents=True, exist_ok=True)
    page = (WWW / "index.html").read_bytes()
    vercel = json.dumps({"buildCommand": BUILD, "outputDirectory": "public"}, separators=(",", ":")) + "\n"
    (out / "vercel.json").write_text(vercel)
    files = [{"file": "vercel.json", "data": vercel, "encoding": "utf-8"}]
    upload = []

    def add(path, data, publish=True):
        files.append({"file": path, "sha": sha1(data), "size": len(data)})
        if publish:
            b = base64.b64encode(data).decode()
            upload.append(f"=== {path} size {len(data)} sha1 {sha1(data)}")
            upload.extend(b[i:i + 1000] for i in range(0, len(b), 1000))

    for i, p in enumerate(parts(page, a.max_bytes)):
        (out / "gz" / f"{i:02d}.part").write_bytes(p)
        add(f"gz/{i:02d}.part", p)
    for name in STATIC:
        add(f"public/{name}", (WWW / name).read_bytes(), publish=False)
    if a.pictures:
        pics = Path(a.pictures)
        for f in sorted(pics.glob("*.webp")):
            add(f"public/flex/{f.name}", f.read_bytes())
    (out / "upload.txt").write_text("\n".join(upload) + "\n")
    (out / "files.json").write_text(json.dumps(files, separators=(",", ":")))
    print(f"page {len(page)} bytes, sha1 {sha1(page)}; {sum(1 for f in files if f['file'].startswith('gz/'))} parts;"
          f" {len(files)} files")


if __name__ == "__main__":
    main()
