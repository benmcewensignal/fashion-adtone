# fashion-adtone

Measures tonal shifts in luxury fashion advertising, using the EU ad repositories.

Under Article 39 of the Digital Services Act, Meta keeps every ad delivered in the EU for a year
after it last ran, with reach and targeting attached, and offers an API (UK ads are included
too). This repo collects the paid digital advertising of a panel of luxury houses from that
repository, scores the tone of every image against a frozen rubric, and tests whether the houses
whose creative director changed in 2025 changed the tone of their advertising more than houses
whose leadership did not.

It measures how houses present themselves in paid digital channels. It does not measure what
their clothes look like.

## Status, 7 October 2026

- While Meta's access is pending, the sources that need no one's permission are running: each house's
  homepage month by month since 2014 from the Wayback Machine, read by the reader (`adtone.homepages`);
  Wikipedia views in ten languages and Wikidata's record of owners and creative directors
  (`adtone.wikiviews`, `adtone.wikidata`); GDELT news (`adtone.press`); and revenue by house, shows,
  credits, show statements and media value compiled by hand into `reference/`, each row with its
  source. YouTube (`adtone.youtube`) and TikTok's ad library (`adtone.tiktok`) are built and wait for
  their keys. `adtone.character` reads character, shift and its relation to growth from the
  homepages; `adtone.readings` takes that further (which answers can be tracked, each brand's change
  like for like and net of the market, turnover against drift, across brands, the market, designer
  changes beside brands without one, and success); both are exploratory. `adtone.bakeoff` tests larger
  readers, pairwise questions written for luxury pictures and style models against today's reader on
  about 300 pictures, and `adtone.luxury` fetches every homepage picture again for a second reading
  by the winner (pictures stay on a private volume, never in this repository). A second instrument
  version (`rubric/tone-v2.md`, `rubric/pairs-v2.md`, and composition measured from the pixels by
  `adtone.composition` after `rubric/composition-v1.md`) is frozen before it reads anything and tested
  on the bake-off's pictures by its own fixed rules. See `docs/JOINS.md` for every source and its
  workflow.
- Built and tested on synthetic data with known ground truth (358 tests).
- Not yet run against the live API. Meta was unreachable from the build environment, so the API
  client, the render-page parsing and the embedder download are untested in production. The
  first live runs are the real test, and the probes fail a run loudly when a step returns nothing.
- `PREREGISTRATION-AMENDMENT-2.md` is a draft dated 6 October 2026. Frozen before the first collect run,
  it would register two more designer-move tests (Blazy, Bottega Veneta to Chanel; Anderson, Loewe to
  Dior) and the crew decomposition. Until then both are reported as exploratory.
- `PREREGISTRATION.md` was frozen on 5 October 2026, before any data. Debut dates in
  `registry/houses.yml` are verified; the registry stays DRAFT until its page ids are confirmed.
  Analysis refuses to run until both are frozen.

## How it works

1. **collect** pulls every ad delivered in the EU27 and the UK from the panel's Facebook pages, and
   keeps metadata, reach marginals and copy features.
2. **process** opens each ad's render page, fetches the images it showed, embeds them with a pinned
   OpenCLIP model, scores them against `rubric/tone-v1.md` with Claude, and discards the pixels.
   Ads go in order of how soon they leave the repository.
3. **analyse** collapses ad variants into concepts, groups concepts into campaign blocks, measures
   each house against a field built from the control houses, and runs the three pre-registered
   tests. Results land in `data/results/`.

## What is kept and what is not

The repo is public for unlimited Actions minutes, so:

- Images never reach a commit. They are held in memory, and `.gitignore` and the persist guard
  are the backstops.
- Ad copy is kept as features and a hash, never as text.
- The API's snapshot URL is never requested, because it can carry the caller's access token.
  Render URLs are rebuilt in memory from the ad id.
- Vectors, rubric scores, reach marginals and targeting fields are kept as derived data.

Each file under `data/` is owned by one workflow. That is what makes the persist step safe: after a
push race it lays a workflow's outputs back onto the new head, which only works when nobody else
writes the same files.

| workflow | writes |
|---|---|
| collect, resolve-pages | `data/ads/`, `data/state/collect.json`, `data/provenance/collect.jsonl`, `data/registry/page_candidates.json` |
| process | `data/media/`, `data/obs/`, `data/vectors/`, `data/state/process.json`, `data/provenance/process.jsonl` |
| attention | `data/attention/`, `data/state/attention.json`, `data/provenance/attention.jsonl` |
| analyse | `data/results/`, `data/provenance/analyse.jsonl`, `data/human_check/` |

Every workflow carries the signal-sonic lessons: persist by replaying onto the new head with five
retries, a timeout on every step that can hang, a completeness probe that counts from disk instead
of trusting the step's own report, and a daily watchdog for scheduled runs that never fired.

## Setup

Three things need a person:

1. A long-lived Ad Library token. Confirm your identity and location at facebook.com/ID (the
   same check political advertisers do; your phone camera works), create a basic app at
   developers.facebook.com, generate a User Token for it in the Graph API Explorer, then extend it
   to about 60 days with "Extend Access Token" in the Access Token Debugger. No app review is
   needed. In mobile Safari, switch on Request Desktop Website for the developer tools.
2. This directory pushed to a new public repository.
3. Secrets: `META_AD_LIBRARY_TOKEN` and `ANTHROPIC_API_KEY`. Optional `META_APP_ID` and
   `META_APP_SECRET` let collect warn ten days before the token expires.

Then it runs itself. Within a day of the token appearing the watchdog dispatches the first backfill
(or run **collect** by hand to start at once). That first run finds each house's page by itself:
an exact name match among the pages advertising under the house's name, skipping beauty,
fragrance and reseller pages. Looser matches are listed as suggestions and not collected,
because keyword results for some houses (Hermès especially) are crowded with resellers. Processing starts when collection finishes, falls back to headless
Chromium for any ad the plain render page cannot resolve, and drains the backlog every six hours.
Analysis waits until both files are frozen.

What is left for a person afterwards:

- Confirm the automatic page picks. The status page lists them. Run **confirm-pages** with accept
  set to all, add any ids found by hand as overrides (house=id), and tick freeze once every panel
  house has a page. A house with no automatic match needs its id found by hand: open it in the public
  Ad Library and read `view_all_page_id` from the URL.
- Renew the Meta token every 60 days or so.
- Code the 120-image agreement sample once a few hundred images are scored (analyse workflow,
  agreement-sample mode).

A status page built for a phone (collection per house, backlog, page picks, token expiry, forward
tests, and what needs you) publishes to GitHub Pages after every run. One-time setup: Settings > Pages
> Source: GitHub Actions.

Two optional keys widen the sources, and each workflow waits, recording that it waited, until its
key exists:

- `YOUTUBE_API_KEY`: free. In the Google Cloud console create a project, enable "YouTube Data API v3",
  then Credentials > Create credentials > API key. The weekly youtube workflow then reads every film
  on the houses' channels (`reference/youtube_channels.csv`; twelve channels there are marked
  unverified and the first run checks each channel's title against the house).
- `TIKTOK_CLIENT_KEY` and `TIKTOK_CLIENT_SECRET`: apply for the Commercial Content API at
  developers.tiktok.com (TikTok says it answers within two working days); the keys then appear in the
  research section of the developer site. The weekly tiktok workflow then reads the houses' ads in
  Europe, kept by TikTok for a year after they last ran.

Optional repository variables: `GRAPH_API_VERSION` (default v26.0), `ADTONE_CLAUDE_MODEL` (default
claude-sonnet-5-5; part of the instrument's identity, so changing it starts a separate series), and
`ADTONE_RESOLVER` (auto, static or browser).

## Before reading any result

`docs/POWER.md` simulates the real calendar. Whether the main test can run at all depends less on
effect size than on two things the first backfill will reveal: how long brand-image ads run, and how
often they launch. Collection timing matters too: under middle assumptions, starting in December 2026
instead of early October leaves too few testable houses. Amendment 1 (`PREREGISTRATION-AMENDMENT-1.md`,
dated before any data) changed how campaign blocks are formed for that reason.

## Limits known before any data

- **Coarse per-house tests.** A year of advertising holds only a few campaign blocks per house. With
  three blocks a side, the exact test cannot produce a p-value below 0.05, and every result reports
  its floor. The cross-house comparison is where the power is.
- **Timing.** Advertising changes with the new director's first campaign, months after the show. For
  the July 2025 debuts (Celine, Margiela) little pre-change advertising is likely to remain in the
  repository, and for the September and October debuts the before side is mostly the last old
  campaign still running. Each month before the first backfill shrinks it further. Houses without
  enough data are reported as insufficient, not tested.
- **One channel.** Paid digital ads delivered in the EU and UK. No print, outdoor or organic posts.
- **One-year memory.** History older than a year has left the repository, and exists here only if it
  was collected in time.
- **Re-scoring.** Pixels are not kept, so a new rubric version can only see ads still in the
  repository. A private raw archive would remove that limit. That is a decision for later, not a
  default.

## Back catalogue

The Meta repository forgets after a year, so older campaigns come from two other sources (the
**backcat** workflow, monthly):

- **models.com** gives the spine: each house's campaigns with crew, publication date, the page the
  campaign was published on, and picture and film counts. It serves no images to an anonymous reader.
- **The Wayback Machine** gives the images, from captures of that source page made around the
  publication date. Campaigns published on Instagram stay metadata-only, because the archive holds
  login walls there.

Both are read politely (robots.txt honoured, a pause between requests) and under the same rule as
everything else: derived rows only, no images committed. Coverage is unknown until the first run, and
the probe reports it.

A different source is a different sensor, so the back catalogue is never pooled with the live series.
`adtone.calibrate` finds campaign images that reappear in the paid Meta ads during the overlap year,
measures the offset between the channels from those matched pairs, and splices the back catalogue on
only with at least 20 pairs. None of the v1 pre-registered tests use the back catalogue; it exists for
later registered questions, such as the decade-long arc towards and away from streetwear, and separating
photographers from houses through the credits.

## Campaign credits

`reference/credits.csv` is a hand-made seed of campaign crew credits: 353 rows for 18 of the panel's
houses, each with its source page, read on 6 October 2026. It records who shot, filmed, styled, cast
and art-directed each campaign, never who appears in it. `python -m adtone.credits` summarises it:
who stayed through each debut, who moved with each designer, and which photographers work for several
houses. It is groundwork for separating photographers from houses, a later registered question, and
nothing in the v1 tests reads it. Details and soft spots are in `docs/CREDITS.md`. `adtone.crews` ties each
collected ad concept to a campaign's crew and splits tone into house and photographer parts, on the
houses that photographers working for several houses connect (`docs/CREWS.md`).

## Forward tests

Predictions registered before the event they predict live in `forward/`, each frozen by hash, with
later facts (a successor's name and first show) recorded in `forward/events.yml`. A test computes
nothing until its adjudication date and is never recomputed once recorded. The first,
`saint-laurent-v1`, was registered on 5 October 2026 while Anthony Vaccarello's reported departure was
unconfirmed and no successor had been named. Recording the successor and the date of their first show
in `events.yml` is all it needs.

## Beyond the 18

`registry/houses.yml` has two tiers. The core tier is the frozen v1 panel of 19 houses, the only
houses H1 to H3 ever read. The extension tier, proposed by Amendment 2 and fixed when it is frozen,
adds Versace (Dario Vitale's season, then Pieter Mulier from Alaïa), the March 2025 debuts at
Givenchy, Tom Ford and Dries Van Noten, Alaïa as the origin of the Mulier move, and four candidate
controls: Dolce&Gabbana, Max Mara, Brunello Cucinelli and Zegna. Its dates stay unverified until
they are checked against show schedules before the freeze.

`adtone.family` runs the amendment's analyses: transfer as a family of five moves scored both ways,
with Chiuri, who moved with nobody on file, as the contrast and the reading of each pattern fixed in
advance; the late-September debuts pooled as one shock against fashion-week placebos; the change in
ad mix as its own outcome; and four declared sensitivities. Its results stay labelled exploratory
until Amendment 2 is frozen.

It also runs the checks that decide whether the 2025 season can be trusted at all: an in-time
placebo (treated houses split at a fake date inside their own pre-debut period), a September check
(controls scanned blind must not break in the same fashion weeks as often as the debuts), the shift
measured against controls of other owners only, and the kill rule. If the calibration fails, the
Saint Laurent prediction is not scored. `docs/CLOCK.md` estimates when each house's pre-debut look
leaves Meta's archive; Balenciaga's, which H2 needs, goes first, around 21 October 2026, and collection
now runs in deadline order.

## What the clothes add

`adtone.press` collects daily news tone and volume from GDELT for every house back to January 2017,
in the weekly attention workflow. `adtone.runway` then joins every source into one row per show
(`data/results/runway_events.csv`) and asks whether reception predicts lasting attention beyond the
show's spectacle and the house's momentum, within houses, against shuffled reception. Registered in
Amendment 2, section 13b. How the pieces join is in `docs/JOINS.md`.

## The Thread

`adtone.thread` follows every show of the panel's houses from 2015, one row each: the shows and their dates
from NOWFASHION's brand listings (read through Claude's fetcher, since the site turns GitHub's machines
away; 77 of the 86 verified shows on the same day), how far English
Wikipedia views jumped around each one, with a renamed article's earlier titles joined (heat, and
surprise against the house's earlier shows), how far
news volume jumped (press) and how the news wrote about it against its usual tone (tone), each also as a
z-score among the shows of the same season. Its output (`data/results/thread.json`, `data/thread/thread.csv`)
is descriptive: it computes no lasting attention and no relation to anything after a show, which are
Amendment 2's registered tests. NOWFASHION's write-ups are not used. Workflow `thread`, weekly.

Each show's collection is then followed through the stages on windows fixed in `adtone/thread.py`
(`data/thread/collections.jsonl`): the scene before it (ambassador appointments in the 180 days before),
the show, the shop window (the homepages in the six months after), the campaigns and other things the
house published (joined by the season their title names, or else by date, and sorted into collection,
accessories, beauty, jewellery and watches, capsules and other), and the advertising in Amendment 2's two
windows. Each record says which stages are filled. The show and the advertising are kept as separate
channels with their own measures; the shared description of the clothes that would let them be compared
is the next piece, and `adtone.looks` probes whether the houses' own sites, as the Wayback Machine holds
them, carry the runway looks it would read. The thread workflow runs the probe as mode `looks`.

## The clothes rubric

`rubric/clothes-v1.md` is the description the show and the advertising share: what is worn or shown,
its shape, colour, surface, material and register, in the terms of Fashionpedia's expert-built
ontology. It is a draft until Ben and a labeller trained in fashion have read it; then it is frozen
before any picture is read with it. Its questions are marked anyone (Ben's labels are the reference) or
trained (a trained labeller's are), and a question goes forward only if it varies, is stable across
crops, and agrees with its reference on runway looks and on advertising pictures separately.
`adtone.clothes` assembles the test set: about 150 runway looks from the collection pages the archive
keeps and 150 homepage pictures from the earlier bake-off, the reader's copies on the private Modal
volume and the labelling page's thumbnails sealed. Workflow `clothes`.

The runway layer itself comes from the same archive: `adtone.looks coverage` maps, for every show since
2015, the pages the Wayback Machine holds on the house's own site (each house's show sections first,
read whole, then the fortnight after any show they miss), and `adtone.clothes collect` keeps about
twenty looks of each show found, spread from first look to last, as copies on the private volume
(`data/clothes/looks_coverage.json`, `data/clothes/runway.jsonl`). Nothing is read until the rubric
is frozen. The clothes workflow runs both every three hours until they are complete.
`PREREGISTRATION-AMENDMENT-3.md` is a draft, not decided, of how the links between the show and the
advertising would be tested if they are tested at all.

## The reader

Each image's eighteen answers come from an open vision model, Qwen2.5-VL-7B-Instruct, on a Modal GPU
(`reader/modal_app.py`). Its weights are pinned to a recorded commit, and its replies are constrained
to the rubric's schema, then validated by the same code as the Claude reader, which stays available
with `ADTONE_READER=claude` and as the second reader. The reader workflow pins the weights once, deploys
the reader and runs a pilot that measures throughput, valid replies and cost per 10,000 images. It needs
the `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` secrets. Registered in Amendment 2, section 2e.

## Runway to attention

`adtone.runway` asks whether a show's attention lasts, and whether the campaign that follows carries it.
The attention half needs no advertising: page views now go back to July 2015, and the test compares
how well a show's unexpected attention lasts against equal jumps on ordinary days, so ordinary decay
is not mistaken for runway power. The advertising half, for shows inside Meta's archive, adds the
reach pushed after each show and how closely the campaign resembles the house's own show-period ads.
`reference/shows.csv` holds 35 verified show dates from September 2025 to October 2026; older
seasons come from the published fashion-week calendars. Registered in Amendment 2, section 13.

## Change and attention

There is no clean house-level measure of success. Revenue is reported by house for only a few of the
eighteen, and the Lyst Index changed its method in early 2026. So the **attention** workflow collects
daily Wikipedia page views for every house, and `adtone.success` describes two things. One is whether
the houses that shifted most also gained the most attention, relative to the controls. The other is
whether the other houses' advertising drifts towards whichever house Lyst ranks first. Both are
description, not effects (`docs/SUCCESS.md`).

## Site

`www/index.html` is the public page that frames the project: one static file, no images, no
scripts. It is live at https://adtone-benmcewen20-9581s-projects.vercel.app (Vercel project `adtone`,
first deployed 6 October 2026), separate from the timestamp record.
(`site/` is not used for it: the status workflow builds the GitHub Pages status page there.)

## Relation to fashion-position

This is the advertising track in the fashion-position architecture: a T1 source, derived data
only, with its own rubric and versioning rules. It is a separate repo because fashion-position was
not reachable from the build environment. The layout follows signal-sonic's conventions so the two
can merge.
