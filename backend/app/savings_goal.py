"""Savings goal target — not sourced from any statement (it's a goal, not
a fact), so it lives here rather than in manual_data.py. A percentage of
monthly income rather than a fixed peso amount: income here swings from
$0 to $450k+ across real months (salary plus irregular investment/other
income), so a fixed amount would be trivial in high months and impossible
in low ones — see CLAUDE.md's "Números ancla" for the underlying data.
"""

from __future__ import annotations

import datetime
import statistics

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

AMBITIOUS_SAVINGS_RATE_PCT = 30.0
"""The "ambitious" scenario alongside TARGET_SAVINGS_RATE_PCT in
/savings-projection — user-requested 2026-08-12 ("proyección ambiciosa
del 30%"), not derived from any statement. Double the minimum goal,
chosen as a round, clearly-stretch number rather than computed from
anything — same spirit as TARGET_SAVINGS_RATE_PCT's own citation above."""

PROJECTION_MILESTONE_YEARS: tuple[int, ...] = (2026, 2027, 2028)
"""End-of-year projection checkpoints shown in Meta de ahorro —
user-requested 2026-08-12."""

PROJECTION_WINDOW_MONTHS = 12
"""Trailing window (of CLOSED months — the current, still-in-progress
month is excluded first) used as the "typical month" basis for
/savings-projection. 12 rather than all-time: income here swings from $0
to $450k+ across the account's whole history (salary plus irregular
investment/other income spanning very different life circumstances since
2022), so an all-time average would blend in periods that don't reflect
where things stand now. Median, not mean, for the same reason within the
window: two real months in the trailing 12 as of 2026-08-12 (2026-03 at
$451,172 and 2026-04 at $340,523) are large enough one-off inflows that a
mean would overstate what a "typical" month looks like — median isn't
pulled by them (mean $163,541 vs median $90,228 in that same window, as
of 2026-08-12 — a ~1.8x difference, confirming the skew is real, not
theoretical)."""


def _months_remaining_to_year_end(today: datetime.date, year: int) -> int:
    """Full CLOSED calendar months from today's month (exclusive — it's
    still in progress) through December of `year`, inclusive. Negative if
    `year` has already fully closed relative to `today`."""
    return (year - today.year) * 12 + (12 - today.month)


def compute_savings_projection(
    months: list[dict],
    current_net_worth_mxn: float,
    today: datetime.date | None = None,
) -> dict:
    """A simple, linear month-by-month projection of what finances would
    look like at each PROJECTION_MILESTONE_YEARS end if the minimum
    (TARGET_SAVINGS_RATE_PCT) or ambitious (AMBITIOUS_SAVINGS_RATE_PCT)
    savings rate were hit every month from now on, grounded in the
    trailing PROJECTION_WINDOW_MONTHS of real income and gasto fijo (see
    that constant's docstring for why median-of-trailing-12, not
    all-time-mean).

    `months` is _compute_monthly_summary's output — passed in rather than
    queried here so this stays a pure function or a session, matching how
    /savings-goal already reuses the same computation instead of risking
    two independently-computed pictures of the same months drifting apart.

    Deliberately linear: cumulative_savings for a milestone is just
    monthly_savings × months_remaining, added once on top of
    current_net_worth_mxn. No investment growth/compounding is assumed on
    money already invested (GBM, AFORE, etc. keep growing or not on their
    own — this projection doesn't guess at a return rate for them), and no
    inflation adjustment. Both are real simplifications, surfaced in the
    response's own caveats rather than silently baked in.
    """
    today = today or datetime.date.today()
    current_month = today.strftime("%Y-%m")

    # The current month is still in progress — its income reads $0 or
    # partial until its statements are actually imported, so it isn't a
    # real "typical month" data point.
    closed = [m for m in months if m["month"] != current_month]
    window = closed[-PROJECTION_WINDOW_MONTHS:]
    income_sample = [m["income"] for m in window if m["income"] > 0]
    fijo_sample = [m["fijo_total"] for m in window]

    if not income_sample:
        raise ValueError(
            f"Not enough closed income-months in the trailing {PROJECTION_WINDOW_MONTHS} to build a projection"
        )

    median_income = round(statistics.median(income_sample), 2)
    median_fijo = round(statistics.median(fijo_sample), 2)

    scenarios = []
    for label, rate_pct in (
        ("minimo", TARGET_SAVINGS_RATE_PCT),
        ("ambicioso", AMBITIOUS_SAVINGS_RATE_PCT),
    ):
        monthly_savings = round(median_income * rate_pct / 100, 2)
        monthly_variable_budget = round(median_income - median_fijo - monthly_savings, 2)
        milestones = []
        for year in PROJECTION_MILESTONE_YEARS:
            months_remaining = _months_remaining_to_year_end(today, year)
            cumulative_savings = round(monthly_savings * months_remaining, 2)
            milestones.append(
                {
                    "year": year,
                    "months_remaining": months_remaining,
                    "cumulative_savings": cumulative_savings,
                    "projected_net_worth_mxn": round(current_net_worth_mxn + cumulative_savings, 2),
                }
            )
        scenarios.append(
            {
                "label": label,
                "rate_pct": rate_pct,
                "monthly_savings": monthly_savings,
                "monthly_variable_budget": monthly_variable_budget,
                "milestones": milestones,
            }
        )

    return {
        "as_of": today.isoformat(),
        "window_months_used": len(window),
        "median_monthly_income": median_income,
        "median_monthly_fijo": median_fijo,
        "current_net_worth_mxn": round(current_net_worth_mxn, 2),
        "scenarios": scenarios,
        "caveats": [
            "Proyección lineal: no asume rendimiento de inversiones sobre lo ya invertido "
            "(GBM, AFORE, etc. — esas cuentas crecen o no por su cuenta, esto no le mete un "
            "% de retorno inventado) ni ajusta por inflación.",
            f"Base de 'mes típico': mediana de los últimos {PROJECTION_WINDOW_MONTHS} meses "
            "cerrados de ingreso y gasto fijo reales, no un promedio de todo el historial "
            "(el ingreso varía demasiado — de $0 a $450k+ en meses reales — para que un "
            "promedio de todo el historial sea representativo de un mes típico ahora).",
            "El gasto variable implícito de cada escenario es lo que sobra después del gasto "
            "fijo mediano y la meta de ahorro — no es un presupuesto ya validado contra "
            "categorías reales, es aritmética simple (ingreso - fijo - ahorro).",
        ],
    }
