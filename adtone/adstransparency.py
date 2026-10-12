"""Google's ads: the houses' ads on YouTube, Search and Display, from the Ads Transparency Center's public
BigQuery dataset (bigquery-public-data.google_ads_transparency_center).

    python -m adtone.adstransparency auto --run <id>        # discover until the accounts are confirmed, then collect
    python -m adtone.adstransparency discover --run <id> [--max-gb 50]
    python -m adtone.adstransparency collect --run <id> [--max-gb 50]
    python -m adtone.adstransparency probe --run <id>

What the dataset holds, from Google's README (storage.googleapis.com/ads-transparency-center/api-data/README.txt,
read 12 October 2026): every ad shown in the European Economic Area or Turkey, with impressions reported from
1 March 2023, one row an ad: the advertiser (disclosed and legal names, location, verification), the ad's
format (text, image or video), a link to its page on the Transparency Center, and for each region the first
and last day it was shown and a range for how often. It holds no pictures and no video: the creative is seen
only on the ad's own page, which Focal does not harvest. So this source gives the advertising stage its clock
and its weight in Europe, not what the ads look like.

Access is keyless: the workflow signs in through Workload Identity Federation (GitHub's own token exchanged
for the focal-ads service account's), and queries run in that account's project. BigQuery bills by the
bytes a query reads, the first terabyte a month free, so every query is measured first (a dry run) and
refused above --max-gb, and runs with that ceiling set so BigQuery itself stops it beyond.

discover: the table's size, rows, clustering and schema, and every advertiser whose disclosed or legal name
carries a house's name, with its count of ads by format; each is matched to its house or refused, and the
reason kept, for checking by eye before collect is trusted.
collect: for the advertiser accounts confirmed in reference/google_advertisers.csv (discover's matches, checked
by eye: a name can carry a house's by chance, as "defending" carries "fendi"), every ad with its regions, first
and last days and impression ranges.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import unicodedata
from pathlib import Path

from . import config, registry, store

TABLE = "bigquery-public-data.google_ads_transparency_center.creative_stats"
CONFIRMED = config.ROOT / "reference" / "google_advertisers.csv"
NOT_THE_HOUSE = ("beaute", "beauty", "parfum", "fragrance", "cosmetic", "loreal", "coty", "esteelauder",
                 "interparfums", "shiseido", "outlet", "vintage", "resale", "secondhand", "preloved", "kering eyewear",
                 "eyewear", "optic", "optik", "lunett")
MAX_GB = 50.0


def paths() -> dict[str, Path]:
    b = config.DATA / "adstransparency"
    return {"dir": b, "advertisers": b / "advertisers.jsonl", "creatives": b / "creatives", "table": b / "table.json",
            "state": config.STATE_DIR / "adstransparency.json", "prov": config.PROV_DIR / "adstransparency.jsonl"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def house_names(h: registry.House) -> list[str]:
    """The names a house's advertiser account would carry, normalised; Dior's legal name is Christian Dior
    Couture, Hermès's is Hermès Sellier and Saint Laurent's Yves Saint Laurent, so containment is enough."""
    return sorted({norm(n) for n in [h.name] + list(h.search_terms or []) if len(norm(n)) >= 4})


def name_pattern(reg: registry.Registry) -> str:
    """One regular expression over lower-cased, accent-stripped names, for BigQuery's REGEXP_CONTAINS."""
    alts = sorted({n for h in reg.houses for n in house_names(h)})
    return "|".join(re.escape(a) for a in alts)


def match(reg: registry.Registry, disclosed: str, legal: str) -> tuple[str | None, str]:
    """The house an advertiser account is, or None and why not. The name the ads carry decides when it names one
    house (Miu Miu advertises as Miu Miu through Prada S.p.A.), else the legal name; a licensee or reseller is
    refused whichever name carries the house's."""
    names = [norm(disclosed), norm(legal)]
    if any(w.replace(" ", "") in n for n in names for w in NOT_THE_HOUSE):
        return None, "licensee or reseller by name"
    for which, n in (("disclosed name", names[0]), ("legal name", names[1])):
        hits = sorted({h.id for h in reg.houses for hn in house_names(h) if hn in n})
        if len(hits) == 1:
            return hits[0], which
        if len(hits) > 1:
            return None, f"{which} names several houses: " + ", ".join(hits)
    return None, "no house's name"


SQL_NAMES = r"""
-- every advertiser whose name carries a house's: its names, place, verification and ads by format
SELECT advertiser_id,
       ANY_VALUE(advertiser_disclosed_name) AS disclosed,
       ANY_VALUE(advertiser_legal_name) AS legal,
       ANY_VALUE(advertiser_location) AS location,
       ANY_VALUE(advertiser_verification_status) AS verification,
       COUNT(*) AS ads,
       COUNTIF(ad_format_type = 'VIDEO') AS video,
       COUNTIF(ad_format_type = 'IMAGE') AS image,
       COUNTIF(ad_format_type = 'TEXT') AS text,
       ARRAY_AGG(DISTINCT IFNULL(ad_format_type, '')) AS formats
FROM `{table}`
WHERE REGEXP_CONTAINS(REGEXP_REPLACE(LOWER(NORMALIZE(CONCAT(IFNULL(advertiser_disclosed_name, ''), ' ',
                                                            IFNULL(advertiser_legal_name, '')), NFD)),
                                     r'[^a-z0-9]', ''), r'{pattern}')
GROUP BY advertiser_id
"""

SQL_ADS = r"""
-- every ad of the matched advertisers, with each region's first and last day and impression range
SELECT advertiser_id, creative_id, ad_format_type, creative_page_url, topic,
       ARRAY(SELECT AS STRUCT region_code, first_shown, last_shown, times_shown_lower_bound,
                              times_shown_upper_bound, times_shown_start_date, times_shown_end_date
             FROM UNNEST(region_stats)) AS regions
FROM `{table}`
WHERE advertiser_id IN UNNEST(@ids)
"""


def client():
    from google.cloud import bigquery  # requirements-ads.txt; imported here so the rest of Focal never needs it
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
    sa = os.environ.get("GCP_SERVICE_ACCOUNT", "")
    if not project and "@" in sa:
        project = sa.split("@", 1)[1].split(".", 1)[0]
    return bigquery, bigquery.Client(project=project)


def measured(bq, cl, sql: str, max_gb: float, params=None) -> tuple[int, object | None]:
    """A dry run first, for the bytes the query would read; the query runs only under the ceiling, and with it set."""
    cfg = bq.QueryJobConfig(dry_run=True, use_query_cache=False, query_parameters=params or [])
    nbytes = int(cl.query(sql, job_config=cfg).total_bytes_processed or 0)
    if nbytes > max_gb * 1e9:
        return nbytes, None
    run = bq.QueryJobConfig(maximum_bytes_billed=int(max_gb * 1e9), query_parameters=params or [])
    return nbytes, cl.query(sql, job_config=run).result()


def _day(v) -> str | None:
    return v.isoformat()[:10] if v is not None and hasattr(v, "isoformat") else (str(v)[:10] if v else None)


def discover(reg: registry.Registry, run: str, max_gb: float = MAX_GB) -> dict:
    bq, cl = client()
    P = paths()
    t = cl.get_table(TABLE)                                     # metadata only: free
    table = {"table": TABLE, "bytes": t.num_bytes, "rows": t.num_rows, "modified": _day(t.modified),
             "clustering": t.clustering_fields, "partitioning": (t.time_partitioning.field if t.time_partitioning else None),
             "schema": [f.to_api_repr() for f in t.schema], "read_at": store.utc_now(), "run": run}
    store.write_state(P["table"], table)
    sql = SQL_NAMES.format(table=TABLE, pattern=name_pattern(reg))
    nbytes, rows = measured(bq, cl, sql, max_gb)
    out = {"run": run, "table_gb": round((t.num_bytes or 0) / 1e9, 1), "rows": t.num_rows,
           "names_query_gb": round(nbytes / 1e9, 2), "max_gb": max_gb}
    if rows is None:
        out["stopped"] = f"the name query would read {nbytes / 1e9:.1f} GB, above {max_gb:g}"
        return out
    found = []
    for r in rows:
        house, why = match(reg, r["disclosed"] or "", r["legal"] or "")
        found.append({"advertiser_id": r["advertiser_id"], "disclosed": r["disclosed"], "legal": r["legal"],
                      "location": r["location"], "verification": r["verification"], "ads": r["ads"],
                      "video": r["video"], "image": r["image"], "text": r["text"], "formats": list(r["formats"] or []),
                      "house": house, "why": why,
                      "run": run, "found_at": store.utc_now()[:10]})
    found.sort(key=lambda x: (x["house"] or "~", -x["ads"]))
    store.write_jsonl(P["advertisers"], found)
    ids = [x["advertiser_id"] for x in found if x["house"]]
    out.update({"advertisers": len(found), "matched": len(ids),
                "houses": sorted({x["house"] for x in found if x["house"]})})
    if ids:                                                      # what collect would cost, measured now
        p = [bq.ArrayQueryParameter("ids", "STRING", ids)]
        try:
            out["ads_query_gb"] = round(int(cl.query(SQL_ADS.format(table=TABLE), job_config=bq.QueryJobConfig(
                dry_run=True, use_query_cache=False, query_parameters=p)).total_bytes_processed or 0) / 1e9, 2)
        except Exception as e:                                   # the schema differs from the README: kept, fixed by hand
            out["ads_query_error"] = f"{type(e).__name__}: {str(e)[:300]}"
    return out


def confirmed() -> dict[str, str]:
    """advertiser id -> house, from the checked list; empty until it exists."""
    if not CONFIRMED.exists():
        return {}
    with CONFIRMED.open(encoding="utf-8", newline="") as f:
        return {r["advertiser_id"].strip(): r["house"].strip() for r in csv.DictReader(f)
                if r.get("advertiser_id", "").strip() and r.get("house", "").strip()}


def collect(reg: registry.Registry, run: str, max_gb: float = MAX_GB) -> dict:
    bq, cl = client()
    P = paths()
    house_of = confirmed()
    if not house_of:
        return {"run": run, "stopped": "no confirmed advertisers: reference/google_advertisers.csv is empty or missing"}
    p = [bq.ArrayQueryParameter("ids", "STRING", sorted(house_of))]
    nbytes, rows = measured(bq, cl, SQL_ADS.format(table=TABLE), max_gb, p)
    out = {"run": run, "ads_query_gb": round(nbytes / 1e9, 2), "max_gb": max_gb}
    if rows is None:
        out["stopped"] = f"the ads query would read {nbytes / 1e9:.1f} GB, above {max_gb:g}"
        return out
    by = {}
    for r in rows:
        regions = [{"region": g["region_code"], "first": _day(g["first_shown"]), "last": _day(g["last_shown"]),
                    "lo": g["times_shown_lower_bound"], "hi": g["times_shown_upper_bound"],
                    "from": _day(g["times_shown_start_date"]), "to": _day(g["times_shown_end_date"])}
                   for g in (r["regions"] or [])]
        firsts = [g["first"] for g in regions if g["first"]]
        lasts = [g["last"] for g in regions if g["last"]]
        h = house_of[r["advertiser_id"]]
        by.setdefault(h, []).append({"creative_id": r["creative_id"], "advertiser_id": r["advertiser_id"], "house_id": h,
                                     "format": r["ad_format_type"], "page": r["creative_page_url"], "topic": r["topic"],
                                     "first": min(firsts) if firsts else None, "last": max(lasts) if lasts else None,
                                     "regions": regions})
    P["creatives"].mkdir(parents=True, exist_ok=True)
    for h, xs in by.items():
        xs.sort(key=lambda x: (x["first"] or "", x["creative_id"]))
        store.write_jsonl(P["creatives"] / f"{h}.jsonl", xs)
    out.update({"houses": len(by), "ads": sum(len(v) for v in by.values()),
                "by_house": {h: len(v) for h, v in sorted(by.items())}})
    return out


def probe(run: str) -> list[str]:
    st = store.read_state(paths()["state"])
    if st.get("run") != run:
        return [f"no adstransparency run {run} on file"]
    return [st["stopped"]] if st.get("stopped") else []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.adstransparency")
    ap.add_argument("stage", choices=["auto", "discover", "collect", "probe"])
    ap.add_argument("--run", default=config.run_id())
    ap.add_argument("--max-gb", type=float, default=MAX_GB)
    a = ap.parse_args(argv)
    P = paths()
    if a.stage == "probe":
        for e in probe(a.run):
            print(f"::warning::{e}")
        return 0
    if not os.environ.get("GCP_SERVICE_ACCOUNT"):
        store.append_jsonl(P["prov"], [{"run": a.run, "updated_at": store.utc_now(), "waiting": "no GCP_SERVICE_ACCOUNT"}])
        print("::notice::adstransparency: no Google Cloud sign-in yet; nothing queried")
        return 0
    reg = registry.load()
    stage = a.stage if a.stage != "auto" else ("collect" if confirmed() else "discover")
    try:
        out = (discover if stage == "discover" else collect)(reg, a.run, a.max_gb)
    except Exception as e:                                       # recorded, never a token or a key: the message only
        out = {"run": a.run, "stopped": f"{type(e).__name__}: {str(e)[:400]}"}
    out.update({"stage": stage, "updated_at": store.utc_now()})
    store.write_state(P["state"], out)
    store.append_jsonl(P["prov"], [out])
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
