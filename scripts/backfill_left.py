"""What an ad library's first year still lacks, for the hourly backfill runs: the houses never searched to the
end, and the ads whose made pictures wait to be read (Meta's dynamic and catalogue units are left out).
Standard library only, so it runs before anything is installed; prints GitHub step outputs.

    python3 scripts/backfill_left.py <source>        -> go=yes|no, collect=yes|no, houses=a,b,c, read=yes|no
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DYNAMIC = ("DCO", "DPA")


def left(source: str) -> dict:
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
            if line.strip() and fmt.get(json.loads(line)["ad_id"], "") not in DYNAMIC:
                made += 1
    stopped = str(st.get("stopped") or "")
    capped = "usage hard limit" in stopped or "credit is used up" in stopped
    return {"houses": never, "made_pending": made, "capped": capped}


def main(argv: list[str]) -> int:
    out = left(argv[1])
    collect = bool(out["houses"])
    read = out["made_pending"] > 0
    print(f"go={'yes' if collect or read else 'no'}")
    print(f"collect={'yes' if collect else 'no'}")
    print(f"houses={','.join(out['houses'])}")
    print(f"read={'yes' if read else 'no'}")
    print(f"made_pending={out['made_pending']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
