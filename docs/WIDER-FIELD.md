# The wider field: what is on file and what is still to gather

Twelve houses joined on 15 October 2026 (`registry/houses.yml`, `tier: wider`; reasons and order in
`SELECTION.md`): armani, moncler, ferragamo, jacquemus, the_row, mcqueen, balmain, schiaparelli, lanvin,
stella_mccartney, comme_des_garcons, issey_miyake.

The collectors gather what they can by themselves: Focal's daily homepage captures, the homepage history
from the web archive (the twelve first), page views and Wikidata, news, the back catalogue and Google's ad
archive. The hand-built joins below were compiled by agents in two passes on 15 October 2026, every row
with its source, and checked before merging; the first pass ran out of search budget, the second took up
what it missed. Each item is done when its rows are merged, the repository's checks pass, and the box is
ticked here in the same commit.

## On file, 15 October 2026 (second pass)

| table | rows | houses with none |
|---|---|---|
| `reference/designers.csv` | 37 | none |
| `reference/executives.csv` | 40 | none |
| `reference/shows.csv` | 152, February 2022 on | none |
| `data/thread/shows.jsonl` (show listings) | 508; full listings for ferragamo, issey_miyake, balmain, schiaparelli, lanvin and comme_des_garcons | none |
| `reference/ambassadors.csv` | 27, and a note row where none was found | the_row, schiaparelli, stella_mccartney, comme_des_garcons, issey_miyake (none found) |
| `reference/credits.csv` | 312, crew only | comme_des_garcons (none found) |
| `reference/google_advertisers.csv` | 20 accounts, 19 house and 1 doubt | the_row, schiaparelli, comme_des_garcons (none found) |
| `reference/youtube_channels.csv` | 9 of the twelve, 3 linked from the house's own site, 6 found by search (unverified) | jacquemus, comme_des_garcons, issey_miyake (no channel of the house found) |
| `reference/revenue.csv` | 93 | all but moncler, ferragamo, armani and lanvin |

## Still to gather

Identifiers
- [ ] Show listings: the full markdown listings for ferragamo, issey_miyake, and the earlier entries the
      brand pages cut off for armani (main line before 2020), balmain, schiaparelli, lanvin, mcqueen and
      comme_des_garcons; merged with `python -m adtone.thread ingest` (titles, dates and links only).
      Merged on 15 October 2026 for ferragamo, issey_miyake, balmain, schiaparelli, lanvin and
      comme_des_garcons. Still to do: mcqueen (its listing would not load) and armani, whose Privé page now
      links the Fall Winter 2026 couture show as `...-fall-winter-2026-paris-2`, not the address on file.
- [x] YouTube: each house's official channel in `reference/youtube_channels.csv`, with the evidence that it
      is the house's own.
- [x] Google's ad archive: once the discovery run lists the twelve houses' candidate accounts, confirm each
      by hand in `reference/google_advertisers.csv`, as the file's notes do. The run of 15 October 2026 found
      19 accounts for nine of the houses and none for the_row, schiaparelli or comme_des_garcons. All 19 were
      checked on 15 October 2026 by name, place and Google's verification (the ad archive's pages show nothing
      to a fetcher, so their topics are first seen by the next collect): 18 are the houses' own companies
      (house), and Moncler S.P.A, not verified by Google, is entered as doubt.
- [x] Owners. No completion date is public for any of the three deals, so the announcements stand in:
      Lanvin's earlier owner, Shaw-Lan Wang, until Fosun said it had acquired a majority (2018-02-22); at
      Balmain, the Hivelin family until the sale to Mayhoola was announced (2016-06-21, not the 22nd, which
      was the press reports' date). Stella McCartney has no earlier owner entered: her purchase of Kering's
      half was announced on 2018-03-28 but Kering still carried it at 30 June 2018. LVMH's minority (announced
      2019-07-15) never gave it control and was bought back on 2025-01-27. All in comments in
      `registry/houses.yml`.

Shows (`reference/shows.csv`)
- [x] The 2022 seasons for the eight Paris houses: 17 shows, seven of them unverified, because the FHCM's
      2022 calendars could not be reached and dates and times come from calendars printed in the press.
      McQueen showed AW22 in New York and SS23 in London; Comme des Garçons showed AW22 in Tokyo.
- [x] Giorgio Armani: 16 shows, including SS25 women's in New York (17 October 2024) rather than Milan.
- [x] Ferragamo (AW22 was a presentation), Jacquemus's four shows, Moncler's Re/Icons presentation in Paris
      (5 October 2026; no 2025 show beyond Courchevel was found) and McQueen SS27 in London (20 September 2026).
- [ ] Still open on shows: the days of Comme des Garçons' two Tokyo shows of AW22 (men's and women's, no
      source gives them, so they have no row); whether Schiaparelli showed ready-to-wear for SS23; the 2022
      rows checked against the FHCM's own calendars once they can be reached.

People
- [x] Designers: the founder rows for Simon Porte Jacquemus and Rei Kawakubo, now sourced; first and last
      shows filled at Schiaparelli, Lanvin, Issey Miyake and Ferragamo, with two departure days corrected
      (Giornetti 2016-03-24, Andrew 2021-04-08); Issey Miyake's men's lines (Yusuke Takahashi, then the
      studio, then IM MEN).
- [x] Executives: Ferragamo has had no chief executive since Gobbetti left (2025-03-06); McQueen's before
      and after Testa (Gintzburger; Gianfranco D'Attis from 2026-06-03); Balmain's before Guével (Diemoz,
      Piombini); Schiaparelli's Delphine Bellini, now from trade press; Lanvin's sequence to Barbara
      Werschine (2026-05-28); Stella McCartney's dates; Jacquemus (Bastien Daguzan, then Sarah Benady);
      Comme des Garçons (Rei Kawakubo, Adrian Joffe).
- [x] Ambassadors: Armani Beauty and fragrance faces named by the licensee, L'Oréal; Moncler Grenoble's;
      Balmain Watches in Portugal; note rows for the_row, comme_des_garcons and issey_miyake, where none was
      found. None new was found for McQueen after 2023 or for Lanvin.
- [x] Credits: 115 rows, among them Sarah Burton's 2021 to 2023 McQueen campaigns, Balmain 2022 to 2026,
      Moncler 2021 to 2024, Stella McCartney 2020 to 2023 and The Row's lookbooks and Summer 2022 campaign.
- [ ] Still open on people: the show days missing for Giornetti's last show (AW16), Rigoni's first (SS17),
      Andrew's last (AW21), Elbaz's last (SS16) and Takahashi's first (SS14); Guillaume Meilland's dates;
      Hecquet's appointment at Lanvin, Andy Lew's announcement and Tom Mendenhall's start; credits for Comme
      des Garçons and Schiaparelli, and for Moncler, Stella McCartney and The Row after 2024, whose credits
      sit behind a login.

Revenue (`reference/revenue.csv`, published figures only)
- [x] Ferragamo 2020Q1 to 2026H1 from the company's releases; the Armani Group's net revenues for 2019 to
      2025 (2021 from the group's own release, the other years from press reports and unverified); the
      Lanvin brand's revenue from Lanvin Group's results, 2021H1 to 2026H1.
- [ ] Still open: the Armani Group's own releases for the six years now taken from press.
- [ ] Companies House: the filed accounts could not be opened. Stella McCartney Ltd (04169969) files full
      accounts for the one company, not group accounts, so its turnover (from press: £32.5m in 2021, £40.1m
      in 2022, £21.9m in 2023, £16m in 2024) is not the house's worldwide revenue and is not entered.
      Alexander McQueen Trading Limited (04438273) has accounts on file for 2019 to 2024, unread. Jacquemus
      has published only a lower bound (its founder: over €100 million in 2021); nothing published was found
      for Balmain; Comme des Garçons and Issey Miyake were not searched.

Later
- [x] Moncler's own advertising account: the brand is licensed to the group's sub-holding Industries S.p.A.
      (Moncler annual report 2024), so the matching rules (version 4, 15 October 2026) look for that exact
      company as Moncler's. The discovery run that day found it: INDUSTRIES S.P.A., Italy, verified, 2,714 ads,
      now confirmed in `reference/google_advertisers.csv`; the next collect gathers its ads. The Armani
      accounts are the group's companies, so their ads may include Emporio Armani and Armani Exchange (noted
      on each row).
- [ ] Show statements (`reference/statements.csv`) for the twelve houses' shows of 2025 and 2026.
- [x] Issue covers for the twelve: drawn on 15 October 2026 and checked by eye, the chosen picture of each
      house in `data/issues/choices.json`. The Row, Balmain and Schiaparelli were drawn again (`seeds_for` in
      the plan) because their first pictures ran into the masthead; Schiaparelli's is now seen from above.
- [ ] The twelve covers on the site's Issues page, once each house has readings for the back of its cover:
      the covers' markup. Their pictures were published on 15 October 2026 (`python -m adtone.issues publish`,
      which now makes only new or changed pictures, so the 28 already on the site kept their names) and are in
      `data/issues/web.json`, so the site's build already fetches them.
