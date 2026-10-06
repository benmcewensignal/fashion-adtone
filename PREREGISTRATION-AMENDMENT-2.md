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

The sample stays as frozen: ads delivered to the EU27 or the UK. The EU half is archived by law
(DSA Article 39); Britain is outside the DSA and archived on the same one-year terms by Meta's own
policy, which could change separately. So every test is repeated on ads with EU reach only, and the
reach control sums the per-country breakdown, which includes the UK, because `eu_total_reach` does not.

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

## 2b. The measure, named

- **The fingerprint** is the image embedding of OpenCLIP ViT-B-32 with the `laion2b_s34b_b79k`
  weights. The processing workflow records the weights file's SHA-256 on its first run, and the
  analyses refuse vectors made with any other.
- **The distance** in H1 is the Euclidean distance between the mean pre and post residual vectors,
  as implemented in `analysis._shift` at the frozen commit; the readings in section 8 use the same
  residuals. The eighteen rubric answers never enter a distance: they decide which ads count and
  describe what changed, nothing more. So no encoding of categorical answers can make a transfer.
- **A thin month** (fewer than four brand-image concepts, or fewer than two campaigns) gets no
  reading. Nothing is imputed.

## 2c. Deduplication

Identical and near-identical images (pHash within 6 of 64 bits) collapse into one concept across
placements, languages and resizes, and concepts group into campaigns (Amendment 1's blocks). The shift
test already reassigns whole campaigns, so heavy localisation adds concepts inside a campaign, never
campaigns. Means are still taken over concepts, though, so a campaign cut many ways weighs more:
the readings in section 8 average campaigns first and months second, and H1 with every campaign
weighted equally is a declared sensitivity. A crop that defeats the hash stays inside its campaign.

## 2d. Video

A video ad enters only as its preview still. The tests do not cover moving image, so the film work
in the credits (Rahim Fortune, Benoît Delhomme) is mostly invisible to them, and a transfer carried
by film would be missed. Stills taken from video are flagged, and every test is repeated without them.

## 2e. The reader

The frozen design reads each image with the Claude model named in `ADTONE_CLAUDE_MODEL`. This amendment
replaces it, before any ad has been collected, with an open vision model run on Modal:
Qwen2.5-VL-7B-Instruct (Apache-2.0 weights), pinned to the commit the reader workflow records,
answering the same frozen rubric at temperature 0 under the rubric's JSON schema, so no reply outside
the fixed options can be generated.

- **Why.** Cost: at the estimated throughput, roughly a fiftieth of the API price per image, which the
  pilot measures. Durability: pinned open weights can be re-run identically for as long as they exist,
  while API models are retired on a schedule, which would strand a frozen instrument.
- **What does not change.** The rubric and its hash, the validation code, the image the reader sees
  (the image alone, never the house), and the blind human check: a question enters claims only at a
  kappa of 0.6 or above. The rule that an image without people has no gaze is enforced by validation,
  not by the schema, and a reply that breaks it is recorded as invalid.
- **Name.** The instrument is `tone-v1@qwen2.5-vl-7b-instruct@` followed by the first 12 characters of
  the weights' commit. The commit is recorded once and changes only by a dated re-pin before the freeze.
- **Fitness.** Before the reader runs on ads, a pilot on drawn test cards must return at least 95 per
  cent of replies that pass the rubric.
- **Second reader.** Named at the freeze: Claude on the stratified 10 per cent sample of section 4 if
  the Anthropic key is set, otherwise a second open model from a different family.

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

- Owners are clustered. Kering holds Gucci, Balenciaga and Bottega Veneta among the treated and Saint
  Laurent among the controls; LVMH holds Dior, Celine, Loewe and Fendi, and Louis Vuitton and Loro
  Piana; Prada Group holds Prada and Miu Miu, and Versace; OTB holds Jil Sander and Maison Margiela.
  A group-wide media policy could move a treated house and its control together, so every shift is
  also measured against controls of other owners only, and the pooled shift is reported both ways.

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

## 7b. Two falsifications

- **In-time placebo.** Each treated house is split at a fake debut, the median date of its pre-debut
  concepts, using pre-debut concepts only and no gap. At most 20 per cent of these may reach p below
  0.05. The archive's one year keeps pre-debut history short, so a fixed offset with the 90-day gap
  would leave most houses untestable; the median rule was fixed for that reason, before data.
- **Finding September.** Controls are scanned blind like the treated houses. The share whose best
  split falls between 23 September 2025 and 120 days after 6 October 2025 with p below 0.05 must be at
  most 20 per cent and at most half the treated houses' hit rate. Otherwise the detector is finding
  the season, not the debuts.

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
  the next, less that month's median change across all houses. Page views, search and press spike when
  a designer is named and again at the show, whether or not the ads changed, so the model carries a
  marker for the 30 days either side of each appointment announcement and each first show.
  Appointment dates join the registry, checked against announcements, before the freeze. Secondary curiosity: news volume and
  mean tone from GDELT; search interest only if the Trends API is in use at the freeze. Desire: Lyst
  rank changes under the new method, quarterly from Q1 2026. Money: quarterly revenue growth at the
  seven houses that report it by house: Gucci, Saint Laurent and Bottega Veneta (Kering), Prada and
  Miu Miu (Prada Group), Burberry and Hermès. Chanel reports once a year and LVMH does not break out
  its maisons. Sales lag the advertising by about a season, so money's primary lag is two quarters:
  readings in one quarter against revenue growth two quarters later, with one and three secondary.
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

## 11. The kill rule

The 2025 debuts calibrate the instrument; they are not the result. The calibration fails if any of
these holds: the control false-positive rate (share of controls scanned with p below 0.05) is above
20 per cent; the September check fails; more than 20 per cent of in-time placebos reach p below 0.05;
or the pooled brand-image shift is not significant while at least half the debuts changed their ad
mix beyond the 95th percentile of control placebos (a change of mix, not of look). If it fails, the
Saint Laurent prediction is not scored, nothing in this amendment is reported as a finding, and any
revised instrument is a new version, registered before it is pointed at another event.

## 12. Forward tests

`forward/saint-laurent-v1-addendum-1.md` settles the branches the frozen Saint Laurent file leaves
open, and gives Chloé a forward test of its own. It is frozen with this amendment.

## 13. Runway to attention

Does a show's attention last, and does the campaign that follows carry it? Shows recur, two to four a
year per house, so this rests on many events rather than ten debuts.

- **Events.** Show dates from `reference/shows.csv`, verified rows only, each with its source. Older
  seasons are added from the published fashion-week calendars before any result is read.
- **Spike.** The peak of log daily Wikipedia page views from the day before a show to three days after,
  over the mean from 60 to 10 days before. **Surprise** is the spike less the mean spike of the house's
  earlier events, once it has two.
- **Lasting attention.** The mean from 30 to 120 days after, over the same baseline, less the median
  house's change over the same days. A window needs 80 per cent of its days.
- **Sticky test.** The slope of lasting attention on surprise, within houses. The same slope is
  computed at placebo dates, at least 30 days from any show of the house, the same number per house,
  1,000 times. Supported if the show slope exceeds 95 per cent of placebo slopes: a show's attention
  lasts better than an equal jump on an ordinary day. It runs on page views back to July 2015 and
  needs no advertising.
- **The bridge.** For shows inside Meta's archive: lasting attention regressed on surprise, the log of
  EU and UK reach in the 90 days after the show, and alignment, the cosine between the centroid of the
  house's ads first seen from 7 days before to 21 days after the show and the centroid of those first
  seen 45 to 150 days after. Each needs three concepts. Standardised, with 90 per cent bootstrap
  intervals. Reported as more than description only from 30 shows. Predictive, not causal: houses push
  more after a show that went well.
- **Limits.** A front-row celebrity can make a spike, not the clothes. The show-period ads are the
  looks the house chose to promote, not the runway. Saint Laurent stays out of the bridge until its
  forward prediction is judged.

## 13b. What the clothes add

Does how a collection is received predict the attention that lasts, beyond the show's spectacle and
the house's momentum? This is the question of how much the clothes matter, asked of attention.

- **Reception.** GDELT's average tone of news matching the house's query (`adtone/press.py`; the house
  name, with fashion context for the ambiguous ones), on the show day and the three days after, less
  its mean from 60 to 10 days before. It needs two covered days after and ten in the baseline.
- **Spectacle** is the attention spike of section 13. **Momentum** is the mean log page views from 60
  to 10 days before, less the mean from 150 to 90 days before.
- **Test.** Within houses (each house's own averages removed, so a house whose press is always warm
  cannot pass for good clothes), the increase in R-squared from adding reception to spectacle and
  momentum, set against 1,000 shuffles of reception among each house's own shows. Supported if p is
  at most 0.05 and the coefficient is positive; reported with a 90 per cent bootstrap interval. Read
  only from 60 shows with every term, in houses with at least three.
- **Reading.** Supported: a well-received collection is followed by more lasting attention than its
  spectacle and the house's momentum predict. Not supported: nothing beyond spectacle and momentum
  is detectable at this sample. Desire (the next Lyst rank change) and money (revenue growth two
  quarters on) get the same decomposition, reported as description: too few observations to test.
- **Limits.** News tone is not criticism; celebrity coverage enters it; reception may follow momentum,
  which is why momentum is in the model. Predictive, not causal.
- **The thread.** Every show is one row of `data/results/runway_events.csv`, joining attention,
  press, momentum and, inside the archive, reach and alignment, on house and date.

## Before freezing

Extension dates verified and page ids confirmed; appointment dates added and verified; older show
dates compiled from the fashion-week calendars; the reader's weights pinned and its pilot passed; the second
reader named; the Trends API status recorded; the outcome evaluator, the second reader, the weights
checksum and the video flag in place and tested on synthetic worlds with known answers. Then this file and the registry are hashed, committed and timestamped together.
