# Amendment 2 to the pre-registration

STATUS: DRAFT

Drafted 6 October 2026, before any ad was collected. It registers nothing until it is frozen: status
set to FROZEN, its sha256 written to `PREREGISTRATION-AMENDMENT-2.sha256`, committed, and timestamped
publicly, all before the first collect run. Until then the analysis reports what it describes as
exploratory. `PREREGISTRATION.md` and Amendment 1 stay frozen and unchanged; this amendment only adds.

## What it adds

### H2b and H2c: the other designer moves inside the panel

The credits table (`reference/credits.csv`) shows two more creative directors who moved between
collected houses in 2025:

- **H2b.** Matthieu Blazy, Bottega Veneta to Chanel: Chanel's advertising moves towards Bottega
  Veneta's tone before Louise Trotter's debut.
- **H2c.** Jonathan Anderson, Loewe to Dior: Dior's advertising moves towards Loewe's tone before the
  debut of Jack McCollough and Lazaro Hernandez.

Each is tested exactly as H2 is, with `analysis.mover` unchanged: the same windows, permutations of
whole campaign blocks, the ridge regression over every reference tone and the destination's own
earlier tone, and the specificity rule. Holm-adjusted p-values are computed over the pair, counting
both tests even if one cannot run. A test is supported if its transfer is positive, its adjusted p is
0.05 or below, and the origin carries the largest coefficient. H2 is unchanged and keeps its own
criterion.

### How a transfer result is read

In all three moves the people behind the camera moved too, according to the credits table at this
commit. Demna is credited as photographer at both Balenciaga and Gucci. Alec Soth, Louise and Maria
Thornfeldt and Rahim Fortune worked with Blazy at both Bottega Veneta and Chanel. David Sims, Benjamin
Bruno, Benoît Delhomme and Poppy Bartlett worked with Anderson at both Loewe and Dior. A supported
transfer is therefore reported as a transfer of the creative team, not of the designer alone. H1 is
read the same way: a house's shift after a debut is a shift in its creative team.

### A descriptive decomposition

`adtone.crews` splits concept residuals into a house part and a photographer part. It works on the
largest set of houses connected by photographers who worked for more than one of them, and corrects
the bias that comes from having few concepts per photographer. Concepts are tied to crews by image
match first, otherwise by the date rule in the code at this commit. Shares are reported only with at
least 3 photographers linked at two or more houses and 10 spare degrees of freedom. It is descriptive:
no hypothesis, no p-value, no claim of support.

## Expectations recorded before data

These change no test.

- The origin sides are the hard part. Ads leave the repository a year after they last ran. Bottega
  Veneta's ads from before Trotter's first campaign (January 2026) and Loewe's from before the new
  directors' first campaigns (a teaser in September 2025, the main campaign in February 2026) may
  already be thin by the first backfill. Either test may report insufficient data. That would be a
  result about the window, not about the hypothesis.
- From the seed credits alone, 14 of the 16 covered houses fall in one connected set. Jil Sander and
  Margiela sit outside it and stay out of the decomposition unless their crews cross into it.

## What it does not change

H1, H2 and H3 and their thresholds, the block rule, the field built from the control houses, and the
forward test.
