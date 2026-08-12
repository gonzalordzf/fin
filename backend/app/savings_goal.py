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
picked as a deliberate middle ground above the account's real historical
savings rate — enough above it to represent real behavior change, but
short of the common 20% ("50/30/20") guideline, which would've been a
bigger jump than the track record supported as a credible first target.

That historical rate moved three times the same day as real data gaps
and a categorization bug got fixed, each time making the picture more
accurate rather than reflecting any actual behavior change — logged here
so the number's provenance stays traceable instead of looking arbitrary:
  1. -10.86% (43 income-months, 16/45 positive) — initial baseline.
  2. -14.41% — after backfilling BBVA TDC's ene-2023–jun-2024 credit card
     history (parsers/bbva_credit_legacy.py): real spend that was simply
     missing from the system before.
  3. +7.57% (43 months, 18/45 positive) — after fixing GBM transfers
     being counted as spend under "Inversión" (kind=EXPENSE) instead of
     excluded as a pure transfer between the user's own accounts (user
     confirmed 2026-08-12 — see rules.py's
     INVESTMENT_ACCOUNT_TRANSFER_PATTERNS): ~$1.2M MXN of net outflow had
     been inflating measured gasto, plus ~$477k of offsetting inflows
     sitting uncounted in a different bucket instead of netting against it.
  4. +2.73% (44 months, 18/45 positive) — current, after backfilling
     BBVA (débito)'s abr-may-jun 2025 gap (previously missing statements,
     found and uploaded by the user) and BBVA TDC's real Noviembre 2023
     statement (replacing the synthetic net-adjustment transaction that
     had stood in for it — see manual_data.py's MANUAL_TRANSACTIONS).

Left at 15% through all of these — none were behavior changes, and
re-tuning the target every time an old data gap gets filled would make
it a moving target instead of something to hold steady against. Meant to
be revisited once there's real progress data — edit this constant
directly, nothing else in the app assumes this exact number.
"""
