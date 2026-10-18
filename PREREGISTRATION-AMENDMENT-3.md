# Amendment 3 to the pre-registration: the thread from the show to the advertising

STATUS: DRAFT. On 11 October 2026 Ben decided that the link between the show and the advertising is
tested, on the advertising, and that the shop window is described. It binds once frozen: its SHA-256 in
`PREREGISTRATION-AMENDMENT-3.sha256`, committed and timestamped on the public record before any
advertisement is read with `rubric/clothes-v1.md`. The readings of the rubric's own test set
(`data/clothes/sample.json`) decide which questions go forward and are never used here. Until frozen,
everything below is exploratory and reported as such.

## Read before the freeze

On 17 October 2026, before this amendment was frozen, the paid-social collectors read 1,628 pictures of
Snapchat advertisements with `rubric/clothes-v1.md` (the reader had been added to the collection loop
that day, and the run finished before it was stopped; `data/snap/obs/clothes-v1.jsonl`). No position,
change, likeness or statistic of section 3 was computed from them, and none will be until the freeze;
the collectors read no further advertisement with the rubric until then (`--no-clothes`). The readings
are kept, dated, so that the record shows them rather than hides them; whether they enter the test
after the freeze is decided here, in writing, before it.

On 18 October 2026 Ben directed that the clothes readings of advertisements proceed before the freeze,
and that the clothes analysis run on them. Every result so produced is exploratory and is reported as
such, under the clause above. The rules that decide which questions stand (rules 1 to 4, fixed with the
rubric on 9 October) read the test set and the labels only, never these readings; what this costs is
that the text of this amendment is now finished by people who have seen the advertising read on the
rubric, which the freeze was meant to prevent. The registered test of section 3 therefore reads its
first result from the collections whose advertising positions were not computed before the freeze: the
spring-summer 2027 campaign windows onward. Positions computed before the freeze are description.

## What it does not change

The frozen pre-registration and Amendments 1 and 2 stand as they are. Nothing here reads their
outcomes. The attention that lasts after a show, and the lift in attention from the day a campaign
lands, stay Amendment 2's.

## What was seen before it was decided

The first draft tested the shop window, the homepages, and followed the advertising as a second test.
It was changed after the runway looks and the homepage pictures had been read with the rubric
(provisional: no label had yet been given) and before any advertisement had been collected or read.
On those readings:

- A shop window's pictures first shown before day 120 looked no more like the collection's runway than
  like the house's previous one: closer to it in 8 of 17 collections, mean likeness 0.917 against 0.930.
  They cannot be credited to the show they follow.
- Started at day 120, around when campaigns land, 17 shop windows had five pictures of an outfit worn,
  against 119 over the whole window.
- The shop window moved less than the runway between seasons (median movement 0.07 against 0.11, net
  of chance), and 31 of its 95 changes could be told from chance, against 41 of 54 runway changes. The
  test would have set most runway changes against shop-window changes no larger than chance.
- In 13 of 24 collections the shop window looked more like its own runway than like any other runway
  of its season: it carries the house more than the season.
- The 38 autumn-winter campaigns the Thread dates (models.com's publication dates) came out a median
  143.5 days after the show, with quartiles at 120 and 174 days.

The industry's own account points the same way. Houses present the show as the designer's statement
and a burst of attention, and the advertising as a portfolio on the retail calendar (the collection's
campaign beside leather goods, fragrance and holiday campaigns) that keeps the house recognisable and
sells named products, often made by people other than the designer.

## 1. The unit and the windows

The unit is the collection: one house's show of one kind (ready-to-wear, men's, couture) in one
season, on the Thread's calendar (`data/thread/shows.jsonl`). Each collection is followed through
stages on windows counted in days from the show date, as fixed in `adtone/thread.py`: the show (the
day before to three days after, `STAGES`), the shop window (the homepages, 1 to 182 days after, to the
day before the house's next main show) and the advertising: ads first seen in the campaign window,
from 45 days after the show to 7 days before the house's next main show, or, where that show is not on
file, to 203 days after an autumn-winter show and 147 after a spring-summer one (`campaign_window`).
Amendment 2's bridge uses the same window. It holds 34 of the 38 dated autumn-winter campaigns.

## 2. The shared description

The show and the advertising have different purposes and are scored on different scorecards; what
they share is the clothes. Each picture is read with `rubric/clothes-v1.md`, frozen, and only the
questions and options that went forward under its rule are used: those that read the same way on
runway looks and on advertising pictures.

A collection's position in a channel is the share of its pictures giving each answer that went
forward (for a list, the share choosing each option; for street to couture, the mean). Each channel
is placed against its own kind: positions are standardised across the houses in the same channel and
season, so a runway is compared with other runways and an advertisement with other advertisements.
A position needs at least 8 pictures: 8 looks of the show, or 8 advertisements for the season's
collection first seen in the campaign window.

**Which advertisements.** A house advertises far more than its collection, and each line has its own
job. Only advertisements for the season's collection enter a position. Each ad is sorted when it is
collected, by its own text (body, link title and description), as the Thread sorts campaign titles:
beauty, jewellery and watches, accessories, capsules and the holidays by their words, in English,
French, Italian, German and Spanish; the season's collection when the text names a season, a
collection, a campaign or a show and none of those; other otherwise (`adtone/thread.py`, `ad_line`).
The text itself is not kept, only its sorting. A concept counts as the collection's when one of its
ads names the collection and none names another line, or, where its text names nothing, when its
picture shows a full outfit worn (the rubric's subject `worn_full`; `adtone/runway.py`,
`collection_sorter`).

## 3. The registered test: transmission

When a house's runway moves, does its advertising for the collection move the same way?

- **Change.** For each collection with a previous show of the same kind by the same house, both with
  positions on both sides: the change in its runway position from that show to this one, and the
  change in its advertising position over the same step (this campaign window's less the previous
  one's).
- **Statistic.** The mean, over collections, of the cosine between the runway change and the
  advertising change. A cosine reads direction, not size: advertising that moves less than its runway,
  the same way, counts as following it.
- **Null.** 1,000 shuffles of the advertising changes among the houses of the same season, which keeps
  every season-wide movement and breaks only the pairing of a house's runway with its own advertising.
- **Decision.** Supported if the observed mean exceeds 95% of the shuffled means (one-sided, p at most
  0.05) and at least 30 collections enter. It is this amendment's only registered test; until 30
  collections enter it is reported as description.
- **Reading.** Supported: a house's change on the runway is followed by a change of the same kind in
  its advertising for the collection. Not supported: no pairing beyond the season's common movement is
  detectable at this sample. Neither says that one causes the other: a house may plan both together.

## 4. Described, not tested

- **The shop window.** The homepages on their own scorecard: their distinctness among the season's
  homepages, their mix, their likeness to the house's own runway, and whether they look more like it
  than like the season's other runways (the house's signature). Their movement is shown, never scored.
- **The gap.** For each collection, the distance between its runway position and its advertising
  position (one minus their cosine), house by house through the seasons. A wide gap is a strategy (the
  show as statement, the advertising as commerce), not a fault, and no gap is called better.
- **Icons.** The share of a house's advertising pictures, in each window, closer to none of its last
  two runway positions than the median advertising picture of the season: what sells without coming
  from a show. Beside it, the share of the house's ads sorted as accessories, beauty, jewellery and
  watches, capsules and the holidays.
- **Influence.** Whether the other houses' runway positions move towards a house's position in the
  following season.
- **Movement in the advertising.** Shown, never scored. Newness is the show's job; much advertising is
  built to repeat.
- **Attention.** Show heat beside the reach of the advertising that follows. The lift in attention
  from the day the campaign lands is Amendment 2's bridge (section 13); its registered tests are not
  repeated here.
- **The scene.** Fashion ambassador appointments in the 180 days before a show, shown beside each
  collection. Ambassadors front both the show and the advertising, so Amendment 2 holds them constant
  rather than credit either channel with them.

## 5. Limits

The runway rests on the pages the Wayback Machine kept on the houses' own sites
(`data/clothes/looks_coverage.json`), which cover some houses and seasons and not others; the test
runs on the collections the archive allows and reports which. The advertising is Meta's archive of EU
and UK ads, which starts in 2023 at the earliest and keeps about a year, so the test waits until 30
collections have two consecutive campaign windows read. The sorting by text knows five languages and
the words it lists; an ad in another language, or with no text, falls to its picture. The Thread dates
too few spring-summer campaigns (6) to check the window against them. The homepages are what a house
chose to show at the front of its site, mostly campaign pictures, not its whole shop. The reader errs;
the rubric's rule bounds how much.

## Before freezing

The kept questions of `rubric/clothes-v1.md` written down once the labels have been given; the
coverage map and the runway collection finished, and the collections they cover recorded; the
collector sorting each ad by its text in place and tested before the first collection; Amendment 2
frozen with its campaign window and its bridge as amended on 11 October 2026; this file and its hash
committed together.
