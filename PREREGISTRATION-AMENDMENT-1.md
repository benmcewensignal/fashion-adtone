# Amendment 1 to the pre-registration

Dated 5 October 2026, before any ad was collected. `PREREGISTRATION.md` stays frozen and unchanged;
where the two differ, this amendment governs. It applies equally to `forward/saint-laurent-v1.md`,
whose measure follows the pre-registration.

## Change

Campaign blocks start from the pre-registered 21-day gap rule, and any block spanning more than 35 days
is cut at calendar-month boundaries. A discrete campaign stays one block even across a month boundary;
a house that launches continuously gets one block per month. Every null still permutes whole blocks. The
pure 21-day gap rule becomes a sensitivity analysis, reported for H1 alongside the primary result.

## Why

The calendar-realistic simulation in `docs/POWER.md`, run before any data:

- A house that launches brand imagery more often than every three weeks has all its concepts merged into
  one block by the gap rule, and then nothing about it can be tested. At 24 launches a year the gap rule
  left 0 of 10 treated houses testable; the hybrid rule left 8.
- Plain calendar months fixed that but split discrete campaigns across month boundaries, which made the
  null too narrow: in a synthetic world with planted shifts, two of six controls showed false
  changepoints, and H3's control check failed. The hybrid rule keeps discrete campaigns whole and removed
  those false positives.
- Where launches were monthly, the hybrid and gap rules performed the same. In worlds with no effect both
  gave false H1 support in 1 of 10 runs, too few runs to estimate the rate.

## Expectations recorded before data

These change no test. They say in advance what an untestable result would mean.

- H1 is testable only if brand-image ads run long enough for pre-debut advertising to survive in the
  repository. With a median run of three weeks about 1 of 10 treated houses had a testable before side;
  with two months, about 6.
- Collection timing matters as much. Under the middle assumptions, a first backfill on 1 December 2026
  instead of 6 October left about 1 of 10 treated houses testable, below the 4 that H1 needs.
- Celine and Maison Margiela were never testable in simulation; Chanel and Fendi always were.
