# How the data and the analysis thread together

Everything joins on two keys: the house (`registry/houses.yml` is the only source of house identity)
and the date. Two calendars supply the dates that matter: designer events in the registry, and show
dates in `reference/shows.csv`.

## Sources, each owned by one workflow

| Source | Workflow | Stored (derived only) | Grain |
|---|---|---|---|
| Meta Ad Library: ads, reach, targeting | collect (Meta token) | `data/ads` | per ad |
| The reader on Modal, the image fingerprint | process | `data/obs`, `data/vectors` | per image |
| Wikipedia page views | attention | `data/attention` | per house, per day |
| GDELT news tone and volume | attention | `data/press` | per house, per day |
| Lyst Index ranks, house revenue | compiled by hand | `reference/` | per quarter |

Images are never stored: the reader and the fingerprint keep answers and numbers.

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

## Order of operations

collect, then process (reader and fingerprint), then analyse; attention weekly on its own clock;
status after any of them. Nothing in the analyses runs on real data until the amendment that
registers it is frozen.
