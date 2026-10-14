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
