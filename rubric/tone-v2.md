# Tone rubric v2

Frozen 2026-10-08, before any image was scored with it. Exploratory: `rubric/tone-v1.md` stays the
registered instrument for the pre-registered advertising analysis, and nothing here changes it. v2
reads the brands' homepage pictures for the luxury reading. This file is the method: any change is
v3, with a new `rubric_version` in every output row, and scores from different versions are never
pooled. `rubric/tone-v2.sha256` holds this file's hash; the test suite fails if the file changes.

## What is being judged

As in v1: the tone of one picture, meaning how it is lit, coloured, staged, cast and produced, and
what it puts in front of the viewer. v2 adds how the picture is composed and how it addresses the
viewer, in categories taken from the research on advertising and photographic images, so that each
can be checked against that research and against the pixels. Not the brand, not the designer, not
the season, and not whether the picture is any good.

## What changed from v1, and why

- **creative_type.** The line between a product on a model, a packshot and a campaign picture is
  drawn by whether a person is visible and whether the scene is staged. In the bake-off of 7 and 8
  October 2026, the v1 reader called 465 product shots "product on a model" (16 of 16 checked by eye
  showed no person), and the larger reader called some styled still lifes packshots.
- **framing** is defined by where the frame cuts the body: Kress and van Leeuwen's size of frame,
  which they read as social distance (close: intimate or personal; medium: social; long: public).
- **New, from Kress and van Leeuwen, *Reading Images* (1996; 3rd edition 2021):** vertical_angle
  (looking down on, level with or up at the subject: power), horizontal_angle (a person turned
  towards or away from the viewer: involvement or detachment), placement (where the subject sits in
  the frame: their information value of centre and margin, left and right), and modality (how the
  picture renders its world: their naturalistic, sensory and abstract coding orientations, here
  naturalistic, heightened, reduced and artificial). gaze keeps v1's values, which are their
  "demand" (to_camera) and "offer" (away).
- **New, from Pracejus, Olsen and O'Guinn, "How Nothing Became Something: White Space, Rhetoric,
  History, and Meaning" (Journal of Consumer Research, 2006):** open_space, the empty space they
  trace as a signal of prestige, quality and price.
- **New, from Pieters, Wedel and Batra, "The Stopping Power of Advertising: Measures and Effects of
  Visual Complexity" (Journal of Marketing, 2010):** objects and arrangement, two of the principles
  of their design complexity (the quantity of objects, and the asymmetry and irregularity of their
  arrangement). Their feature complexity is measured from the pixels in `rubric/composition-v1.md`.
- **New, from Goffman, *Gender Advertisements* (1979), as coded in content analyses since:**
  head_cant, self_touch, body_level and withdrawal, his canting of the head and body, the light
  "feminine touch", the lowered and reclining body of the ritualization of subordination, and
  licensed withdrawal. They are coded as visible behaviour only: nothing about who the person is.
- **New, from the coding of dress in studies of sexual appeals in advertising (Reichert and
  colleagues, 1999 onwards):** skin_shown, how much of the body is uncovered, in three steps.
- **New, from Phillips and McQuarrie, "Beyond Visual Metaphor: A New Typology of Visual Rhetoric in
  Advertising" (Marketing Theory, 2004):** rhetoric, their visual structures (juxtaposition, fusion,
  replacement) against a straightforward picture.
- **Dropped:** production. Under the reader chosen in the bake-off one answer covered 92% of
  pictures, and modality covers the same ground with categories from the literature.

Every other question, its values and its wording are as in v1.

## Instructions given to the scorer

The text between the markers is sent verbatim as the system prompt.

<!-- PROMPT START -->
You are shown one picture from a fashion brand's own homepage or advertising. Judge only what is
visible in this picture. Do not use knowledge of the brand, the designer, the season or the
campaign. Do not identify any person, and do not guess anyone's identity, age, ethnicity, gender or
other personal characteristics: describe only what the picture shows them doing. Where there is more
than one person, answer about the most prominent one. If a field cannot be judged from the picture,
use the value for unclear or not applicable: that is a correct answer and is counted as such.

Return one JSON object and nothing else, with exactly these keys.

creative_type: what kind of picture this is.
  brand_image = a staged campaign or editorial photograph, or a film still, made to convey the brand. A styled still life, with products arranged in a composed scene or set and no person, is brand_image
  product_on_model = a product worn or carried by a person, shown plainly in an e-commerce or lookbook style, usually on a plain background. Use it only when a person is visible
  product_packshot = a product alone with no person, on a plain or neutral background, with no staged scene around it
  catalogue_grid = several products laid out as a grid, carousel tile or catalogue page
  promotional = a picture built around an offer, price, sale, gift guide or call to action
  event_or_announcement = a show, store opening, event, livestream or announcement graphic
  text_graphic = mainly typography or graphic design, with little or no photography
  other = none of the above
category: the main product category shown.
  ready_to_wear, leather_goods, footwear, jewellery_watches, eyewear, fragrance_beauty, home, mixed, unclear
light: high_key (bright, few shadows), low_key (dark, strong shadows), natural_daylight, mixed, unclear
colour_temperature: warm, cool, neutral, mixed
saturation: muted, moderate, vivid, monochrome
setting: studio_plain, studio_set, interior, urban_exterior, landscape_nature, water_beach, abstract_or_digital, unclear
people: none, one, two, group
gaze: to_camera (the person looks at the viewer), away (looks at something in the picture or out of frame), eyes_hidden, no_face, not_applicable (not_applicable when people is none)
expression: neutral, smiling, intense, playful, not_applicable
pose: posed_static, candid_movement, performative, not_applicable
framing: where the frame cuts the person, or how much of the scene is shown.
  close_up = head and shoulders or closer
  medium = from the waist or the knees up
  full_length = the whole figure, filling most of the height of the frame
  wide_scene = the figure small within a larger scene
  product_only = no person; a product or object is the subject
vertical_angle: high (the camera looks down on the subject), eye_level (level with it), low (the camera looks up at it), unclear
horizontal_angle: frontal (the person's body and face are turned squarely towards the viewer), oblique (turned at an angle, side-on or away from the viewer), not_applicable (no person)
placement: where the main subject sits in the frame.
  centre = in the middle
  left = clearly to the left, with open space to the right
  right = clearly to the right, with open space to the left
  fills_frame = the subject fills almost all of the frame
  spread = several elements of similar weight across the frame, no single subject
modality: how the picture renders its world.
  naturalistic = like an ordinary photograph of a real scene: natural colour, light and detail
  heightened = colour, light, contrast or texture pushed beyond the natural for effect
  reduced = simplified to a few shapes, flat colour or a plain ground, with detail stripped away
  artificial = visibly computer-generated, composited, collaged or painterly
open_space: how much of the frame is empty or plain space around the subject.
  little = the frame is filled, with almost no empty space
  some = some open space around the subject
  much = large areas of empty or plain space, the subject small against them
objects: how many distinct things the picture is built from, counting each person, product, prop and set element once: one, two_to_three, four_to_six, many (seven or more)
arrangement: how those things are arranged.
  single = one thing only, nothing to arrange
  symmetrical = a balanced, mirror-like arrangement about the centre
  balanced = not mirror-like, but evenly weighted across the frame
  irregular = uneven, scattered or deliberately off balance
head_cant: the person's head is tilted noticeably to one side. yes, no, not_applicable (no person, or the head is not visible)
self_touch: the person lightly touches their own face, hair, neck or body. yes, no, not_applicable (no person)
body_level: the person's posture.
  upright = standing, or sitting upright
  lowered = bending, crouching, kneeling or sitting low
  reclining = lying down, or leaning back on a surface
  not_applicable = no person, or the body is not visible
withdrawal: the person seems mentally elsewhere: gaze unfocused or cast down, eyes closed, or the face partly hidden behind a hand, hair or an object. yes, no, not_applicable (no person, or the face is not visible)
skin_shown: how much of the person's body is uncovered.
  covered = clothing covers almost all of the body
  some = arms, legs, shoulders or back bare
  much = much of the torso or body bare
  not_applicable = no person
primary_subject: the single thing the picture is composed around.
  garment, bag, footwear, accessory, jewellery, face, body, scene, product_object, text
styling_register: formal_tailored, eveningwear, casual, sport_street, utilitarian, mixed, not_applicable
rhetoric: whether the picture is built as a visual figure.
  none = a straightforward picture of its subject
  juxtaposition = two or more things set side by side to be compared or linked
  fusion = two things combined into one, such as an object shaped like another
  replacement = something appears where something else would be expected
text_in_image: none, logo_only, headline, price_or_offer, call_to_action
mood: a list of one to three of: austere, serene, intimate, sensual, playful, ironic, nostalgic, opulent, surreal, aggressive, romantic, documentary
street_couture_axis: an integer from 1 to 5.
  1 = street register: sportswear or logo garments, street or everyday settings, snapshot or lo-fi production
  3 = neither, or a deliberate mix
  5 = couture register: constructed or formal garments, controlled studio or grand settings, high polish
confidence: a number from 0 to 1 for how sure you are about the picture as a whole
<!-- PROMPT END -->

## Machine-readable specification

The scorer validates every response against this block, and a test checks that every value here
also appears in the prompt above.

```json
{
  "version": "tone-v2",
  "enums": {
    "creative_type": ["brand_image", "product_on_model", "product_packshot", "catalogue_grid", "promotional", "event_or_announcement", "text_graphic", "other"],
    "category": ["ready_to_wear", "leather_goods", "footwear", "jewellery_watches", "eyewear", "fragrance_beauty", "home", "mixed", "unclear"],
    "light": ["high_key", "low_key", "natural_daylight", "mixed", "unclear"],
    "colour_temperature": ["warm", "cool", "neutral", "mixed"],
    "saturation": ["muted", "moderate", "vivid", "monochrome"],
    "setting": ["studio_plain", "studio_set", "interior", "urban_exterior", "landscape_nature", "water_beach", "abstract_or_digital", "unclear"],
    "people": ["none", "one", "two", "group"],
    "gaze": ["to_camera", "away", "eyes_hidden", "no_face", "not_applicable"],
    "expression": ["neutral", "smiling", "intense", "playful", "not_applicable"],
    "pose": ["posed_static", "candid_movement", "performative", "not_applicable"],
    "framing": ["close_up", "medium", "full_length", "wide_scene", "product_only"],
    "vertical_angle": ["high", "eye_level", "low", "unclear"],
    "horizontal_angle": ["frontal", "oblique", "not_applicable"],
    "placement": ["centre", "left", "right", "fills_frame", "spread"],
    "modality": ["naturalistic", "heightened", "reduced", "artificial"],
    "open_space": ["little", "some", "much"],
    "objects": ["one", "two_to_three", "four_to_six", "many"],
    "arrangement": ["single", "symmetrical", "balanced", "irregular"],
    "head_cant": ["yes", "no", "not_applicable"],
    "self_touch": ["yes", "no", "not_applicable"],
    "body_level": ["upright", "lowered", "reclining", "not_applicable"],
    "withdrawal": ["yes", "no", "not_applicable"],
    "skin_shown": ["covered", "some", "much", "not_applicable"],
    "primary_subject": ["garment", "bag", "footwear", "accessory", "jewellery", "face", "body", "scene", "product_object", "text"],
    "styling_register": ["formal_tailored", "eveningwear", "casual", "sport_street", "utilitarian", "mixed", "not_applicable"],
    "rhetoric": ["none", "juxtaposition", "fusion", "replacement"],
    "text_in_image": ["none", "logo_only", "headline", "price_or_offer", "call_to_action"]
  },
  "lists": {
    "mood": {"options": ["austere", "serene", "intimate", "sensual", "playful", "ironic", "nostalgic", "opulent", "surreal", "aggressive", "romantic", "documentary"], "min": 1, "max": 3}
  },
  "integers": {
    "street_couture_axis": {"min": 1, "max": 5}
  },
  "numbers": {
    "confidence": {"min": 0, "max": 1}
  }
}
```

## Which questions go forward

Fixed with this file, before any picture was read with it, and the same rule v1's questions met: on
the bake-off's 296 pictures, read by the reader the bake-off chose (Qwen3-VL-32B at its pinned
weights), a question goes forward when its commonest answer covers less than 90% of the pictures and
two crops of one picture get the same answer beyond chance (kappa at least 0.4). The questions about
a person (gaze, expression, pose, horizontal_angle, head_cant, self_touch, body_level, withdrawal,
skin_shown) are judged on the pictures that show a person, since not_applicable is the right answer
for the rest. No question goes forward on whether it shows the result anyone hoped for.

## Sources

- Goffman, E. (1979). *Gender Advertisements*. Harper and Row.
- Kress, G. and van Leeuwen, T. (1996; 3rd edition 2021). *Reading Images: The Grammar of Visual
  Design*. Routledge.
- Phillips, B. J. and McQuarrie, E. F. (2004). Beyond visual metaphor: A new typology of visual
  rhetoric in advertising. *Marketing Theory*, 4(1/2), 113 to 136.
- Pieters, R., Wedel, M. and Batra, R. (2010). The stopping power of advertising: Measures and
  effects of visual complexity. *Journal of Marketing*, 74(5), 48 to 60.
- Pracejus, J. W., Olsen, G. D. and O'Guinn, T. C. (2006). How nothing became something: White space,
  rhetoric, history, and meaning. *Journal of Consumer Research*, 33(1), 82 to 90.
- Reichert, T., Lambiase, J., Morgan, S., Carstarphen, M. and Zavoina, S. (1999). Cheesecake and
  beefcake: No matter how you slice it, sexual explicitness in advertising continues to increase.
  *Journalism and Mass Communication Quarterly*, 76(1), 7 to 20.
