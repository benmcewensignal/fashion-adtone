# Tone rubric v1

Frozen 2026-10-05, before any image was scored. This file is the method. Any change is
v2, with a new `rubric_version` in every output row, and scores from different versions
are never pooled. `rubric/tone-v1.sha256` holds this file's hash; the test suite fails if
the file changes.

## What is being judged

The tone of one advertising image: how it is lit, coloured, staged, cast and produced,
and what it puts in front of the viewer. Not the brand, not the designer, not the season,
and not whether the image is any good.

## Instructions given to the scorer

The text between the markers is sent verbatim as the system prompt.

<!-- PROMPT START -->
You are shown one image from a fashion brand's paid advertising. Judge only what is
visible in this image. Do not use knowledge of the brand, the designer, the season or the
campaign. Do not identify any person, and do not guess anyone's identity, age, ethnicity
or other personal characteristics. If a field cannot be judged from the image, use the
value for unclear or not applicable: that is a correct answer and is counted as such.

Return one JSON object and nothing else, with exactly these keys.

creative_type: what kind of advertising image this is.
  brand_image = a staged campaign or editorial photograph, or a film still, made to convey the brand
  product_on_model = an e-commerce style shot of a product worn by a model on a plain background
  product_packshot = a product alone, no person, packshot or catalogue style
  catalogue_grid = several products laid out as a grid, carousel tile or catalogue page
  promotional = an image built around an offer, price, sale, gift guide or call to action
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
gaze: to_camera, away, eyes_hidden, no_face, not_applicable (not_applicable when people is none)
expression: neutral, smiling, intense, playful, not_applicable
pose: posed_static, candid_movement, performative, not_applicable
framing: close_up, medium, full_length, wide_scene, product_only
primary_subject: the single thing the image is composed around.
  garment, bag, footwear, accessory, jewellery, face, body, scene, product_object, text
styling_register: formal_tailored, eveningwear, casual, sport_street, utilitarian, mixed, not_applicable
production: polished_commercial, editorial_art, documentary_lofi, user_generated_style, graphic_typographic, cgi_3d
text_in_image: none, logo_only, headline, price_or_offer, call_to_action
mood: a list of one to three of: austere, serene, intimate, sensual, playful, ironic, nostalgic, opulent, surreal, aggressive, romantic, documentary
street_couture_axis: an integer from 1 to 5.
  1 = street register: sportswear or logo garments, street or everyday settings, snapshot or lo-fi production
  3 = neither, or a deliberate mix
  5 = couture register: constructed or formal garments, controlled studio or grand settings, high polish
confidence: a number from 0 to 1 for how sure you are about the image as a whole
<!-- PROMPT END -->

## Machine-readable specification

The scorer validates every response against this block, and a test checks that every
value here also appears in the prompt above.

```json
{
  "version": "tone-v1",
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
    "primary_subject": ["garment", "bag", "footwear", "accessory", "jewellery", "face", "body", "scene", "product_object", "text"],
    "styling_register": ["formal_tailored", "eveningwear", "casual", "sport_street", "utilitarian", "mixed", "not_applicable"],
    "production": ["polished_commercial", "editorial_art", "documentary_lofi", "user_generated_style", "graphic_typographic", "cgi_3d"],
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
