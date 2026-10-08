# Clothes rubric v1

**Draft of 8 October 2026. Not frozen: no picture has been read with it.** It is frozen, and its hash
committed as `rubric/clothes-v1.sha256`, after Ben and the trained labeller have read the questions and
before any picture is read with it. Until then it can change; once frozen, any change is v2. The
loader refuses a rubric without its hash, so nothing can be read with this draft.

Exploratory, not in the pre-registration. `rubric/tone-v1.md` stays the registered instrument and
`rubric/tone-v2.md` the exploratory reading of the picture; nothing here changes either.

## What is being judged

The clothes and accessories in one picture: what is worn or shown, its shape, colour, surface, material
and register. Not the picture (light, setting, framing, pose and mood are tone-v2's), not the brand,
the designer or the season, and not whether the clothes are any good.

This is the layer the show and the advertising share. A runway photograph, a lookbook page, a
homepage picture and an advertisement are different kinds of picture made for different purposes,
but each shows clothes, and these questions mean the same thing on all of them. Each channel is
placed against its own kind before anything is compared across channels; this rubric only describes.

## Who can judge each question

The categories, silhouettes, lengths, patterns, finishing techniques and materials are the terms of
Fashionpedia's ontology (Jia and others, 2020), built by fashion experts, grouped where a picture
cannot separate them. Fashionpedia had its fine-grained attributes labelled by 15 graduate students in
apparel, not by a crowd, because those attributes need a trained eye. The questions are marked
accordingly:

- **anyone**: written definitions are enough. Ben's labels are the reference.
- **trained**: silhouette, construction, finishing, materials, formality and the street to couture
  axis. A labeller trained in fashion (a stylist, buyer, lecturer or dress curator) provides the
  reference labels. Ben's labels on these are kept and reported, to show how far an untrained eye
  differs, but they do not decide.

## Instructions given to the scorer

The text between the markers is sent verbatim as the system prompt.

<!-- PROMPT START -->
You are shown one picture from a fashion house: a runway photograph, a lookbook page, a picture from
its own website or one of its advertisements. Judge only the clothes and accessories that are visible.
Do not use knowledge of the brand, the designer, the season or the collection.
Do not identify any person, and do not guess anyone's identity, age, ethnicity, gender or other
personal characteristics. Where several people are shown, answer about the one whose outfit is most
fully visible; where there is no person, answer about the product shown. If a field cannot be judged from the picture, use the
value for not visible or not applicable: that is a correct answer and is counted as such.

Return one JSON object and nothing else, with exactly these keys.

subject: what the picture shows of clothing.
  worn_full = a person wearing an outfit, most of it visible (at least from the shoulders to below the knee)
  worn_part = a person wearing clothes, only part of the outfit visible
  product_alone = garments or accessories with no person wearing them
  no_clothing = no garment or accessory can be seen clearly
garments: every kind of garment that is visible, one to five of:
  shirt_blouse = a shirt or blouse, usually with a collar or a buttoned front
  top = a T-shirt, sweatshirt, tank, bodysuit or other top without a collar or buttoned front
  sweater = a knitted pullover
  cardigan = a knitted garment that opens down the front
  jacket = a sleeved outer layer ending at or above the hip, including blazers and suit jackets
  coat = a sleeved outer layer reaching below the hip, including trench coats and parkas
  vest = a sleeveless upper layer worn over other clothes: a waistcoat or gilet
  trousers = full or cropped trousers and jeans
  shorts = trousers ending above the knee
  skirt
  dress
  jumpsuit = a one-piece garment with legs
  cape = a sleeveless outer garment hanging from the shoulders
  none = no garment is visible (only accessories, or nothing)
accessories: every kind of accessory that is visible, one to five of:
  bag, shoes, eyewear, hat, head_accessory (headband, hair ornament, head covering other than a hat),
  scarf, gloves, belt, jewellery, watch, tie, hosiery (tights, stockings or visible socks),
  none = no accessory is visible
colour_main: the colour covering the largest area of the clothes and accessories (ignore skin, hair and the background).
  black, white_ivory, grey, beige_camel, brown, navy, blue, green, yellow_gold, orange, red, pink, purple,
  metallic (silver, gold or other metallic finish as the main colour),
  multicolour = no single colour covers half of the clothes,
  not_applicable = no clothing visible
colour_second: the next largest colour of the clothes, from the same list, or none when there is only one colour.
pattern: the pattern of the clothes.
  plain = no pattern, including one colour with texture
  check = check, plaid, tartan, houndstooth or gingham
  stripe = stripes, including pinstripe
  dot = dots or polka dots
  floral = flowers, leaves or plants
  animal = leopard, zebra, snakeskin or another animal skin pattern
  logo_letters = logos, monograms, letters or numbers as the pattern
  graphic = geometric, abstract, cartoon, camouflage, paisley or another printed or woven motif
  mixed = two or more of these on different garments
  not_applicable = no clothing visible
skin_shown: how much of the person's body is uncovered.
  covered = clothing covers almost all of the body
  some = arms, legs, shoulders or back bare
  much = much of the torso or body bare
  not_applicable = no person
hemline: where the lowest edge of the outfit falls on the body (the dress, skirt, coat or trousers, whichever reaches lowest).
  above_knee, knee, midi (below the knee to mid-calf), ankle_floor (at the ankle or below),
  not_visible = the frame cuts above it,
  not_applicable = no person
layers: how many garments are worn one over another on the upper body and can be seen.
  one, two, three_or_more, not_applicable (no person, or the upper body is not visible)
silhouette: the overall shape the outfit makes around the body.
  fitted = follows the body closely (slim, skinny, curved, pencil or mermaid shapes)
  straight = falls straight from the shoulders or hips without clinging or flaring (column, shift, regular fit)
  flared = narrow at the waist, hip or knee and widening below it (A-line, fit and flare, trumpet, circle, bell)
  volume = held away from the body, larger than it (oversized, balloon, cocoon, tent, trapeze, baggy, wide leg)
  mixed = clearly different shapes above and below, such as a volume top over narrow trousers
  not_visible = too little of the outfit can be seen
  not_applicable = no person
construction: how the main garment is built, as far as it can be seen.
  tailored = cut and built to hold its own shape: structured shoulders, lapels, darts, pressed lines, corsetry
  draped = cloth folded, gathered or hung on the body so that its fall shapes the garment: bias cuts, draped jersey, gowns built on the form
  knitted = a knitted garment shaped as it was knitted
  soft_cut = simply cut soft garments that neither hold a shape of their own nor are draped: T-shirts, plain shirts, simple shifts
  mixed = tailored and draped or knitted parts of similar weight in one outfit
  not_visible, not_applicable (no clothing)
finishing: the techniques worked into the clothes that can be seen, one to three of:
  none = no special technique
  embroidered = embroidery, appliqué or patches
  beaded = beads, sequins, crystals or paillettes
  studded = rivets, studs, spikes or eyelets as decoration
  pleated = pleats, ruching, gathering, smocking or shirring
  quilted = quilting or padding stitched through
  tiered = tiers, ruffles or layered flounces
  cut = cutouts, slits or perforation
  distressed = ripped, frayed, washed, bleached or burnt-out
  feathers_fringe = feathers, fringe or tassels
materials: the materials that can be told by eye, one to three of:
  leather = leather, suede, patent or exotic skins
  fur_shearling = fur, faux fur or shearling
  denim
  knit = knitted fabric
  sheer_lace = chiffon, organza, tulle, mesh or lace
  satin_silk = lustrous fluid cloth: satin, silk, charmeuse
  tweed_boucle = rough-textured woven wool, tweed or bouclé
  metallic = lamé or another metallic cloth or finish
  technical = nylon, rubberised or performance fabrics
  plain_woven = suiting wool, cotton poplin or another plain woven cloth, when nothing more specific shows
  not_distinguishable = the materials cannot be told from the picture
formality: the register the clothes are styled in.
  formal_tailored, eveningwear, casual, sport_street, utilitarian, mixed, not_applicable
street_couture_axis: an integer from 1 to 5, judging the clothes only (not the setting or the photograph).
  1 = street register: sportswear, logo garments, everyday basics
  3 = neither, or a deliberate mix
  5 = couture register: constructed, formal or hand-finished garments
confidence: a number from 0 to 1 for how sure you are about the clothes as a whole
<!-- PROMPT END -->

## Machine-readable specification

The scorer validates every response against this block, and a test checks that every value here
also appears in the prompt above.

```json
{
  "version": "clothes-v1",
  "enums": {
    "subject": ["worn_full", "worn_part", "product_alone", "no_clothing"],
    "colour_main": ["black", "white_ivory", "grey", "beige_camel", "brown", "navy", "blue", "green", "yellow_gold", "orange", "red", "pink", "purple", "metallic", "multicolour", "not_applicable"],
    "colour_second": ["black", "white_ivory", "grey", "beige_camel", "brown", "navy", "blue", "green", "yellow_gold", "orange", "red", "pink", "purple", "metallic", "none"],
    "pattern": ["plain", "check", "stripe", "dot", "floral", "animal", "logo_letters", "graphic", "mixed", "not_applicable"],
    "skin_shown": ["covered", "some", "much", "not_applicable"],
    "hemline": ["above_knee", "knee", "midi", "ankle_floor", "not_visible", "not_applicable"],
    "layers": ["one", "two", "three_or_more", "not_applicable"],
    "silhouette": ["fitted", "straight", "flared", "volume", "mixed", "not_visible", "not_applicable"],
    "construction": ["tailored", "draped", "knitted", "soft_cut", "mixed", "not_visible", "not_applicable"],
    "formality": ["formal_tailored", "eveningwear", "casual", "sport_street", "utilitarian", "mixed", "not_applicable"]
  },
  "lists": {
    "garments": {"options": ["shirt_blouse", "top", "sweater", "cardigan", "jacket", "coat", "vest", "trousers", "shorts", "skirt", "dress", "jumpsuit", "cape", "none"], "min": 1, "max": 5},
    "accessories": {"options": ["bag", "shoes", "eyewear", "hat", "head_accessory", "scarf", "gloves", "belt", "jewellery", "watch", "tie", "hosiery", "none"], "min": 1, "max": 5},
    "finishing": {"options": ["none", "embroidered", "beaded", "studded", "pleated", "quilted", "tiered", "cut", "distressed", "feathers_fringe"], "min": 1, "max": 3},
    "materials": {"options": ["leather", "fur_shearling", "denim", "knit", "sheer_lace", "satin_silk", "tweed_boucle", "metallic", "technical", "plain_woven", "not_distinguishable"], "min": 1, "max": 3}
  },
  "integers": {
    "street_couture_axis": {"min": 1, "max": 5}
  },
  "numbers": {
    "confidence": {"min": 0, "max": 1}
  },
  "eye": {
    "anyone": ["subject", "garments", "accessories", "colour_main", "colour_second", "pattern", "skin_shown", "hemline", "layers"],
    "trained": ["silhouette", "construction", "finishing", "materials", "formality", "street_couture_axis"]
  }
}
```

## What changes from tone-v2

`skin_shown` is tone-v2's question with its words. `formality` is tone-v2's `styling_register` under a
clearer name, with the same values. `street_couture_axis` keeps tone-v2's scale but judges the clothes
only: tone-v2's version also weighed the setting and the production, which belong to the picture and
would make a runway photograph and an advertisement differ for reasons that are not the clothes.

## The test set

About 300 pictures, two kinds in equal numbers:

- **Runway looks**: about 150, from the spring-summer 2026 collection pages the Wayback Machine keeps
  on the houses' own sites (`data/thread/looks_probe.json`: Balenciaga, Bottega Veneta, Celine, Chloé,
  Dries Van Noten, Hermès, Jil Sander, Maison Margiela, Miu Miu, Saint Laurent, Valentino), the same
  number from each house as far as each allows, taking the pictures in portrait format, which on these
  pages are the looks. A picture that turns out to show something else is labelled as what it is. As
  drawn on 8 October 2026 (`data/clothes/sample.json`): 127 looks, 14 from each of nine houses and one
  from Hermès; Balenciaga's archived pages gave none in portrait format at a usable size.
- **Advertising pictures**: about 150 of the 296 homepage pictures in the earlier bake-off, two in three
  showing a person and one in three a product alone by their tone-v2 reading, drawn at random within
  house, so that the questions about a worn outfit have enough pictures on this side too.

For 60 pictures, 30 of each kind, a copy cut to its central 85% is read as well: each picture and its
crop make a crop pair, to measure how stable each answer is. No picture is written to the repository: copies for the reader stay on the private Modal
volume, and the thumbnails for the labelling page leave the runner only sealed.

**Labels.** 100 pictures, 50 of each kind drawn at random (the advertising 50 in the same two to one
proportion), are labelled on a private page by Ben (every
question) and by at least one trained labeller (the trained questions; every question if they are
willing). With two trained labellers, both label the same 100.

## Which questions go forward

Fixed with this file, before any picture is read with it, and implemented in `adtone/clothes.py`
(`judge`), tested on synthetic answers, before any reading. The reader is the one the earlier bake-off
chose (Qwen3-VL-32B at its pinned weights). Questions about a worn outfit (skin_shown, hemline, layers,
silhouette) apply to pictures that show one (subject worn_full or worn_part); the others to every
picture that shows clothing (subject other than no_clothing). Rules 1 and 2 are judged over the whole
test set, using the reader's own subject answer to decide where a question applies; rules 3 and 4 over
the labelled pictures, using Ben's subject label. A list question is judged option by option, and goes
forward with the options that pass, if at least two do. For street_couture_axis every kappa is
quadratic weighted.

A question goes forward when all of these hold:

1. **It varies.** Its commonest answer covers less than 90% of the pictures it applies to; an option
   of a list is chosen on at least 5% and at most 95% of them.
2. **It is stable.** Two crops of one picture get the same answer beyond chance: kappa at least 0.4,
   over at least ten crop pairs.
3. **It agrees with the reference on both kinds of picture.** Kappa at least 0.4 between the reader and
   the reference labels on the runway looks, and at least 0.4 on the advertising pictures, each over
   at least 20 labelled pictures the question applies to. A question that works on one kind only is
   not part of the shared layer. The reference is Ben's labels for the questions marked anyone, the
   trained labeller's for those marked trained; with two trained labellers the reader must reach it
   with each. Without a trained labeller, no trained question goes forward.
4. **The trained labellers agree with each other**, where there are two: kappa at least 0.4 on each
   kind of picture. A question two trained eyes split on does not go forward whatever the reader does.

Kappa at least 0.4 is the lower edge of what Landis and Koch call moderate agreement. No question
goes forward on whether it shows the result anyone hoped for.

## Sources

- Jia, M., Shi, M., Sirotenko, M., Cui, Y., Cardie, C., Hariharan, B., Adam, H. and Belongie, S.
  (2020). Fashionpedia: Ontology, segmentation, and an attribute localization dataset. *European
  Conference on Computer Vision (ECCV)*. arXiv:2004.12276.
- Landis, J. R. and Koch, G. G. (1977). The measurement of observer agreement for categorical data.
  *Biometrics*, 33(1), 159 to 174.
