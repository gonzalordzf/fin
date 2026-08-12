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
the account's real historical savings rate across 43 income-months is
-10.86% (spending more than earning, on average) — only 16 of 45 months
(35.6%) closed positive. 15% is a deliberate middle ground: a ~26
percentage-point swing from the historical average, enough to represent
real behavior change, but short of the common 20% ("50/30/20") guideline,
which would be a bigger jump than the track record supports as a credible
first target. Meant to be revisited once there's real progress data —
edit this constant directly, nothing else in the app assumes this exact
number.
"""
