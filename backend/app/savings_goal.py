"""Savings goal target — not sourced from any statement (it's a goal, not
a fact), so it lives here rather than in manual_data.py. A percentage of
monthly income rather than a fixed peso amount: income here swings from
$0 to $450k+ across real months (salary plus irregular investment/other
income), so a fixed amount would be trivial in high months and impossible
in low ones — see CLAUDE.md's "Números ancla" for the underlying data.
"""

TARGET_SAVINGS_RATE_PCT = 15.0
"""% of each month's income the goal asks to save (Quedó / Entró >= this).

Chosen 2026-08-12 as a reasoned default, not derived from any statement:
the account's real historical savings rate across 43 income-months was
-10.86% at the time (spending more than earning, on average) — only 16 of
45 months (35.6%) closed positive. 15% was picked as a deliberate middle
ground: a ~26 percentage-point swing from the historical average, enough
to represent real behavior change, but short of the common 20%
("50/30/20") guideline, which would be a bigger jump than the track
record supported as a credible first target.

Updated 2026-08-12 (same day, after backfilling BBVA TDC's ene-2023 to
jun-2024 credit card history — see parsers/bbva_credit_legacy.py): the
real historical rate was -14.41% across 43 income-months, 15 of 45
closed positive.

Updated again 2026-08-12 (same day, after fixing a real categorization
bug — see rules.py's INVESTMENT_ACCOUNT_TRANSFER_PATTERNS): transfers to
the user's own GBM investment account were being counted as spend
("Inversión", kind=EXPENSE) rather than excluded as a pure transfer
(user confirmed 2026-08-12 they should be treated like moving money
between the user's own accounts, not consumption) — inflating measured
gasto by ~$1.2M MXN net across the account's history in one direction,
with a further ~$477k of offsetting inflows sitting uncounted in a
different bucket instead of netting against it. With that fixed, the
real historical rate is +7.57% across 43 income-months, 18 of 45 closed
positive — a materially different picture from either number above.

Left at 15% through all of these corrections rather than re-deriving a
new "middle ground" each time: none of them were behavior changes, they
were the picture becoming more accurate, and re-tuning the target every
time old data gets fixed would make it a moving target instead of
something to hold steady against. 15% is no longer as much of a stretch
now that the real baseline is positive, if anything it reads as more
achievable than when it was set — still a reasonable target, not
re-derived. Meant to be revisited once there's real progress data — edit
this constant directly, nothing else in the app assumes this exact
number.
"""
