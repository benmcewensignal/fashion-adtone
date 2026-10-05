# Testability and power under the real calendar

Run on 5 October 2026, before any data, with `scripts/power.py`. Synthetic houses follow the real
registry: the ten treated houses' actual debut dates and the eight controls. Each simulated ad runs for
a log-normal time around a median, and only ads still running a year before collection starts survive,
as in the repository. Treated houses get a moderate planted shift; Gucci a planted transfer from
Balenciaga. Six runs per cell, ten for the null worlds, so read the percentages as rough.

| launches a year | median run | blocks | treated houses testable | H1 testable / supported | H2 | H3 |
|---|---|---|---|---|---|---|
| 6 | 21 days | gap | 0 of 10 | 0% / 0% | 0% / 0% | 100% / 0% |
| 6 | 60 days | gap | 0 of 10 | 0% / 0% | 0% / 0% | 100% / 0% |
| 12 | 21 days | gap | 1.0 of 10 | 0% / 0% | 0% / 0% | 100% / 100% |
| 12 | 60 days | gap | 5.7 of 10 | 100% / 100% | 67% / 67% | 100% / 100% |
| 24 | 21 days | gap | 0 of 10 | 0% / 0% | 0% / 0% | 0% / 0% |
| 24 | 60 days | gap | 0 of 10 | 0% / 0% | 0% / 0% | 0% / 0% |
| 6 | 21 days | hybrid | 0 of 10 | 0% / 0% | 0% / 0% | 100% / 0% |
| 6 | 60 days | hybrid | 0 of 10 | 0% / 0% | 0% / 0% | 100% / 0% |
| 12 | 21 days | hybrid | 1.0 of 10 | 0% / 0% | 0% / 0% | 100% / 100% |
| 12 | 60 days | hybrid | 5.7 of 10 | 100% / 100% | 67% / 67% | 100% / 100% |
| 24 | 21 days | hybrid | 1.7 of 10 | 0% / 0% | 0% / 0% | 100% / 100% |
| 24 | 60 days | hybrid | 8.0 of 10 | 100% / 100% | 100% / 100% | 100% / 100% |

Share of runs in which each treated house had a testable before and after side (hybrid blocks, 12
launches a year, 60-day runs): Chanel 100%, Fendi 100%, Bottega Veneta 83%, Gucci, Balenciaga and Loewe
67%, Jil Sander 50%, Dior 33%, Celine 0%, Maison Margiela 0%.

Null worlds (12 launches a year, 60-day runs): H1 falsely supported in 1 of 10 runs under both rules, too
few runs to estimate the rate; H3 in none.

Plain calendar-month blocks were also tried. They matched the hybrid rule here, but split discrete
campaigns at month boundaries, and in the unit-test world with planted shifts that produced false
changepoints in two of six controls.

Collection starting 1 December 2026 instead of 6 October (hybrid blocks, 12 a year, 60 days): about 1 of
10 treated houses testable, and H1 testable in no run.

## What it means

- Effect size is not the binding constraint. Where a test was possible, a moderate shift was found. The
  constraint is whether pre-debut advertising survives in the repository, which depends on how long ads
  run and when collection starts.
- The first backfill measures both unknowns directly: delivery start and stop dates give run lengths, and
  launch dates give cadence. Read them against this table before reading any result.
- The block rule moved to the hybrid (Amendment 1) because the gap rule fails outright for houses that
  launch brand imagery more often than every three weeks, and plain months miscalibrate discrete campaigns.
