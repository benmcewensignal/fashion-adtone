# The archive clock

Meta keeps an ad delivered to the EU or the UK for a year after its last impression. A house's
pre-debut look is in its old-look ads, which usually stop when the first new-era campaign replaces
them. So each house's baseline leaves the archive about a year after that first new campaign.

Estimated on 6 October 2026 from the earliest dated new-era advertising in `reference/credits.csv`.
Dates are months unless a day is on file. An old ad still running later extends its own deadline,
so these are the earliest likely losses, not certain ones.

| house | debut | first new-era campaign on file | old look leaves the archive about |
|---|---|---|---|
| Balenciaga | 4 Oct 2025 | 21 Oct 2025, Piccioli's first campaign | 21 Oct 2026 |
| Gucci | 23 Sep 2025 | Jan 2026 | Jan 2027 |
| Bottega Veneta | 28 Sep 2025 | Jan 2026 | Jan 2027 |
| Dior | 1 Oct 2025 | Jan 2026 | Jan 2027 |
| Chanel | 6 Oct 2025 | Jan 2026 | Jan 2027 |
| Jil Sander | 24 Sep 2025 | Feb 2026 | Feb 2027 |
| Loewe | 3 Oct 2025 | Feb 2026 | Feb 2027 |
| Fendi | 25 Feb 2026 | Jul 2026 | Jul 2027 |
| Celine, Maison Margiela | Jul 2025 | none dated on file | likely already gone |

Balenciaga matters most: its Demna-era ads are the origin look for H2, which needs at least 8 of
them. Collection runs in this order (`config.DEADLINE_ORDER`), and processing already takes the
soonest-to-expire ads first. Neither starts until the Meta identity check is done and the token is in
the repository: the archive is served only to people who have confirmed their identity.
