# Crews: separating photographers from houses

`adtone.crews` ties each collected ad concept to the crew that made it, then splits concept tone into
a house part and a photographer part. It is exploratory. Amendment 2, while still a draft, would make
it a registered descriptive analysis, still without a hypothesis test.

    python -m adtone.crews               # the house and photographer graph, from the credits alone
    python -m adtone.crews --decompose   # with collected data; the analyse workflow runs this weekly

## Linking a concept to a crew

Credit rows (`reference/credits.csv`, plus whatever the backcat workflow has read) are grouped into
campaign entries. A concept is linked:

1. **By image** when one of its images matches a back-catalogue image of a campaign (the matcher
   `adtone.calibrate` uses, keyed by the campaign's models.com page).
2. **By date** otherwise. A campaign launched within 7 days of the concept's first run, or listed for
   the month that contains it, takes the concept. Failing those, the house's most recent earlier
   campaign within 150 days takes it.
3. **Not at all** when the campaigns that qualify were shot by different photographers. A tie is left
   unlinked rather than guessed.

Only fashion advertising entries that name a photographer and carry at least a month are used. A
concept credited to several photographers gives each an equal share.

## The decomposition

Concept tone (the analysis residuals, so each month's field is already removed) is modelled as a
house effect plus a photographer effect plus noise, the firm-and-worker split labour economists use
for wages. Only photographers who work for more than one house can tell the two apart, so the model
runs on the largest set of houses those photographers connect. Houses outside that set are reported
and left out.

Two sets of shares come back, each as a fraction of total variance: house, photographer, their
covariance, and residual. The naive shares overstate the photographer part when each photographer
has only a few concepts, because noise in each estimate reads as spread between photographers. The
corrected shares subtract that expected bias, assuming equal noise across concepts. On synthetic
data with three concepts per photographer, the naive photographer share came out about 0.40 against
a true 0.04, and the corrected share was right on average.

`by_era` repeats the split with each house divided by creative director, where the link names one.

## What identifies what

Designers and their crews moved together in 2025 (`docs/CREDITS.md`), so a debut cannot separate a
designer from a photographer. What separates photographers from houses is photographers crossing
between houses within the window, such as David Sims, Steven Meisel and Glen Luchford. From the
seed credits alone, 14 of the 16 covered houses fall in one connected set. Jil Sander and Margiela
sit outside it.

## Limits

- Date links are coarse wherever a house runs several campaigns a month. Image links fix that only
  where the back catalogue holds the campaign's pictures.
- The bias correction assumes equal noise. With strongly unequal noise it is approximate.
- Shares are reported only with at least 3 linking photographers and 10 spare degrees of freedom.
  Below that, the output says insufficient rather than printing numbers.
