# Campaign credits

`reference/credits.csv` is a hand-made seed for separating photographers from houses: one row per
credited person per campaign entry, read on 6 October 2026 from public pages (models.com listings and
work pages, campaign reviews, Ads of the World, press). Every row names the page it came from.

## What is in it

290 rows from 99 source pages, covering 16 of the 19 registry houses (not Hermès, Loro Piana or Chloé).
120 rows are photographer credits; the rest are film, art direction, styling, casting, set design, hair
and makeup. Chanel's beauty campaigns are included with `scope` beauty, because photographers cross
between beauty and fashion. Summaries leave beauty out by default, as the panel does.

Crew only. Models and talent are never recorded, in line with the rubric's rule of never identifying
the people in an ad. Manicurists, production companies and music are left out. Campaigns built from
found pictures (Balenciaga's Le City paparazzi campaign, Bottega Veneta's A$AP Rocky campaign) have no
photographer and no row.

`reference/stated_links.csv` holds four relationships a source states outright but the credits cannot
show, such as Piccioli and David Sims at Valentino, which is outside the table.

## Columns

| column | meaning |
|---|---|
| house | registry id |
| client | as the source names it (Dior Men, Chanel Fragrances & Beauty) |
| scope | fashion or beauty |
| campaign | the source's name for it; may be blank |
| work_type | advertising, lookbook, social or ecommerce |
| published | YYYY, YYYY-MM or YYYY-MM-DD, as precise as the source; blank when it gives none |
| creative_director | the house's creative director for this campaign |
| cd_basis | source when the page names them, tenure when the house had one creative director throughout, blank during a handover |
| role | photographer, director, videographer, cinematography, art_direction, stylist, casting, set_design, hair, makeup |
| person | as credited |
| source_url | one or more pages, separated by ` \| ` |
| notes | caveats, such as a row that may repeat another campaign |

## Soft spots

- These are search snapshots, not complete records. The table holds what the pages showed, not every
  campaign each house ran.
- Nothing is on file for the before side at Jil Sander and Margiela.
- Rows that may describe one campaign twice say so in `notes` (Gucci's Blommers and Schumm, Chanel's
  Métiers d'Art, Prada's two Spring 2026 reviews). They can double a person's count at one house. They
  cannot create a crossing or an import.
- Where a row has no date, its year comes from the season in the campaign name, which can run ahead of
  publication: Loewe's Spring 2026 teaser came out in September 2025.
- Juergen Teller's Loewe SS25 pre-collection campaign is not in the table, because no public page for
  it was to hand.

## What it shows on 6 October 2026

`python -m adtone.credits` prints coverage, carry-over, crossings and designer moves. On this seed:

- None of the eight treated houses with both sides on file kept an advertising photographer across the debut.
- From 2025 to 2026, Prada kept Oliver Hadlee Pearch and Valentino kept Glen Luchford. Saint Laurent,
  Miu Miu and Burberry kept nobody.
- Three designer moves inside the table carried crew with them. Blazy brought Alec Soth, Louise and Maria
  Thornfeldt and Rahim Fortune from Bottega Veneta to Chanel. Anderson brought David Sims, the stylist
  Benjamin Bruno, the cinematographer Benoît Delhomme and the set designer Poppy Bartlett from Loewe to
  Dior. Demna brought himself, as photographer, from Balenciaga to Gucci. Chiuri's move from Dior to
  Fendi carried nobody on file.
- Nine photographers are credited at two or more houses, David Sims at six.

## Use

Separating photographers from houses needs each ad concept tied to a campaign's crew. That join is not
built. It would run on house and campaign period, plus the image matches `adtone.calibrate` already
finds between the back catalogue and the live ads.

When the backcat workflow runs, `adtone.credits.from_backcat` turns its models.com rows into this shape,
so the automated spine can extend these rows, and these rows' sources can check it.

Nothing here feeds the v1 pre-registered tests.

## Adding rows

Same columns, a source page on every row, crew only, and a blank creative director rather than a
guess. `tests/test_credits.py` validates the whole file on every run.
