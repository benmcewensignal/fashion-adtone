"""Google's ads: the houses' ads on YouTube, Search, Shopping, Maps and Play, from the Ads Transparency Center's
public BigQuery dataset (bigquery-public-data.google_ads_transparency_center).

    python -m adtone.adstransparency auto --run <id>        # discover when due, then collect
    python -m adtone.adstransparency discover --run <id> [--max-gb 50]
    python -m adtone.adstransparency collect --run <id> [--max-gb 150]
    python -m adtone.adstransparency probe --run <id>

What the dataset holds, from Google's README (storage.googleapis.com/ads-transparency-center/api-data/README.txt,
read 12 October 2026) and the table's schema: every ad shown in the European Economic Area or Turkey, one row an
ad: the advertiser (disclosed and legal names, location, verification), the ad's format (text, image or video),
and for each region the first and last day it was shown and a range for how often, overall and by surface
(Search, YouTube, Shopping, Maps, Play), counted from 1 March 2023; an ad first shown before then reports
1 March 2023 as its first day. It holds no pictures, no video and no words: the creative is seen only on the ad's
own page (adstransparency.google.com/advertiser/<advertiser id>/creative/<creative id>), which Focal does not
harvest. So this source gives the advertising stage its clock and its weight in Europe, not what the ads look like.

Access is keyless: the workflow signs in through Workload Identity Federation (GitHub's own token exchanged
for the focal-ads service account's), and queries run in that account's project. BigQuery bills by the
bytes a query reads, the first terabyte a month free; the table is neither clustered nor partitioned, so a
query reads whole columns whatever its filter. Every query is measured first (a dry run), refused above its
ceiling, and runs with that ceiling set so BigQuery itself stops it beyond.

discover: the table's size, rows and schema, and every advertiser whose disclosed or legal name carries a
house's name. Only the accounts whose name is the house's own company (its name, perhaps a country, and a
company form, by the rules in OWN) are kept, with their names; the rest, people and businesses that share a
name ("defending" carries "fendi", and many people are called Chloé), are counted by house and never named.
A group's own accounts (Kering, LVMH, OTB...) are kept as well, marked as the group's, since a house's ads
may run through its group's.
collect: for the accounts in reference/google_advertisers.csv (discover's, checked by eye; an account named
only "Chanel" and never verified is marked doubt, not house), every ad: its format, first and last day shown,
each country's days and impression range, the impressions summed over countries and by surface, and how its
audience was chosen. Written by house and by the year the ad was first shown, a whole snapshot each time.
auto: discover when the matching rules changed or four weeks have passed, then collect.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path

from . import config, registry, store

TABLE = "bigquery-public-data.google_ads_transparency_center.creative_stats"
CONFIRMED = config.ROOT / "reference" / "google_advertisers.csv"
NOT_THE_HOUSE = ("beaute", "beauty", "parfum", "fragrance", "cosmetic", "loreal", "coty", "esteelauder",
                 "interparfums", "shiseido", "outlet", "vintage", "resale", "secondhand", "preloved", "kering eyewear",
                 "eyewear", "optic", "optik", "lunett")
MAX_GB = {"discover": 50.0, "collect": 150.0}     # the names query read 14.8 GB; the ads query about 62
RULES_VERSION = 2
REDISCOVER_DAYS = 28

# The houses' own companies, on names lower-cased with accents and punctuation dropped: the house's name,
# perhaps a country or a known arm, and a company form. Hermès alone also needs its accent, or the legal name
# Hermès Sellier (or Hermès of Paris): the parcel firm Hermes Germany carries the name without it.
FORMS = (r"(sa|sas|s a|s a s|sarl|srl|s r l|spa|s p a|sl|s l|ltd|limited|inc|corp|corporation|gmbh|ges m b h|b v|bv|nv|"
         r"ab|as|aps|oy|ag|kft|s r o|sp z o o|lda|unipessoal lda|pty ltd|pty limited|llc|co ltd|fzco|pte ltd|sdn bhd)")
OWN = {h: re.compile(p) for h, p in {
    "alaia": rf"^(azzedine )?alaia( {FORMS})?$",
    "balenciaga": rf"^(house of )?balenciaga( {FORMS})?$",
    "bottega_veneta": rf"^bottega veneta( {FORMS})?$",
    "brunello_cucinelli": rf"^brunello cucinelli( {FORMS})?$",
    "burberry": rf"^burberry( latin america)?( {FORMS})?$",
    "celine": rf"^celine( {FORMS})?$",
    "chanel": rf"^chanel( (international|denmark|australia|italia|france|germany|deutschland|espana|nederland|belgium|"
              rf"schweiz|suisse|austria|polska|uk))?( {FORMS})?$",
    "chloe": rf"^chloe( {FORMS})?$",
    "dior": rf"^christian dior( couture)?( (italia|espana|germany|deutschland|uk|australia|nederland|belgique|suisse|"
            rf"schweiz))?( {FORMS})?$",
    "dolce_gabbana": rf"^dolce gabbana( uk)?( {FORMS})?$",
    "dries_van_noten": rf"^dries van noten( {FORMS})?$",
    "fendi": rf"^fendi( (italia|germany|france|retail spain|uk|canada|north america))?( {FORMS})?$",
    "givenchy": rf"^givenchy( {FORMS})?$",
    "gucci": rf"^(it )?(guccio )?gucci( (america|australia))?( {FORMS})?$",
    "hermes": rf"^hermes( sellier| of paris| (gb|italie|iberica|benelux nordics|international|suisse|schweiz|monte carlo|"
              rf"prague))?( {FORMS})?$",
    "jil_sander": rf"^jil sander( (usa|japan))?( {FORMS})?$",
    "loewe": rf"^loewe( {FORMS})$",                  # Loewe alone is also a German surname and a TV maker
    "loro_piana": rf"^loro piana( (g b|c))?( {FORMS})?$",
    "louis_vuitton": r"^(societe des magasins )?louis vuitton\b.*$",
    "margiela": rf"^(maison )?margiela( {FORMS})?$",
    "max_mara": rf"^max ?mara( (usa|fashion group|japan))?( {FORMS})?$",
    "miu_miu": r"^miu ?miu$",
    "prada": rf"^prada( (usa|canada|middle east|asia pacific|singapore|australia))?( {FORMS})?$",
    "saint_laurent": rf"^yves saint laurent( {FORMS})?$",
    "tom_ford": rf"^tom ford( (distribution|retail uk|retail))?( {FORMS})?$",
    "valentino": rf"^valentino( {FORMS})$",          # Mario Valentino, the shoemaker, is another house
    "versace": rf"^(gianni versace( {FORMS})?|versace us( {FORMS})?)$",
    "zegna": rf"^(ermenegildo )?zegna( (group|holditalia|usa|uk))?( {FORMS})?$",
}.items()}
NOT_FASHION = ("parfum", "perfume", "beaut", "fondazione", "fundacion", "foundation", "fondation")
EXTRA_NAMES = {"margiela": ["margiela"]}          # the company may drop the "Maison"
GROUPS = re.compile(rf"^(kering|lvmh|richemont|puig|otb|only the brave|capri holdings|luxury goods (italia|international))"
                    rf"( (group|italia|france|deutschland|germany|espana|uk|international|europe|benelux|nordics|suisse|"
                    rf"schweiz|austria|polska|fashion group|moet hennessy louis vuitton))?( {FORMS})?$")
GROUP_NAMES = ["kering", "lvmh", "richemont", "puig", "otbspa", "otbgroup", "onlythebrave", "capriholdings",
               "luxurygoods"]
COUNTRY = re.compile(r"^[A-Z]{2}$")


def paths() -> dict[str, Path]:
    b = config.DATA / "adstransparency"
    return {"dir": b, "advertisers": b / "advertisers.jsonl", "creatives": b / "creatives", "table": b / "table.json",
            "state": config.STATE_DIR / "adstransparency.json", "prov": config.PROV_DIR / "adstransparency.jsonl"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def plain(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def house_names(h: registry.House) -> list[str]:
    """The names a house's advertiser account would carry, normalised; Dior's legal name is Christian Dior
    Couture, Hermès's is Hermès Sellier and Saint Laurent's Yves Saint Laurent, so containment is enough."""
    return sorted({norm(n) for n in [h.name] + list(h.search_terms or []) + EXTRA_NAMES.get(h.id, [])
                   if len(norm(n)) >= 4})


def name_pattern(reg: registry.Registry) -> str:
    """One regular expression over lower-cased, accent-stripped names, for BigQuery's REGEXP_CONTAINS."""
    alts = sorted({n for h in reg.houses for n in house_names(h)} | set(GROUP_NAMES))
    return "|".join(re.escape(a) for a in alts)


def match(reg: registry.Registry, disclosed: str, legal: str) -> tuple[str | None, str]:
    """The house whose name an account's names carry, or None and why not: the loose reading, for counting what
    discover refused. The name the ads carry decides when it names one house (Miu Miu advertises as Miu Miu
    through Prada S.p.A.), else the legal name; a licensee or reseller is refused whichever name carries it."""
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


def own_house(disclosed: str, legal: str) -> str | None:
    """The house whose own company an account is, by OWN: the disclosed name first, then the legal name."""
    raw = f"{disclosed or ''} {legal or ''}"
    names = [plain(disclosed), plain(legal)]
    if any(w in n for n in names for w in NOT_FASHION):
        return None
    for n in names:
        hits = [h for h, pat in OWN.items() if n and pat.match(n)]
        if len(hits) == 1:
            h = hits[0]
            if h == "hermes" and not ("è" in raw.lower() or plain(legal or disclosed).startswith(("hermes sellier",
                                                                                                  "hermes of paris"))):
                return None
            return h
    return None


def group_of(disclosed: str, legal: str) -> str | None:
    for n in (plain(disclosed), plain(legal)):
        m = GROUPS.match(n) if n and "eyewear" not in n else None
        if m:
            return m.group(1)
    return None


def status_of(disclosed: str, legal: str, verification: str | None) -> str:
    """house: verified by Google, or named as a company; doubt: a bare name nobody verified (a Chanel or a Celine
    may be a person)."""
    if (verification or "").upper().startswith("VERIFIED"):
        return "house"
    return "house" if any(re.search(rf" {FORMS}$", plain(n)) for n in (disclosed, legal) if n) else "doubt"


def sift(reg: registry.Registry, rows) -> tuple[list[dict], dict[str, int]]:
    """discover's rows split: the houses' and groups' own accounts kept, with their names; the rest counted by
    the house whose name they carry, never named."""
    kept, refused = [], Counter()
    for r in rows:
        d, l = r["disclosed"] or "", r["legal"] or ""
        h = own_house(d, l)
        g = None if h else group_of(d, l)
        if h or g:
            kept.append({"advertiser_id": r["advertiser_id"], "disclosed": d, "legal": l, "location": r["location"],
                         "verification": r["verification"], "ads": r["ads"], "video": r["video"], "image": r["image"],
                         "text": r["text"], "house": h, "group": g, "status": status_of(d, l, r["verification"])})
        else:
            refused[match(reg, d, l)[0] or "other"] += 1
    kept.sort(key=lambda x: (x["house"] or "~", x["group"] or "", -x["ads"], x["advertiser_id"]))
    return kept, dict(sorted(refused.items()))


SQL_NAMES = r"""
-- every advertiser whose name carries a house's or a group's: its names, place, verification and ads by format
SELECT advertiser_id,
       ANY_VALUE(advertiser_disclosed_name) AS disclosed,
       ANY_VALUE(advertiser_legal_name) AS legal,
       ANY_VALUE(advertiser_location) AS location,
       ANY_VALUE(advertiser_verification_status) AS verification,
       COUNT(*) AS ads,
       COUNTIF(ad_format_type = 'VIDEO') AS video,
       COUNTIF(ad_format_type = 'IMAGE') AS image,
       COUNTIF(ad_format_type = 'TEXT') AS text
FROM `{table}`
WHERE REGEXP_CONTAINS(REGEXP_REPLACE(LOWER(NORMALIZE(CONCAT(IFNULL(advertiser_disclosed_name, ''), ' ',
                                                            IFNULL(advertiser_legal_name, '')), NFD)),
                                     r'[^a-z0-9]', ''), r'{pattern}')
GROUP BY advertiser_id
"""

SQL_ADS = r"""
-- every ad of the confirmed accounts: each region's days and impression range, and its impressions by surface
SELECT c.advertiser_id, c.creative_id, c.ad_format_type, c.topic, c.ad_funded_by,
       c.audience_selection_approach_info AS aud,
       ARRAY(SELECT AS STRUCT g.region_code, g.first_shown, g.last_shown, g.times_shown_lower_bound AS lo,
                              g.times_shown_upper_bound AS hi, g.times_shown_start_date AS since,
                              g.times_shown_end_date AS till,
                              ARRAY(SELECT AS STRUCT s.surface, s.times_shown_lower_bound AS lo,
                                                     s.times_shown_upper_bound AS hi
                                    FROM UNNEST(g.surface_serving_stats.surface_serving_stats) AS s) AS surfaces
             FROM UNNEST(c.region_stats) AS g) AS regions
FROM `{table}` AS c
WHERE c.advertiser_id IN UNNEST(@ids)
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


def discover(reg: registry.Registry, run: str, max_gb: float = MAX_GB["discover"]) -> dict:
    bq, cl = client()
    P = paths()
    t = cl.get_table(TABLE)                                     # metadata only: free
    table = {"table": TABLE, "bytes": t.num_bytes, "rows": t.num_rows, "modified": _day(t.modified),
             "clustering": t.clustering_fields, "partitioning": (t.time_partitioning.field if t.time_partitioning else None),
             "schema": [f.to_api_repr() for f in t.schema], "read_at": store.utc_now(), "run": run}
    sql = SQL_NAMES.format(table=TABLE, pattern=name_pattern(reg))
    nbytes, rows = measured(bq, cl, sql, max_gb)
    out = {"run": run, "table_gb": round((t.num_bytes or 0) / 1e9, 1), "rows": t.num_rows,
           "names_query_gb": round(nbytes / 1e9, 2), "max_gb": max_gb}
    if rows is None:
        out["stopped"] = f"the name query would read {nbytes / 1e9:.1f} GB, above {max_gb:g}"
        return out
    kept, refused = sift(reg, [dict(r.items()) for r in rows])
    for k in kept:
        k.update({"run": run, "found_at": store.utc_now()[:10]})
    store.write_jsonl(P["advertisers"], kept)
    table["rules"] = RULES_VERSION                              # written last: a stopped discovery stays due
    store.write_state(P["table"], table)
    listed = confirmed()
    out.update({"kept": len(kept), "refused": refused,
                "houses": dict(Counter(k["house"] for k in kept if k["house"]).most_common()),
                "groups": dict(Counter(k["group"] for k in kept if k["group"]).most_common()),
                "unlisted": [k["advertiser_id"] for k in kept if k["house"] and k["advertiser_id"] not in listed]})
    return out


def confirmed() -> dict[str, tuple[str, str]]:
    """advertiser id -> (house, status), from the checked list; empty until it exists. status is house or doubt."""
    if not CONFIRMED.exists():
        return {}
    with CONFIRMED.open(encoding="utf-8", newline="") as f:
        return {r["advertiser_id"].strip(): (r["house"].strip(), (r.get("status") or "house").strip() or "house")
                for r in csv.DictReader(f) if r.get("advertiser_id", "").strip() and r.get("house", "").strip()}


def compact(r: dict, house: str, status: str) -> dict:
    """One ad, small: first and last day over every region, each region's [first, last, low, high], the
    impressions summed over countries (an aggregate region such as EEA is kept in where but not added), the same
    by surface, and the audience approaches without their CRITERIA_ prefix."""
    where, firsts, lasts, since, till = {}, [], [], [], []
    lo = hi = None
    on: dict[str, list[int]] = {}
    for g in r.get("regions") or []:
        code = g.get("region_code") or "?"
        f, l = _day(g.get("first_shown")), _day(g.get("last_shown"))
        where[code] = [f, l, g.get("lo"), g.get("hi")]
        firsts += [f] if f else []
        lasts += [l] if l else []
        since += [_day(g["since"])] if g.get("since") else []
        till += [_day(g["till"])] if g.get("till") else []
        if COUNTRY.match(code):
            if g.get("lo") is not None or g.get("hi") is not None:
                lo, hi = (lo or 0) + (g.get("lo") or 0), (hi or 0) + (g.get("hi") or 0)
            for s in g.get("surfaces") or []:
                t = on.setdefault(s.get("surface") or "?", [0, 0])
                t[0] += s.get("lo") or 0
                t[1] += s.get("hi") or 0
    aud = {k: (v or "").removeprefix("CRITERIA_") for k, v in (r.get("aud") or {}).items() if v}
    ad = {"id": r["creative_id"], "adv": r["advertiser_id"], "house": house, "fmt": r.get("ad_format_type"),
          "first": min(firsts) if firsts else None, "last": max(lasts) if lasts else None, "lo": lo, "hi": hi,
          "since": min(since) if since else None, "till": max(till) if till else None, "where": where}
    if on:
        ad["on"] = on
    if aud:
        ad["aud"] = aud
    if r.get("topic"):
        ad["topic"] = r["topic"]
    if r.get("ad_funded_by"):
        ad["paid_by"] = r["ad_funded_by"]
    if status != "house":
        ad["status"] = status
    return ad


def shard(ad: dict) -> str:
    return f"{ad['house']}-{(ad.get('first') or 'unknown')[:4]}"


def collect(reg: registry.Registry, run: str, max_gb: float = MAX_GB["collect"]) -> dict:
    bq, cl = client()
    P = paths()
    listed = confirmed()
    if not listed:
        return {"run": run, "stopped": "no confirmed advertisers: reference/google_advertisers.csv is empty or missing"}
    p = [bq.ArrayQueryParameter("ids", "STRING", sorted(listed))]
    nbytes, rows = measured(bq, cl, SQL_ADS.format(table=TABLE), max_gb, p)
    out = {"run": run, "ads_query_gb": round(nbytes / 1e9, 2), "max_gb": max_gb}
    if rows is None:
        out["stopped"] = f"the ads query would read {nbytes / 1e9:.1f} GB, above {max_gb:g}"
        return out
    by: dict[str, list[dict]] = {}
    for r in rows:
        house, status = listed[r["advertiser_id"]]
        ad = compact(dict(r.items()), house, status)
        by.setdefault(shard(ad), []).append(ad)
    P["creatives"].mkdir(parents=True, exist_ok=True)
    written = set()
    for name, xs in sorted(by.items()):
        xs.sort(key=lambda x: (x["first"] or "", x["id"]))
        f = P["creatives"] / f"{name}.jsonl"
        store.write_jsonl(f, xs)
        written.add(f.name)
    for f in P["creatives"].glob("*.jsonl"):                     # a whole snapshot: an account no longer listed goes
        if f.name not in written:
            f.unlink()
    ads = [a for xs in by.values() for a in xs]
    houses = Counter(a["house"] for a in ads)
    sizes = {f.name: f.stat().st_size for f in P["creatives"].glob("*.jsonl")}
    out.update({"ads": len(ads), "houses": dict(sorted(houses.items())),
                "doubt": dict(sorted(Counter(a["house"] for a in ads if a.get("status") == "doubt").items())),
                "accounts_with_ads": len({a["adv"] for a in ads}), "accounts_listed": len(listed),
                "files": len(sizes), "largest_file_mb": round(max(sizes.values(), default=0) / 2**20, 2),
                "total_mb": round(sum(sizes.values()) / 2**20, 2),
                "first": min((a["first"] for a in ads if a["first"]), default=None),
                "last": max((a["last"] for a in ads if a["last"]), default=None)})
    return out


def due_discovery() -> str | None:
    """Why discover should run before collect, or None."""
    t = store.read_state(paths()["table"])
    if t.get("rules") != RULES_VERSION:
        return "the matching rules changed"
    try:
        age = (date.today() - date.fromisoformat((t.get("read_at") or "")[:10])).days
    except ValueError:
        return "no discovery on file"
    return f"{age} days since the last discovery" if age >= REDISCOVER_DAYS else None


def probe(run: str) -> list[str]:
    st = store.read_state(paths()["state"])
    if st.get("run") != run:
        return [f"no adstransparency run {run} on file"]
    return [st["stopped"]] if st.get("stopped") else []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="adtone.adstransparency")
    ap.add_argument("stage", choices=["auto", "discover", "collect", "probe"])
    ap.add_argument("--run", default=config.run_id())
    ap.add_argument("--max-gb", type=float, default=None)
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
    if a.stage == "auto":
        why = due_discovery() if confirmed() else "no confirmed accounts yet"
        stages = (["discover"] if why else []) + (["collect"] if confirmed() else [])
    else:
        why, stages = None, [a.stage]
    out = {"run": a.run, "stage": "+".join(stages)}
    if why:
        out["discover_because"] = why
    for s in stages:
        try:
            res = (discover if s == "discover" else collect)(reg, a.run, a.max_gb or MAX_GB[s])
        except Exception as e:                                   # recorded, never a token or a key: the message only
            res = {"stopped": f"{type(e).__name__}: {str(e)[:400]}"}
        res.pop("run", None)
        out[s] = res
        if res.get("stopped"):
            out["stopped"] = f"{s}: {res['stopped']}"
            break
    out["updated_at"] = store.utc_now()
    store.write_state(P["state"], out)
    store.append_jsonl(P["prov"], [out])
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
