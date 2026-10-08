# Pairs rubric v2

Frozen 2026-10-08, before any pair was judged with it. Exploratory: not in the pre-registration. Any
change is v3, and judgements from different versions are never pooled. `rubric/pairs-v2.sha256`
holds this file's hash; the test suite fails if the file changes.

## What is being judged

As in v1: where a picture sits within luxury fashion imagery, on five axes, judged by comparing it
with another luxury picture, each pair asked in both orders, a picture's position fitted from all
its comparisons (Bradley and Terry). The prompt is v1's, word for word. What changes is how each end
of four axes is described.

## What changed from v1, and why

In the bake-off of 7 and 8 October 2026 only restrained to provocative passed. Intimate and staged
were judged as single overall impressions, and the judge changed its answer with the order of the
pictures too often; opulent and contemporary agreed with the person too rarely. v2 describes each end
by the visible parts the research uses to build that impression, so that a judgement rests on things
in the picture:

- **Austere to opulent:** open space and the number of things in the picture, after the white space
  of Pracejus, Olsen and O'Guinn (2006) and the design complexity of Pieters, Wedel and Batra (2010).
- **Remote to intimate:** distance, gaze, angle and turn, after Kress and van Leeuwen's social
  distance (size of frame), demand and offer (gaze), involvement (horizontal angle) and power
  (vertical angle) in *Reading Images*.
- **Naturalistic to staged:** Kress and van Leeuwen's modality, the cues by which a picture reads as
  a record of something real or as something made: light, colour, setting and pose.
- **Classical to contemporary:** abstract against concrete, after the abstractness of luxury in
  Hansen and Wänke (Journal of Economic Psychology, 2011): codes that could belong to any decade
  against details that place the picture in the present.
- **Restrained to provocative:** unchanged; it passed.

The person's 150 pairs from the bake-off stay the yardstick. They were judged against v1's
descriptions of the same named ends, so v2 is tested on whether its sharper descriptions still pick
out what the person saw.

## Instructions given to the judge

The text between the markers is sent verbatim as the system prompt; the question for each pair is
built from the specification below.

<!-- PROMPT START -->
You are shown two pictures from the homepages of luxury fashion houses: campaign photographs and
clothes shown on models. Both belong to the same world of high fashion and luxury, so judge them
against each other, not against pictures in general. Judge only what is visible. Do not use
knowledge of any brand, designer or campaign. Do not identify any person, and do not guess anyone's
identity, age, ethnicity or other personal characteristics.

You are given one axis with both of its ends described. Answer with one word: first, if the first
picture lies further towards the end named in the question, or second, if the second picture does.
If they are close, choose the one that leans further, however slightly.
<!-- PROMPT END -->

## Machine-readable specification

```json
{
  "version": "pairs-v2",
  "answers": ["first", "second"],
  "question": "Axis: from {away} to {toward}.\n{away_cap}: {away_means}.\n{toward_cap}: {toward_means}.\nWhich picture is more {toward}? Answer first or second.",
  "axes": [
    {"id": "opulent", "away": "austere", "toward": "opulent",
     "away_means": "spare and reduced; few things in the frame, much empty or plain space around them, plain surfaces, a quiet palette, nothing decorative",
     "toward_means": "rich and abundant; many things in the frame and little empty space, ornament, layered textures, luxurious materials and settings, a display of plenty"},
    {"id": "intimate", "away": "remote", "toward": "intimate",
     "away_means": "the viewer is kept at a distance; the person is seen whole or small in the frame, looks away or past the viewer, is turned side-on or seen from below, or there is no one to be close to",
     "toward_means": "the viewer is brought close; the person is framed near, head and shoulders or waist up, looks at the viewer or is caught in a private, unguarded moment, and faces the viewer at eye level"},
    {"id": "staged", "away": "naturalistic", "toward": "staged",
     "away_means": "reads as a record of a real moment in a real place; natural light and colour, a real setting, an unforced posture, nothing visibly built",
     "toward_means": "reads as something made; a built set or arranged tableau, theatrical or artificial light, colour or contrast pushed for effect, a pose held for the camera"},
    {"id": "contemporary", "away": "classical", "toward": "contemporary",
     "away_means": "abstract and timeless; heritage codes, elegance and craft, nothing that dates the picture to a particular year",
     "toward_means": "concrete and of this moment; current youth culture, technology, street references, styling or settings that place the picture in the present"},
    {"id": "provocative", "away": "restrained", "toward": "provocative",
     "away_means": "discreet and composed; nothing that confronts, unsettles or flirts",
     "toward_means": "made to provoke a reaction; confronting, overtly sensual, strange or unsettling"}
  ]
}
```

## Which axes go forward

Fixed with this file, before any pair was judged with it, and the yardstick the bake-off fixed for
comparisons read as probabilities: on the bake-off's design of 296 pictures, read by Qwen3-VL-32B at
its pinned weights as the weight it puts on each answer, an axis goes forward when, once the judge's
lean towards one position is taken out, the two orders point the same way on at least three pairs in
four; two crops of one picture sit closer on it than two random pictures, under half the gap; and it
agrees with the person on at least 60% of their pairs on that axis.

## Sources

- Hansen, J. and Wänke, M. (2011). The abstractness of luxury. *Journal of Economic Psychology*,
  32(5), 789 to 796.
- Kress, G. and van Leeuwen, T. (1996; 3rd edition 2021). *Reading Images: The Grammar of Visual
  Design*. Routledge.
- Pieters, R., Wedel, M. and Batra, R. (2010). The stopping power of advertising: Measures and
  effects of visual complexity. *Journal of Marketing*, 74(5), 48 to 60.
- Pracejus, J. W., Olsen, G. D. and O'Guinn, T. C. (2006). How nothing became something: White space,
  rhetoric, history, and meaning. *Journal of Consumer Research*, 33(1), 82 to 90.
