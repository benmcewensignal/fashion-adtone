"""What an ad library's first year still lacks, for the hourly backfill runs: the houses never searched to the
end, and the ads whose made pictures wait to be read (Meta's dynamic and catalogue units are left out, and
so are ads whose picture addresses have lapsed: they wait for the next collection's).
Standard library only, so it runs before anything is installed; prints GitHub step outputs.

    python3 scripts/backfill_left.py <source>        -> go=yes|no, collect=yes|no, houses=a,b,c, read=yes|no

For Meta, whose vendor is paid per ad, the houses are cut to what the account's monthly limit still allows
at HOUSE_USD each, so that no house is started that the limit would stop half way (its ads paid for and
lost); the limit is read from the vendor with APIFY_TOKEN, and when it cannot be read nothing is cut.
"""
import json
import os
import urllib.request
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
DYNAMIC = ("DCO", "DPA")
OE = re.compile(r"[?&]oe=([0-9A-Fa-f]{6,12})(?=&|$)")     # as adtone.adlibs.lapses reads it


HOUSE_USD = 2.0       # a house's year of up to 2,000 ads and its page lookup: about $1.50 to $1.90 on 17 October


def vendor_room_usd() -> float | None:
    """What the vendor account's monthly limit still allows, in dollars; None when it cannot be read."""
    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        return None
    req = urllib.request.Request("https://api.apify.com/v2/users/me/limits", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = (json.loads(r.read().decode("utf-8")) or {}).get("data") or {}
        limit = float((d.get("limits") or {}).get("maxMonthlyUsageUsd"))
        used = float((d.get("current") or {}).get("monthlyUsageUsd"))
    except Exception:
        return None
    return limit - used


def lapsed(pr: dict, now: float) -> bool:
    ts = [int(m.group(1), 16) for m in (OE.search(u) for u in pr.get("urls") or []) if m]
    return bool(ts) and min(ts) < now + 600


def left(source: str, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    ids = re.findall(r"^\s*-\s*\{?\s*id:\s*([a-z0-9_]+)", (ROOT / "registry/houses.yml").read_text(encoding="utf-8"), re.M)
    state_path = ROOT / "data/state" / f"{source}.json"
    st = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    never = [h for h in ids if not (st.get("searched") or {}).get(h)]
    made = 0
    pend_dir = ROOT / "data" / source / "pending"
    for p in sorted(pend_dir.glob("*.jsonl")) if pend_dir.exists() else []:
        fmt = {}
        ads = ROOT / "data" / source / "ads" / p.name
        if ads.exists():
            for line in ads.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    fmt[r["ad_id"]] = r.get("display_format") or ""
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                pr = json.loads(line)
                if fmt.get(pr["ad_id"], "") not in DYNAMIC and not lapsed(pr, now):
                    made += 1
    room = vendor_room_usd() if source == "metaweb" else None
    if room is not None:
        never = never[:max(0, int(room // HOUSE_USD))]
    return {"houses": never, "made_pending": made, "room_usd": room}


def main(argv: list[str]) -> int:
    out = left(argv[1])
    collect = bool(out["houses"])
    read = out["made_pending"] > 0
    print(f"go={'yes' if collect or read else 'no'}")
    print(f"collect={'yes' if collect else 'no'}")
    print(f"houses={','.join(out['houses'])}")
    print(f"read={'yes' if read else 'no'}")
    print(f"made_pending={out['made_pending']}", file=sys.stderr)
    if out["room_usd"] is not None:
        print(f"room_usd={out['room_usd']:.2f}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
