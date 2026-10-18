# Claims ledger

Track record: 0 hits · 0 misses · 0 open · 11 calls registered

Every call carries the probability it was registered with, so the record is scored on calibration (the
Brier score) as well as on hits, and an easy call does not flatter it.

*No open claims yet. A call on the image is open from the first show it waits on; a call on the
advertising is open from the later of that show and the end of the silent baseline season. Results on the
2025 debuts are validation, not calls.*

## Registered

- **saint-laurent-v1**, registered 5 October 2026 before any successor to Anthony Vaccarello was named
  (`forward/saint-laurent-v1.md`, frozen by hash). Three predictions about Saint Laurent's advertising
  after the successor's first show: a shift larger than control placebos, a changepoint within 120
  days of the show, and, if the successor comes from a collected house, a transfer of that house's
  tone. It reads paid advertising in Meta's library, which has not opened to this project; with no
  data by 450 days after the show it is recorded as no result. Waiting for the event.
- **designer-change-v1**, registered 14 October 2026 (`forward/designer-change-v1.md`, frozen by hash
  with its code). Every change of creative lead whose first show falls after that date, read on the
  houses' homepage pictures and campaign credits, so nothing waits on a platform. Four calls on each:
  - magnitude: the homepage moves more, like for like, than at 90% of houses without a change (0.42);
  - still itself: the later pictures pick out the house's own earlier ones less often than at 90% of
    them (0.25);
  - the crew: none of the photographers of the two years before stays (0.90);
  - transfer: the pictures move towards the new lead's previous house, when Focal reads it, more than
    towards any other (0.30).

  The probabilities are the rates on earlier changes (`forward/designer-change-v1-base-rates.json`);
  transfer's is a prior, since no earlier move could be tested.
  - Saint Laurent, successor to Anthony Vaccarello: waiting for the appointment.
  - Versace, Pieter Mulier, from Alaïa: waiting for his first show.

## Validation against history

Retrospective results are never calls; they are recorded here because the record of what was tried belongs
beside the record of what was called.

- **The 2025 debuts (`PREREGISTRATION.md`, frozen 5 October 2026): not testable.** Recorded 18 October
  2026. The three retrospective tests (H1, the houses with a new creative director moved more than the
  controls; H2, Gucci moved towards Balenciaga's earlier tone; H3, the detector finds the known breaks and
  stays quiet on the controls) each need at least eight brand-image concepts in three campaign blocks on both
  sides of a debut, and H1 and H3 at least four treated houses with that. The ad library keeps a year of ads,
  and its first collection for this project ran on 11 October 2026 through a vendor's reading of the library
  (`adtone.metaweb`, instrument `tone-v1` by the Qwen reader), so the advertising from before the twelve
  debuts of 2025 (March to October) had already left it, and Fendi's debut of February 2026 has its months
  before in the library but little brand imagery in them: of the thirteen treated houses with ads on file,
  none has more than five brand-image concepts before its debut (Fendi 5, Chanel 3, Dior and Loewe 1, the
  other nine none), against 7 to 141 after. Under the sensitivity inclusion (product on a model as well), three houses reach eight
  concepts before the debut (Fendi 47, Loewe 9, Balenciaga 8), short of the four H1 needs, and Gucci has
  none, so H2 is not testable either. The pre-registration foresaw this for Celine and Maison Margiela,
  whose debuts fell earliest; it holds for all of them. The tests are not re-specified: they stand as frozen,
  with this result. The instrument's validation against history therefore cannot come from the 2025 debuts
  on this channel; the designer-change-v1 calls above read the homepage pictures, which the web archive
  holds back to 2015 and which no platform's memory limits, and the paid-social record begins with the
  collections of autumn-winter 2026 onward (`PREREGISTRATION-AMENDMENT-3.md`).
