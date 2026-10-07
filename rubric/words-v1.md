# Words rubric v1

Written 2026-10-07 for the Alignment reading, which is exploratory and outside the
pre-registration. This file is the method for reading a house's words: any change is v2, with a
new version in every output row, and readings from different versions are never pooled.
`rubric/words-v1.sha256` holds this file's hash; the test suite fails if the file changes.

## What is being judged

What a house's own text about a collection says about how its images look and feel, in the same
terms as the image rubric (`rubric/tone-v1.md`): the same questions and the same answers, without
the answers that only make sense for a picture (unclear, not applicable), and with not_said for a
question the text does not speak to. Three image questions are left out because words cannot
answer them (creative type, product category and text in the image), and so is the image reader's
confidence.

## Instructions given to the reader

The text between the markers is sent verbatim as the system prompt.

<!-- PROMPT START -->
You are given a short text in which a fashion house or its designer describes a collection or a
campaign: show notes, a manifesto, or the designer's own words as quoted in a review. Judge only
what the text says or clearly implies about how the collection's images look and feel: the light,
the colour, the setting, the people, the clothes' register and the mood. Do not use knowledge of
the brand, the designer, the season or the campaign beyond the text, and do not guess at anything
the text does not speak to. Most texts speak to only a few of these questions: answer not_said for
the rest. That is the expected answer and it is counted as such.

Return one JSON object and nothing else, with exactly these keys. Each takes one of its listed
values; not_said means the text does not speak to the question.

light: high_key, low_key, natural_daylight, mixed, not_said
colour_temperature: warm, cool, neutral, mixed, not_said
saturation: muted, moderate, vivid, monochrome, not_said
setting: studio_plain, studio_set, interior, urban_exterior, landscape_nature, water_beach, abstract_or_digital, not_said
people: none, one, two, group, not_said
gaze: to_camera, away, eyes_hidden, no_face, not_said
expression: neutral, smiling, intense, playful, not_said
pose: posed_static, candid_movement, performative, not_said
framing: close_up, medium, full_length, wide_scene, product_only, not_said
primary_subject: garment, bag, footwear, accessory, jewellery, face, body, scene, product_object, text, not_said
styling_register: formal_tailored, eveningwear, casual, sport_street, utilitarian, mixed, not_said
production: polished_commercial, editorial_art, documentary_lofi, user_generated_style, graphic_typographic, cgi_3d, not_said
mood: a list of zero to three of: austere, serene, intimate, sensual, playful, ironic, nostalgic, opulent, surreal, aggressive, romantic, documentary
  (an empty list when the text implies no mood)
street_couture_axis: an integer from 0 to 5.
  0 = the text does not place the collection on this scale
  1 = street register: sportswear or logo garments, street or everyday settings, snapshot or lo-fi production
  3 = neither, or a deliberate mix
  5 = couture register: constructed or formal garments, controlled studio or grand settings, high polish
<!-- PROMPT END -->

## Machine-readable specification

The reader validates every response against this block, and a test checks that every value here
also appears in the prompt above and in the image rubric.

```json
{
  "version": "words-v1",
  "enums": {
    "light": [
      "high_key",
      "low_key",
      "natural_daylight",
      "mixed",
      "not_said"
    ],
    "colour_temperature": [
      "warm",
      "cool",
      "neutral",
      "mixed",
      "not_said"
    ],
    "saturation": [
      "muted",
      "moderate",
      "vivid",
      "monochrome",
      "not_said"
    ],
    "setting": [
      "studio_plain",
      "studio_set",
      "interior",
      "urban_exterior",
      "landscape_nature",
      "water_beach",
      "abstract_or_digital",
      "not_said"
    ],
    "people": [
      "none",
      "one",
      "two",
      "group",
      "not_said"
    ],
    "gaze": [
      "to_camera",
      "away",
      "eyes_hidden",
      "no_face",
      "not_said"
    ],
    "expression": [
      "neutral",
      "smiling",
      "intense",
      "playful",
      "not_said"
    ],
    "pose": [
      "posed_static",
      "candid_movement",
      "performative",
      "not_said"
    ],
    "framing": [
      "close_up",
      "medium",
      "full_length",
      "wide_scene",
      "product_only",
      "not_said"
    ],
    "primary_subject": [
      "garment",
      "bag",
      "footwear",
      "accessory",
      "jewellery",
      "face",
      "body",
      "scene",
      "product_object",
      "text",
      "not_said"
    ],
    "styling_register": [
      "formal_tailored",
      "eveningwear",
      "casual",
      "sport_street",
      "utilitarian",
      "mixed",
      "not_said"
    ],
    "production": [
      "polished_commercial",
      "editorial_art",
      "documentary_lofi",
      "user_generated_style",
      "graphic_typographic",
      "cgi_3d",
      "not_said"
    ]
  },
  "lists": {
    "mood": {
      "options": [
        "austere",
        "serene",
        "intimate",
        "sensual",
        "playful",
        "ironic",
        "nostalgic",
        "opulent",
        "surreal",
        "aggressive",
        "romantic",
        "documentary"
      ],
      "min": 0,
      "max": 3
    }
  },
  "integers": {
    "street_couture_axis": {
      "min": 0,
      "max": 5
    }
  }
}
```
