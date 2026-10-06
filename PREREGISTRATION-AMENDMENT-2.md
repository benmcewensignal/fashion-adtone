# Amendment 2 to the pre-registration

STATUS: DRAFT. It binds once frozen: its SHA-256 in `PREREGISTRATION-AMENDMENT-2.sha256`, committed
with the frozen registry and timestamped on the public record, before the first collection run.
Until then everything below is exploratory and reported as such.

## What it does not change

H1, H2 and H3 and the Saint Laurent forward test stand exactly as frozen on 5 October 2026, on the
core panel (`tier: core` in `registry/houses.yml`, the original 19 houses). Every analysis here is
additional, runs in `adtone.family` or the outcome evaluator, and is reported separately.

## Already in the frozen design

Three points raised in review are frozen already and are restated, not amended:

- The primary population is brand-image concepts outside fragrance and beauty, jewellery and
  watches, eyewear and home. Packshots, sale creatives and beauty licences never enter H1 to H3.
- The reader sees only the image: no house name, no ad text, no date or page. Each image is scored
  in its own call at temperature 0 against the hashed rubric, so order cannot carry information.
- The detection rule (p below 0.05, a break 0 to 120 days after the event) is fixed in the frozen
  files for H3 and for Saint Laurent. No treated house chose it.

## 1. The extension panel

| house | role | event, as registered (unverified until checked) |
|---|---|---|
| Versace | in the September cluster; destination of the Mulier move | Dario Vitale, first show 26 September 2025; left December 2025; Pieter Mulier, first show September 2026 |
| Givenchy, Tom Ford, Dries Van Noten | debuts in March 2025, before most of the retained window: post-debut houses for the outcome test and the photographer graph | Sarah Burton, Haider Ackermann, Julian Klausner |
| Alaïa | origin of the Mulier move, outside every comparison | Mulier's Alaïa tenure ends after the March 2026 show |
| Dolce&Gabbana, Max Mara, Brunello Cucinelli, Zegna | controls: leadership held through the window | none |

Extension controls join the control field and the placebo pool for every analysis in this
amendment; core controls only is a declared sensitivity. Before the freeze, every extension date is
checked against published show schedules and every page id confirmed, as for the core panel; a
candidate control whose leadership changed is dropped, and the drop is logged in `SELECTION.md`.
Scoring cost rises by about half.

## 2. The reader

- The instrument is the `tone-v1` rubric read by the model named in `config.CLAUDE_MODEL`. Every
  score is stored with its instrument string, the analyses refuse mixed instruments, and a change of
  reader is a new instrument, re-scored in full and reported separately.
- The shift and transfer tests run on image embeddings from a fixed open model; the reader's answers
  decide which ads count (the brand-image filter) and describe what changed. Both have seen fashion
  imagery, so neither is blind to a logo in the frame. The declared sensitivity below removes the
  most direct cue: images whose modal `text_in_image` is anything other than `none`.
- A second reader, a different model named at the freeze, scores a 10 per cent sample stratified by
  house and side, beside the existing human check. Per-field agreement (Cohen's kappa) is reported.
  If agreement on `creative_type` is below 0.6, every test here is re-run on concepts both readers
  call brand images.

## 3. Transfer is a family

Each move is scored both ways. Toward: does the destination move towards the look the designer
left, by the frozen H2 statistic (transfer above zero, block-permutation p, specificity by ridge
regression over every reference tone and the destination's own earlier tone, λ = 0.1). Away: does
the origin move away from it, read as the origin's own shift z with H1's statistics.

| move | origin, destination | who travelled with the designer, from the credits |
|---|---|---|
| Demna | Balenciaga, Gucci | the designer himself, credited as photographer at both (H2, frozen) |
| Matthieu Blazy | Bottega Veneta, Chanel | photographers and film: Alec Soth, Louise and Maria Thornfeldt, Rahim Fortune |
| Jonathan Anderson | Loewe, Dior | a team: David Sims, Benjamin Bruno, Benoît Delhomme, Poppy Bartlett |
| Maria Grazia Chiuri | Dior, Fendi | nobody on file: the contrast |
| Pieter Mulier | Alaïa, Versace | not yet known; credits are added before Versace's post side exists |

- A toward leg is supported if the transfer is above zero, specific, and its Holm-adjusted p is at
  most 0.05 over the four non-frozen moves (Blazy, Anderson, Chiuri, Mulier), a move that cannot be
  tested still counting in the four. H2 keeps its own frozen criterion and enters the reading below,
  not the Holm family.
- Balenciaga towards Piccioli's earlier work is tested only if the back catalogue of his Valentino
  campaigns meets the matched-pair calibration rule; otherwise it is reported as untestable.

The reading is fixed now, before any data:

| pattern | reading |
|---|---|
| Chiuri transfers | the look travels with the designer, even alone |
| Blazy and Anderson transfer, Chiuri does not | the look travels with the team |
| only Blazy transfers | the look belongs to the photographers |
| only Demna transfers | designer and photographer, inseparable in this panel |
| none transfers | no evidence that a look travels |

The crew decomposition (house and photographer shares of tone, bias-corrected) is reported beside
the family, descriptively.

## 4. One shock, not seven

- The pooled shift averages the shift z of every treated house that debuted between 23 September
  and 6 October 2025, Versace's first event included, and compares the average with averages of
  control shifts at fashion-week placebo dates (2 March, 23 June and 28 September 2026), drawing the
  same number of placebo z's 20,000 times. Supported if p is at most 0.05. It needs three cluster
  houses and six placebo shifts.
- Placebo shifts at one date share the same press weeks, so they are only roughly exchangeable; the
  per-date breakdown is reported. Amendment 1's 21-day gap rule stays as the block sensitivity.
- Valentino stays a control: Alessandro Michele's tenure covers the whole window. Miu Miu and
  Valentino both changed campaign art direction in 2026, so the analysis is repeated without them.

## 5. The ad mix is an outcome

For each debut, the total variation distance between the shares of creative types before and after,
over all concepts, is reported against the same distance for controls at the placebo dates, and the
same for categories. A debut that changes the mix has changed something real, and it is shown
rather than filtered away.

## 6. Declared sensitivities

Every test in this amendment is repeated on: concepts with mean reader confidence of at least 0.6;
concepts without a logo or words in the image; core controls only; controls without the two
art-direction changes. Weighting averages by confidence would change the frozen estimator, so the
confidence subset is the declared form.

## 7. Detection thresholds are locked on the controls

No detection parameter is tuned on a treated house. Any recalibration is fitted on controls alone,
versioned, and applies only to events after it. Every detection result is reported with the control
false-positive rate: the share of control scans with p below 0.05.

## 8. From reading to result

- **Unit.** House-months, every core and extension house except the watch houses, in months with at
  least four brand-image concepts.
- **Four readings**, on residuals against the control field: R1, how far the month's look moved from
  the house's own past (cosine distance between the month's centroid and its trailing six-month
  centroid); R2, how far it sits from the market (length of the month's mean residual); R3, how
  consistent it is (mean cosine of the month's concepts to their centroid); R4, drift towards the
  house of the moment (cosine between the month's centroid and the look of the Lyst leader recorded
  for that quarter in `reference/lyst_leaders.csv`).
- **Reach control.** The log of one plus the summed EU reach of the ads first seen that month, and
  the log of one plus the number of ads active.
- **Outcomes.** Primary, curiosity: the change in log monthly Wikipedia page views from one month to
  the next, less that month's median change across all houses. Secondary curiosity: news volume and
  mean tone from GDELT; search interest only if the Trends API is in use at the freeze. Desire: Lyst
  rank changes under the new method, quarterly from Q1 2026. Money: quarterly revenue growth at the
  seven houses that report it.
- **Model.** Next month's outcome on the four readings, the outcome's own last change (momentum),
  the reach control and a debut marker for the six months after an event, with house intercepts,
  standardised readings and ridge λ = 1. The baseline is the same model without the readings.
- **Lag.** One month primary; two and three months secondary.
- **Months that count.** Only calendar months after both the freeze and six months of live
  collection. Earlier months train the model and never score it. Each month the model is refitted
  on every earlier month and predicts the next.
- **Decision.** Skill is one minus the model's mean squared error over the baseline's, across the
  scored months, with a 90 per cent interval from a moving-block bootstrap over months (blocks of
  three). Curiosity is supported after at least 12 scored months if skill is above zero and the
  interval's lower bound is above zero. Desire and money get the same score once eight quarters are
  scored, and are descriptive until then.
- **Reading.** Readings that predict curiosity but not desire mean the look got people talking
  without creating demand.

## 9. What to lead with

The power simulation behind Amendment 1 (`docs/POWER.md`) is published on the site at the freeze.
Ten houses and one clustered fortnight cannot support a house-by-house league table, so results
lead with the pooled shift, the transfer family and the control false-positive rate. Per-house
results appear in an appendix, unranked.

## 10. Kept from the earlier draft

- Change and attention, described: for each treated house, the log ratio of mean daily Wikipedia
  views in the 182 days after the debut over the 182 days before, less the controls' median change,
  with at least 80 per cent of days present on each side, and Spearman's rank correlation with the
  shift and a permutation p.
- Lyst leader drift, described, from `reference/lyst_leaders.csv` at this commit. The Lyst Index
  is not a measure of success here: it changed method in Q1 2026 and began counting Chanel and Dior.

## Before freezing

Extension dates verified and page ids confirmed; the second reader named; the Trends API status
recorded; the outcome evaluator and second-reader code in place and tested on synthetic worlds with
known answers. Then this file and the registry are hashed, committed and timestamped together.
