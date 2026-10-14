# Forward template: every change of creative lead, read on the image

Registered 14 October 2026. STATUS: FROZEN. This file's hash is in `forward/designer-change-v1.sha256`
and the code that computes it, `adtone/forward_image.py`, is frozen with it by the hash in
`forward/designer-change-v1.code.sha256`. The test suite fails if either changes. A change to either is
a new version, never an edit. Facts that arrive later are recorded in `forward/events.yml` under
`designer_changes`; the design reads them and cannot be altered by them.

## Why a template, and why the image

`forward/saint-laurent-v1.md` reads Saint Laurent's paid advertising in Meta's library. That library has
not opened to this project, and under that design's own rules no data is recorded as no result. This
template reads what Focal already collects without anyone's permission: each house's homepage pictures,
month by month from the public web archive, and the crews credited on its campaigns. It applies to every
change of creative lead, not to one chosen house, so no call can be picked after the fact.

## State of the world at registration

- Saint Laurent confirmed on 9 October 2026 that Anthony Vaccarello's spring-summer 2027 show, on
  29 September 2026, was his last. No successor had been named. The house said it would announce its new
  creative leadership soon.
- Versace named Pieter Mulier on 5 February 2026, after his departure from Alaïa was announced on
  30 January 2026. His first Versace show had not taken place.
- The homepage archive held Saint Laurent's pictures through August 2026, 32 of them in 2026. It held
  nothing for Louis Vuitton since October 2024, Burberry since March 2025, Loro Piana since August 2025
  and Fendi since December 2025, and four pictures of Chanel since October 2025.

## Which changes it covers

An event is a newly named creative lead (a person, or a pair named together; never a collection
credited to the studio) at one of the houses whose homepages Focal reads (`data/homepages/captures`),
for womenswear or for the whole house, whose first show falls after 14 October 2026. D is the date of the
first womenswear or co-ed show or presentation credited to them. Menswear-only appointments are not
events. Saint Laurent's successor and Pieter Mulier at Versace are the first two.

Each event's record in `forward/events.yml` gives: the house; the predecessor's departure announcement,
where there is one; the new lead and the date the appointment was announced; D; the house the new lead
was creative lead of immediately before, if Focal reads it, and the date their departure from it was
announced; optionally the photographers credited on the house's fashion advertising in the 24 months
before the announcement (`crew_before`), recorded before D; and a source for every fact.

An event is void if no first show by a newly named lead takes place within 18 months of the later of the
predecessor's departure announcement and the appointment's announcement.

## The pictures

Homepage pictures from the web archive captures only (`data/homepages/captures`), read as a campaign
picture or as product on a model by the reader's frozen answers (`tone-v1@qwen2.5-vl-7b-instruct@cc594898137f`),
each with its pinned fingerprint (`openclip-vitb32-laion2b`). A picture counts once in a window, in the
month it was first shown. Focal's own captures, when they start, are a different sensor and are not
pooled into any comparison here.

Months are counted from the month of D. Before: months 12 to 1 before it. After: months 3 to 14 after it,
since campaigns follow shows.

Controls are the other houses with no dated move of a womenswear or co-ed creative lead (an appointment
announced, a first show or a departure announced, in `reference/designers.csv` or in this template's
records) from 6 months before the before window to 6 months after the after window, leaving out the new
lead's previous house.

## The four calls

**1. Magnitude.** The house's homepage moves more, like for like, than 90% of the controls. Like for
like: the change in the mean fingerprint within each kind of picture, the kinds weighed by the house's
own mix over both windows, scaled by its variance with the pictures of one month taken together, and
divided by the 95th percentile of the same statistic when whole months are moved between the windows at
random (all ways when there are at most 999, else 999 at random). Hit if the house's score is above the
90th percentile of the controls' scores. Needs at least 6 pictures in 2 months on each side, and at least
5 controls that also have them.

**2. Still itself.** The house's later pictures pick out its own earlier pictures less often than the
controls' do. In each of 200 draws, six of the house's pictures from after are set against six from
before of every house with at least six then, each set drawn afresh; a hit for the draw if the nearest,
by the cosine between mean fingerprints, is the house's own. Hit if the house's rate is below the 10th
percentile of the controls' rates, each computed the same way against the same houses. Needs at least
8 houses with pictures before and 5 controls.

**3. The crew.** None of the photographers credited on the house's fashion advertising in the 24 months
before the announcement is credited on it in the 12 months from the month of D. A pair credited together
counts as each of its members. Credits come from `reference/credits.csv`, rows dated to the month; the
earlier set is the event's `crew_before` when it was recorded before D, and otherwise the credits as they
stand. Hit if no one stayed. Needs at least one photographer on each side.

**4. Transfer.** Applies only when the new lead was creative lead of a house Focal reads immediately
before, and their departure from it was announced no more than 180 days before the appointment.
Otherwise not applicable, as for a promotion from inside the house. The origin is that house's pictures
in the 12 months before the announcement. The pull towards the origin is how much nearer, by cosine, the
house's mix-weighted mean fingerprint sits to the origin's mean after than before. Hit if the pull is
positive, at most 5% of the splits that move whole months between the windows pull as far, and the pull
is larger than towards any other house with at least 6 pictures in 2 months of those 12. Needs at least 6
pictures in 2 months for the origin, and at least 5 other houses.

## How likely each call is

Each call carries a probability, so the record is scored on calibration (the Brier score) and an easy
call does not flatter it. Each is the rate at which the same code, run on every earlier change of
creative lead whose windows had closed by June 2026, gave a hit, and is registered for every event unless
a different figure is recorded with its reason before the appointment is announced.

| Call | Earlier changes tested | Hits | Probability registered |
|---|---|---|---|
| 1. Magnitude | 19 | 8 | 0.42 |
| 2. Still itself | 20 | 5 | 0.25 |
| 3. The crew | 8 | 8 | 0.90 (eight of eight, shrunk as nine in ten) |
| 4. Transfer | 0 | 0 | 0.30 (a prior: no earlier move with a closed window could be tested) |

The earlier changes, and each call's result on them, are in `forward/designer-change-v1-base-rates.json`,
made with this code by `scripts/forward_base_rates.py`. Calls 1, 2 and 4 count changes whose after window
had closed by June 2026; call 3 counts those whose 12 months from the first show had closed by September
2026. With no change at all, beating the 90th percentile of ten controls still happens about one time in
seven by chance, and more often with fewer, which is why each call is registered with its probability
rather than read as proof on its own.

## Adjudication

Once per event, on the first day of the 18th month after the month of D, which leaves four months after
the after window for captures to arrive. Nothing about a house is computed for an event before then. If a
call has too little data on that day, the event waits, up to the first day of the 24th month after D;
then whatever cannot be computed is recorded as no result. A result once recorded is never recomputed.
Results go to `data/results/forward-designer-change-v1.json` and into `CLAIMS.md`, hits and misses alike.
A call counts as open from D.

## Machine-readable specification

```json
{
  "id": "designer-change-v1",
  "kind": "image-template",
  "registered": "2026-10-14",
  "code": "adtone/forward_image.py",
  "code_sha256": "forward/designer-change-v1.code.sha256",
  "events_key": "designer_changes",
  "first_show_after": "2026-10-14",
  "instrument": "tone-v1@qwen2.5-vl-7b-instruct@cc594898137f",
  "fingerprint": "openclip-vitb32-laion2b",
  "pre_months": [-12, -1],
  "post_months": [3, 14],
  "control_margin_months": 6,
  "min_pictures": 6,
  "min_months": 2,
  "min_controls": 5,
  "min_candidates": 8,
  "min_others": 5,
  "splits": 999,
  "draws": 200,
  "draw_size": 6,
  "seed": 20261014,
  "magnitude_quantile": 0.9,
  "itself_quantile": 0.1,
  "alpha": 0.05,
  "origin_months": 12,
  "origin_left_days": 180,
  "crew_before_months": 24,
  "crew_after_months": 12,
  "adjudicate_months": 18,
  "deadline_months": 24,
  "void_months": 18,
  "probabilities": {"magnitude": 0.42, "itself": 0.25, "crew": 0.9, "transfer": 0.3}
}
```
