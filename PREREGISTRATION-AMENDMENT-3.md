# Amendment 3 to the pre-registration: the thread from the show to the advertising

STATUS: DRAFT, NOT DECIDED. Ben has not yet chosen whether the links between the show and the
advertising are to be tested or only described. If they are tested, this file binds once frozen: its
SHA-256 in `PREREGISTRATION-AMENDMENT-3.sha256`, committed and timestamped on the public record before
any collection's runway looks, homepage pictures or advertisements are read with `rubric/clothes-v1.md`.
The readings of the rubric's own test set (`data/clothes/sample.json`) decide which questions go
forward and are never used here. Until frozen, everything below is exploratory and reported as such.

## What it does not change

The frozen pre-registration and Amendments 1 and 2 stand as they are. Nothing here reads their
outcomes, and the attention that lasts after a show stays Amendment 2's.

## 1. The unit and the windows

The unit is the collection: one house's show of one kind (ready-to-wear, men's, couture) in one
season, on the Thread's calendar (`data/thread/shows.jsonl`). Each collection is followed through
stages on windows counted in days from the show date, as fixed in `adtone/thread.py` (`STAGES`):
the show (the day before to three days after), the shop window (the homepages, 1 to 182 days after)
and the advertising (ads first seen 45 to 150 days after, Amendment 2's campaign window, or the window
Amendment 2 settles on before it is frozen).

## 2. The shared description

The show and the advertising have different purposes and are scored on different scorecards; what
they share is the clothes. Each picture is read with `rubric/clothes-v1.md`, frozen, and only the
questions and options that went forward under its rule are used: those that read the same way on
runway looks and on advertising pictures.

A collection's position in a channel is the share of its pictures giving each answer that went
forward (for a list, the share choosing each option; for street to couture, the mean). Each channel
is placed against its own kind: positions are standardised across the houses in the same channel and
season, so a runway is compared with other runways and an advertisement with other advertisements.
A position needs at least 8 pictures: 8 looks of the show, 8 homepage pictures in the window, or 8
advertisements first seen in it.

## 3. The registered test: transmission

When a house's runway moves, does its shop window, and then its advertising, move the same way?

- **Change.** For each collection with a previous show of the same kind by the same house: the
  change in its runway position from that show to this one, and the change in its shop-window
  position over the same step (this show's window less the previous show's), and likewise for its
  advertising.
- **Statistic.** The mean, over collections, of the cosine between the runway change and the
  shop-window change.
- **Null.** 1,000 shuffles of the shop-window changes among the houses of the same season, which
  keeps every season-wide movement and breaks only the pairing of a house's runway with its own shop
  window.
- **Decision.** Supported if the observed mean exceeds 95% of the shuffled means (one-sided, p at most
  0.05) and at least 30 collections enter. Runway to shop window is primary. Runway to advertising is
  the same test on the advertising changes, secondary, Holm-adjusted with the primary, and reported
  as more than description only from 30 collections.
- **Reading.** Supported: a house's change on the runway is followed by a change of the same kind in
  what it shows to customers. Not supported: no pairing beyond the season's common movement is
  detectable at this sample. Neither says that one causes the other: a house may plan both together.

## 4. Described, not tested

- **The gap.** For each collection, the distance between its runway position and its advertising
  position (one minus their cosine), house by house through the seasons. A wide gap is a strategy (the
  show as statement, the advertising as commerce), not a fault, and no gap is called better.
- **Icons.** The share of a house's advertising pictures, in each window, closer to none of its last
  two runway positions than the median advertising picture of the season: what sells without coming
  from a show.
- **Influence.** Whether the other houses' runway positions move towards a house's position in the
  following season.
- **Attention both ways.** Show heat beside the reach of the advertising that follows; the attention
  between shows beside the next show's heat. Amendment 2's registered tests are not repeated here.
- **The scene.** Fashion ambassador appointments in the 180 days before a show beside its heat.

## 5. Limits

The runway rests on the pages the Wayback Machine kept on the houses' own sites
(`data/clothes/looks_coverage.json`), which cover some houses and seasons and not others; the test runs
on the collections the archive allows and reports which. The advertising starts in 2023 at the
earliest, as Meta's archive does. The homepages are what a house chose to show at the front of its
site, mostly campaign pictures, not its whole shop. The reader errs; the rubric's rule bounds how much.

## Before freezing

`rubric/clothes-v1.md` frozen and its kept questions written down; the coverage map and the runway
collection finished, and the collections they cover recorded; Amendment 2's campaign window settled;
this file and its hash committed together.
