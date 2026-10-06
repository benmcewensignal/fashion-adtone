# Change and success, broadly

Success has no clean house-level series, so `adtone.success` asks two narrower, descriptive
questions. Neither can show that a change worked.

    python -m adtone.attention collect   # weekly workflow: daily Wikipedia page views per house
    python -m adtone.success             # analyse workflow, after the main analysis

## What was available, and why page views

- **Revenue** is reported by house for only a few of the eighteen. Kering separates Gucci, Saint
  Laurent and Bottega Veneta, with Balenciaga inside its other houses. LVMH reports by division, so
  Dior, Loewe, Celine, Fendi, Louis Vuitton and Loro Piana cannot be separated. Of the ten changed
  houses, only Gucci and Bottega Veneta report quarterly, and Chanel yearly.
- **The Lyst Index** ranks brand heat each quarter, but it changed its method in the first quarter of
  2026 and counted Chanel and Dior for the first time that quarter. Its ranks either side of the
  debuts do not compare, and Chanel's first place in 2026 is partly an effect of being counted at all.
- **Wikipedia page views** are counted the same way throughout, for every house, every day, free and
  without a key. They measure attention, which a debut produces whether or not it works.

## The association

For each changed house: its measured shift (the event-study z) against its attention change, the log
ratio of mean daily views in the 182 days after the debut over the 182 days before, less the median
change of the control houses over the same days. Spearman's rank correlation, a permutation p-value,
and `bar_95`, the correlation needed to stand out from noise with that many houses (about 0.6 with ten).

## Leader drift

Whether the other houses' advertising moves towards the house Lyst ranks first
(`reference/lyst_leaders.csv`: Saint Laurent for the second half of 2025, Chanel for the first half
of 2026). For each leader spell, each other house's similarity to the leader's look in the spell is
compared with its similarity in the six months before, and the average change is ranked against the
same change towards every other house in the leader's place.

On synthetic data the leader ranked first about as often as chance when nothing converged, and in
most runs when houses moved partly towards it. When nearly every house converges heavily, the
stand-in leaders inherit the leader's look and the rank understates it. A large mean change with a
modest rank therefore means heavy convergence, not none. With seventeen stand-ins the best possible
p-value is about 0.06, so the output reports a rank rather than claiming significance.

## What it cannot say

Houses change designers when they are struggling. A new designer changes product, prices, stores and
crew at once. Attention follows any debut. Chanel took the Lyst lead in the quarter the method changed.
Everything here describes association; none of it identifies an effect.
