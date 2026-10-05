# Pre-registered analysis: did the 2025 creative director changes move advertising tone?

STATUS: FROZEN on 5 October 2026, before any ad was collected. Analyses follow this specification,
and any deviation is reported as a deviation. The analyse workflow refuses to run until both this file
and `registry/houses.yml` are frozen.

## Status of results

Every result on the 2025 debuts validates the instrument against history. Under the standing rule,
retrospective analysis never counts as a call. Forward calls go in CLAIMS.md, and only after a
silent baseline season of live collection.

## Data

- Ads delivered in the EU27 and the UK from the pages in `registry/houses.yml` (frozen), collected
  from the Meta Ad Library.
- Images scored with `rubric/tone-v1.md` (frozen by hash) by the model named in
  `ADTONE_CLAUDE_MODEL`, and embedded with OpenCLIP ViT-B-32 (laion2b_s34b_b79k). Analysis reads one
  instrument by exact name. Rows from different instruments are never pooled.

## Units

- **Concept.** Within a house, ads joined by a shared image or by images within 6 bits of 64-bit
  perceptual hash. Its date is the earliest delivery start among its ads. Its vector is the
  normalised mean of its images' vectors.
- **Campaign block.** Within a house, concepts in date order. A gap of more than 21 days starts a new
  block. Every null permutes whole blocks, never single concepts.

## Inclusion

- Only ads from pages confirmed in the frozen registry. Pages the first run selected automatically and
  nobody confirmed are collected but never analysed.
- **Primary.** Concepts whose modal creative_type is brand_image and whose modal category is not
  fragrance_beauty, jewellery_watches, eyewear or home.
- **Sensitivity.** brand_image and product_on_model together. Reported for H1 only.

## Measure

- **Residual.** A concept's vector minus the field for its calendar month. The field is the mean of
  the control houses' monthly mean vectors, leaving out the house being measured. If no other control
  has concepts that month, the other controls' whole-window means are used. Treated houses are kept
  out of the field because the 2025 wave moved too many houses at once for an all-house field to
  stay neutral.
- **Windows.** Pre is before the debut date. Post starts 90 days after it. Concepts in between are
  excluded.
- **Shift and z.** The distance between mean pre and post residuals, standardised against the
  block-permutation null: every split that keeps each side's block count when there are at most
  20,000 of them (an exact test), otherwise 1,999 random splits.
- **Sufficient data.** At least 8 concepts and 3 blocks on each side.

## H1: treated houses moved more than controls

- For each treated house with sufficient data, z at its debut date.
- For each control, z at every treated debut date (placebo splits).
- Supported if at least 60% of treated houses have z above the 90th percentile of the control
  placebo z. Requires at least 4 treated houses with sufficient data and at least 3 controls with a
  placebo; otherwise reported as not testable.

## H2: Gucci moved towards Balenciaga's pre-Piccioli tone

- **Transfer.** cos(Gucci post, Balenciaga pre) minus cos(Gucci pre, Balenciaga pre), on mean
  residuals with the windows above.
- **Null.** Gucci's blocks reassigned between its pre and post sides.
- **Specificity.** Gucci's movement (post mean minus pre mean) regressed, with ridge penalty 0.1,
  on the unit-normalised mean residual of every reference house at once: Balenciaga's pre window,
  other treated houses' pre windows and the controls' whole windows, each with at least 8 concepts,
  plus Gucci's own pre window. Gucci's own term absorbs the move away from its old tone and is not
  eligible to be the largest. The penalty is needed because control residuals are built against each
  other and nearly sum to zero, which leaves their coefficients unidentified without it. It was set
  on synthetic data before collection: at 0.1 a planted transfer was recovered in 10 of 10 worlds on
  the full panel and falsely supported in 0 of 10 where Gucci moved elsewhere.
- Supported if the transfer is positive, p is at most 0.05, and Balenciaga carries the largest
  coefficient among the other houses, and it is positive. Requires at least 8 Balenciaga pre concepts
  and sufficient data on both Gucci sides.

## H3: the detector finds known breaks and stays quiet on controls

- A changepoint scan per house over block boundaries, with at least 3 blocks and 8 concepts on each
  side. The statistic is the shift scaled by sqrt(nL nR / (nL + nR)), and the null permutes the order
  of blocks (1,999 permutations).
- **Hit.** A treated house with p < 0.05 whose best split falls 0 to 120 days after the debut.
- Supported if hits are at least half of the treated houses tested and at most 20% of the controls
  tested have p < 0.05. Requires at least 4 treated houses and 3 controls tested.

## Descriptive outputs, not tested

- For each treated house, the largest changes in modal rubric values from pre to post, and the change
  in mean street_couture_axis.
- A rubric field enters descriptive claims only if its agreement with blind human coding (120 images,
  Cohen's kappa) is at least 0.6.

## Limits recorded before data

- With three blocks a side the exact test's smallest p is 0.05. Every result reports its floor.
- Houses whose debut fell before most of the retained window (Celine, Margiela) are expected to lack
  pre-change advertising and will report as insufficient. They stay in the registry rather than being
  dropped after the fact.
- One channel: paid digital advertising delivered in the EU and UK.

## What would count against the instrument

- H3 failing with sufficient data. A detector that misses breaks everyone agrees happened gives
  quiet periods that mean nothing.
- Control placebo shifts as large as the treated houses' shifts. The field would not be removing
  what it is meant to remove.
