# The wider field: what is on file and what is still to gather

Twelve houses joined on 15 October 2026 (`registry/houses.yml`, `tier: wider`; reasons and order in
`SELECTION.md`): armani, moncler, ferragamo, jacquemus, the_row, mcqueen, balmain, schiaparelli, lanvin,
stella_mccartney, comme_des_garcons, issey_miyake.

The collectors gather what they can by themselves: Focal's daily homepage captures, the homepage history
from the web archive (the twelve first), page views and Wikidata, news, the back catalogue and Google's ad
archive. The hand-built joins below were compiled by agents on 15 October 2026, every row with its source,
and checked before merging. Their shared search budget ran out early, so the list of what is still missing
is long. Each item is done when its rows are merged, the repository's checks pass, and the box is ticked
here in the same commit.

## On file, 15 October 2026

| table | rows | houses with none |
|---|---|---|
| `reference/designers.csv` | 34 | none |
| `reference/executives.csv` | 24 | jacquemus, comme_des_garcons |
| `reference/shows.csv` | 107, January 2023 on for the Paris houses | none |
| `data/thread/shows.jsonl` (show listings) | 223 | ferragamo, issey_miyake |
| `reference/ambassadors.csv` | 18 | the_row, comme_des_garcons, issey_miyake (not yet searched) |
| `reference/credits.csv` | 197, crew only | the_row, comme_des_garcons |
| `reference/youtube_channels.csv` | 9 of the twelve, 3 linked from the house's own site, 6 found by search (unverified) | jacquemus, comme_des_garcons, issey_miyake (no channel of the house found) |
| `reference/revenue.csv` | 49 | all but moncler and ferragamo (2019 only) |

## Still to gather

Identifiers
- [ ] Show listings: the full markdown listings for ferragamo, issey_miyake, and the earlier entries the
      brand pages cut off for armani (main line before 2020), balmain, schiaparelli, lanvin, mcqueen and
      comme_des_garcons; merged with `python -m adtone.thread ingest` (titles, dates and links only).
- [x] YouTube: each house's official channel in `reference/youtube_channels.csv`, with the evidence that it
      is the house's own.
- [ ] Google's ad archive: once the discovery run lists the twelve houses' candidate accounts, confirm each
      by hand in `reference/google_advertisers.csv`, as the file's notes do.
- [ ] Owners: the day Fosun took control of Lanvin in 2018; the day the Kering joint venture at Stella
      McCartney ended in 2018, and whether LVMH's 2019 minority stake was bought back; Mayhoola's completion
      at Balmain (entered as 22 June 2016, reported). Entered in `registry/houses.yml` as `earlier_owners`.

Shows (`reference/shows.csv`)
- [ ] The 2022 seasons for the eight Paris houses (men's and couture weeks of January, June and July 2022;
      women's weeks of March and September 2022).
- [ ] Giorgio Armani: women's AW22, SS23, AW23, SS25, AW25 and every men's show SS23 to SS27 (Milan);
      Armani Privé AW22 and AW24 couture.
- [ ] Ferragamo AW22, SS24, SS25, AW25, SS26, AW26; Jacquemus Le Papier (June 2022), Le Chouchou (June
      2023), Les Sculptures and La Casa (2024); Moncler's events of 2025 and 2026; McQueen SS27 (London).

People
- [ ] Designers: a source naming Simon Porte Jacquemus and Rei Kawakubo for their founder rows; the show
      dates missing at Ferragamo, Schiaparelli, Lanvin and Issey Miyake; Issey Miyake's men's lines.
- [ ] Executives: Ferragamo after Marco Gobbetti; McQueen before Gianfilippo Testa; Balmain before
      Jean-Jacques Guével; Schiaparelli (Delphine Bellini, unconfirmed); the dates at Lanvin and Stella
      McCartney; Jacquemus and Comme des Garçons; whether Moncler changed chief executive again in 2026.
- [ ] Ambassadors: the_row, comme_des_garcons and issey_miyake; more for armani, moncler, mcqueen (after
      2023), balmain and lanvin; beauty and fragrance appointments by licensees, with the licensee named.
- [ ] Credits (crew only, never models or talent): the_row and comme_des_garcons; more for moncler,
      schiaparelli, stella_mccartney and issey_miyake; Sarah Burton's 2023 McQueen campaigns; Balmain's 2024
      and 2025 campaigns.

Revenue (`reference/revenue.csv`, published figures only)
- [ ] Ferragamo 2020 to 2026; Giorgio Armani's annual results; Lanvin from Lanvin Group's results.
- [ ] Companies House accounts for Stella McCartney Ltd and Alexander McQueen Trading Ltd; Jacquemus,
      Balmain, Comme des Garçons and Issey Miyake where the companies have published figures.

Later
- [ ] Show statements (`reference/statements.csv`) for the twelve houses' shows of 2025 and 2026.
- [x] Issue covers for the twelve: drawn on 15 October 2026 and checked by eye, the chosen picture of each
      house in `data/issues/choices.json`. The Row, Balmain and Schiaparelli were drawn again (`seeds_for` in
      the plan) because their first pictures ran into the masthead; Schiaparelli's is now seen from above.
- [ ] The twelve covers on the site's Issues page, once each house has readings for the back of its cover:
      `python -m adtone.issues publish`, then the covers' markup, checking that the 28 pictures already on the
      site keep their names.
