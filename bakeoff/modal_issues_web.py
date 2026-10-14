"""Focal's house-issue pictures for the site: a read-only door onto the web copies the issues workflow made
(adtone/issues.py publish: /issues-v1/web on the private pictures volume), by exact name only. The site's build
fetches each picture named in data/issues/web.json through it, checks its hash and serves its own copy; nothing
else on the volume can be reached.

    modal deploy bakeoff/modal_issues_web.py
"""
import re

import modal

app = modal.App("adtone-issues-web")
image = modal.Image.debian_slim(python_version="3.12").uv_pip_install("fastapi[standard]==0.115.12")
pictures = modal.Volume.from_name("adtone-pictures")
NAME = re.compile(r"^[a-z_]+-[0-9a-f]{12}\.webp$")


@app.function(image=image, volumes={"/pictures": pictures}, timeout=60, max_containers=2, scaledown_window=60)
@modal.fastapi_endpoint(method="GET")
def pic(name: str = ""):
    from fastapi import HTTPException, Response
    if not NAME.match(name):
        raise HTTPException(status_code=404)
    try:
        with open(f"/pictures/issues-v1/web/{name}", "rb") as f:
            data = f.read()
    except FileNotFoundError:
        raise HTTPException(status_code=404) from None
    return Response(content=data, media_type="image/webp", headers={"Cache-Control": "public, max-age=31536000, immutable"})
