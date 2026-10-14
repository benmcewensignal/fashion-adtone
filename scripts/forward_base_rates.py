"""Base rates for forward/designer-change-v1.md: the four calls, computed by the frozen code
(adtone/forward_image.py) on every earlier change of creative lead whose windows had closed.

    python scripts/forward_base_rates.py [last month of a closed after window, default 2026-06]

Calls 1, 2 and 4 need the after window (months 3 to 14 after the first show) closed by that month; call 3
needs only its own 12 months from the first show closed by September 2026. Writes
forward/designer-change-v1-base-rates.json.
"""
import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from adtone import forward_image as F  # noqa: E402

LAST_POST = sys.argv[1] if len(sys.argv) > 1 else "2026-06"
LAST_CREW = "2026-09"
rows = list(csv.DictReader((ROOT / "reference" / "designers.csv").open(encoding="utf-8")))
pictures, credit_rows, moves = F.load_pictures(), F.load_credits(), F.lead_months()


def origin_of(r):
    """The house Focal reads that the designer led just before, if they left it within 180 days of this announcement."""
    ann = F._date(r["announced_date"]) or F._date(r["first_show_date"])
    best = None
    for o in rows:
        if (o["designer"] == r["designer"] and o["house_id"] != r["house_id"] and o["house_id"] in pictures
                and o["left_announced_date"]):
            left = F._date(o["left_announced_date"])
            if left <= ann and (best is None or left > best[1]):
                best = (o["house_id"], left)
    return best if best and (ann - best[1]).days <= F.ORIGIN_LEFT_DAYS else None


named = [r for r in rows if r["scope"] in F.SCOPES and len(r["first_show_date"]) >= 10
         and r["designer"].strip().lower() != "design studio" and r["first_show_date"] >= "2015-01-01"
         and r["house_id"] in pictures]
results = []
for r in sorted(named, key=lambda r: (r["first_show_date"], r["house_id"])):
    d = F._date(r["first_show_date"])
    _, post = F.windows_for(d)
    image_closed = post[1] <= LAST_POST
    crew_closed = F.shift(F.month_of(d), F.CREW_AFTER - 1) <= LAST_CREW
    if not (image_closed or crew_closed):
        continue
    o = origin_of(r)
    e = {"house": r["house_id"], "designer": r["designer"], "announced": r["announced_date"] or None,
         "first_show": r["first_show_date"], "previous_house": o[0] if o else None,
         "previous_left_announced": o[1].isoformat() if o else None}
    res = F.compute(e, pictures, credit_rows, moves)
    if not image_closed:
        for k in ("magnitude", "itself", "transfer"):
            res[k] = {"status": "window_open"}
    if not crew_closed:
        res["crew"] = {"status": "window_open"}
    results.append({"event": e, **res})


def rate(k):
    xs = [r[k]["hit"] for r in results if "hit" in r[k]]
    return {"tested": len(xs), "hits": int(sum(xs))}


summary = {k: rate(k) for k in F.CALLS}
try:
    data_commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
except OSError:
    data_commit = None
out = {"made_by": "scripts/forward_base_rates.py", "code_sha256": F.file_sha256(ROOT / "adtone" / "forward_image.py"),
       "data_commit": data_commit, "last_closed_after_window": LAST_POST, "last_closed_crew_window": LAST_CREW,
       "summary": summary, "events": results}
(ROOT / "forward" / "designer-change-v1-base-rates.json").write_text(json.dumps(out, indent=1, default=str) + "\n")
print(json.dumps(summary))
