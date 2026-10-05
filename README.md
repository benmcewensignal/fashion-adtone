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

## Status, 5 October 2026

- Built and tested on synthetic data with known ground truth (88 tests).
- Not yet run against the live API. Meta was unreachable from the build environment, so the API
  client, the render-page parsing and the embedder download are untested in production. The
  first live runs are the real test, and the probes fail a run loudly when a step returns nothing.
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

- Confirm the automatic page picks. They are listed in the collect run's summary and in
  `data/registry/page_candidates.json`. Copy the right ids into `registry/houses.yml` and set its
  status to FROZEN. A house with no automatic match needs its id found by hand: open the house in
  the public Ad Library and read `view_all_page_id` from the URL.
- Renew the Meta token every 60 days or so.
- Code the 120-image agreement sample once a few hundred images are scored (analyse workflow,
  agreement-sample mode).

Optional repository variables: `GRAPH_API_VERSION` (default v26.0), `ADTONE_CLAUDE_MODEL` (default
claude-sonnet-5-5; part of the instrument's identity, so changing it starts a separate series), and
`ADTONE_RESOLVER` (auto, static or browser).

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

## Forward tests

Predictions registered before the event they predict live in `forward/`, each frozen by hash, with
later facts (a successor's name and first show) recorded in `forward/events.yml`. A test computes
nothing until its adjudication date and is never recomputed once recorded. The first,
`saint-laurent-v1`, was registered on 5 October 2026 while Anthony Vaccarello's reported departure was
unconfirmed and no successor had been named. Recording the successor and the date of their first show
in `events.yml` is all it needs.

## Relation to fashion-position

This is the advertising track in the fashion-position architecture: a T1 source, derived data
only, with its own rubric and versioning rules. It is a separate repo because fashion-position was
not reachable from the build environment. The layout follows signal-sonic's conventions so the two
can merge.
