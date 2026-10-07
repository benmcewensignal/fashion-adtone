# Pairs rubric v1

Frozen 2026-10-07, before any pair was judged. Exploratory: not in the pre-registration. Any
change is v2, and judgements from different versions are never pooled. `rubric/pairs-v1.sha256`
holds this file's hash; the test suite fails if the file changes.

## What is being judged

Where a picture sits within luxury fashion imagery, on five axes. The judge sees two pictures
from luxury fashion houses' own homepages and says which one lies further towards one end of an
axis. Every judgement is made against another luxury picture, so the scale is set by the genre
itself rather than by pictures in general, where nearly all of these would sit at one end. Each
pair is asked in both orders, because judges of this kind are swayed by which picture comes first.
A picture's position on an axis is fitted from all its comparisons (Bradley and Terry).

The five axes are a first draft, chosen on 7 October 2026 for the bake-off of readers; each is
described at both ends so that neither end is defined only as the absence of the other.

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
  "version": "pairs-v1",
  "answers": ["first", "second"],
  "question": "Axis: from {away} to {toward}.\n{away_cap}: {away_means}.\n{toward_cap}: {toward_means}.\nWhich picture is more {toward}? Answer first or second.",
  "axes": [
    {"id": "opulent", "away": "austere", "toward": "opulent",
     "away_means": "spare and reduced; few elements, plain surfaces, a quiet palette, nothing decorative",
     "toward_means": "rich and abundant; ornament, layered textures, luxurious materials and settings, a sense of display"},
    {"id": "intimate", "away": "remote", "toward": "intimate",
     "away_means": "distant or withheld; the person is far off, closed, aloof or monumental, or there is no one to be close to",
     "toward_means": "close and personal; the viewer feels near the person, in a private, unguarded moment"},
    {"id": "staged", "away": "naturalistic", "toward": "staged",
     "away_means": "could be a real moment in a real place; natural light, an unforced posture, nothing visibly built",
     "toward_means": "visibly constructed; a built set, a posed tableau, theatrical or artificial light"},
    {"id": "contemporary", "away": "classical", "toward": "contemporary",
     "away_means": "timeless codes; heritage, elegance and craft that could belong to any decade",
     "toward_means": "of this moment; current youth culture, technology, street references, the look of now"},
    {"id": "provocative", "away": "restrained", "toward": "provocative",
     "away_means": "discreet and composed; nothing that confronts, unsettles or flirts",
     "toward_means": "made to provoke a reaction; confronting, overtly sensual, strange or unsettling"}
  ]
}
```
