# How the data and the analysis thread together

Everything joins on two keys: the house (`registry/houses.yml` is the only source of house identity)
and the date. Two calendars supply the dates that matter: designer events in the registry, and show
dates in `reference/shows.csv`.

## Sources, each owned by one workflow

| Source | Workflow | Stored (derived only) | Grain |
|---|---|---|---|
| Meta Ad Library: ads, reach, targeting | collect (Meta token) | `data/ads` | per ad |
| The reader on Modal, the image fingerprint | process | `data/obs`, `data/vectors` | per image |
| Brand homepages, monthly since 2014, from the Wayback Machine, read by the reader | homepages | `data/homepages` | per house, per month |
| Back catalogue: campaigns listed on models.com | backcat | `data/backcat` | per campaign |
| YouTube: each house's films, counts and thumbnails (needs `YOUTUBE_API_KEY`) | youtube | `data/youtube` | per film, weekly counts |
| TikTok's ad library for Europe (needs an approved Commercial Content API key) | tiktok | `data/tiktok` | per ad |
| Wikipedia page views, English | attention | `data/attention` | per house, per day |
| Wikipedia page views in ten languages, renamed articles joined | attention | `data/wikiviews` | per house, language, day |
| Wikidata: owners and creative directors, checked against the registry | attention | `data/wikidata` | per house |
| GDELT news tone and volume | press | `data/press` | per house, per day |
| Revenue by house, with each figure's source | compiled by hand | `reference/revenue.csv` | per quarter, half or year |
| Shows, credits, show statements, media value, Lyst Index | compiled by hand | `reference/` | per show, campaign, season, quarter |

Images are never stored: the reader and the fingerprint keep answers and numbers. Any workflow can be
started by editing its file in `.github/kick/`.

## Three ways the sources are cut

- **Around designer events.** Ads before and after each debut, measured against the controls: the
  frozen tests (H1 shift, H2 transfer, H3 detection) and Amendment 2's transfer family, pooled shift,
  falsifications and kill rule. `adtone.analysis`, `adtone.family`.
- **Around shows.** One row per show, every source joined on house and date: the attention spike, its
  surprise against the house's earlier shows, the lasting lift, press reception and its spike,
  momentum, and inside the archive the reach after the show and the campaign's alignment with the
  house's show-period ads. That table is written to `data/results/runway_events.csv`; the sticky
  test, the bridge and what the clothes add all read it. `adtone.runway`.
- **By month.** Each house's four advertising readings (movement, distance from the market,
  consistency, drift towards the leader) set against next month's curiosity and, more thinly, desire
  and money: the forecast. Amendment 2, section 8.
- **By half-year, from the homepages.** Each house's character (its share of each answer to the
  eighteen questions and its fingerprint centroid), the shift between half-years against chance,
  its distance from the other houses, and each shift beside the next half-year's organic growth where
  revenue is reported. Exploratory and descriptive. `adtone.character`.

## Order of operations

collect, then process (reader and fingerprint), then analyse; homepages every two hours while their
history fills, press every four hours, attention and the back catalogue weekly, YouTube and TikTok
weekly once their keys exist; status after any of them. Nothing in the analyses runs on real data until the amendment that
registers it is frozen.
