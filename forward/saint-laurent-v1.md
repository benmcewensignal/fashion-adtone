# Forward test: Saint Laurent after Vaccarello

Registered 5 October 2026. STATUS: FROZEN. This file's hash is in `forward/saint-laurent-v1.sha256`
and the test suite fails if the file changes. Facts that arrive later (the successor's name, the first
show, any control's leadership change) are recorded in `forward/events.yml`. The design reads those
facts; it cannot be altered by them.

## State of the world at registration

- Puck reported on 10 September 2026 that Anthony Vaccarello will leave Saint Laurent after the
  Spring 2027 show of 29 September 2026. AFP repeated the report on 29 September and noted that the
  house had not confirmed it. On 30 September Kering still listed Vaccarello as creative director.
  No successor had been named.
- Speculation named Chemena Kamali, creative director of Chloé. Chloé is added to collection today
  as a watch house, so that if she moves, her previous house's advertising has been archived first.
- This project had collected no ads.

## The event

D is the date of the first show or presentation of a Saint Laurent collection credited to a newly
named creative director. A collection credited to the studio does not set D. If no such show happens
by 31 December 2027, the test is void.

## Prediction 1: magnitude

Saint Laurent's brand-image advertising moves more after D than control houses move at placebo dates.

- Measure and windows as in PREREGISTRATION.md: residuals against the control field, pre before D,
  post from D + 90 days.
- The field excludes Saint Laurent and any control whose creative leadership changes between
  D - 180 days and adjudication.
- Placebos: each remaining control's z at splits D + 30k days, for k from -3 to 3.
- Hit if Saint Laurent's z exceeds the 90th percentile of the placebo z values; miss otherwise.
  Requires at least 3 controls with a placebo and sufficient Saint Laurent data (at least 8 concepts
  and 3 blocks on each side).

## Prediction 2: timing

The changepoint scan of Saint Laurent's concepts from D - 365 days to adjudication puts its best split
0 to 120 days after D, with p < 0.05. Hit or miss.

## Prediction 3: transfer, conditional

Applies only if the successor's previous house is a panel or watch house. The origin is that house's
concepts in the 365 days before the successor was announced. Transfer and specificity follow H2 in
PREREGISTRATION.md, with Saint Laurent as destination and its own pre window in the regression. Hit if
the transfer is positive, p is at most 0.05 and the origin carries the largest coefficient among the
other houses. Otherwise a miss; not applicable if the successor came from elsewhere.

## Adjudication

Once, at D + 270 days, which allows 180 days of post data, using the instrument in use at the time.
Nothing about Saint Laurent is computed for this test before then, so there is no peeking. If the data
are insufficient at D + 270, adjudication waits until they are sufficient, up to D + 450; insufficient
then is recorded as no result. A result once recorded is never recomputed.

## Publication

Results go in CLAIMS.md. The claim counts as open from the later of D and the end of the silent baseline
season, six months after the first live collection run.

## Machine-readable specification

```json
{
  "id": "saint-laurent-v1",
  "house": "saint_laurent",
  "registered": "2026-10-05",
  "event_key": "saint_laurent_successor_debut",
  "announce_key": "saint_laurent_successor_announced",
  "origin_key": "saint_laurent_successor_previous_house",
  "void_after": "2027-12-31",
  "post_lag_days": 90,
  "adjudicate_after_days": 270,
  "deadline_days": 450,
  "control_change_lookback_days": 180,
  "placebo_offsets_days": [-90, -60, -30, 0, 30, 60, 90],
  "placebo_quantile": 0.9,
  "min_controls": 3,
  "changepoint_days_before": 365,
  "changepoint_max_days_after": 120,
  "alpha": 0.05,
  "origin_window_days": 365
}
```
