"""FastAPI backend: import trigger, transactions, net worth, spending by category.

Run with:
    uvicorn app.main:app --reload
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from sqlalchemy import func

from app.card_dates import next_cutoff_date, next_payment_due_date
from app.classify import classify_transfers
from app.db import get_session, init_db
from app.importers import afore, amex, balagan, bbva, bbva_credit, bitso, gbm, optimax, revolut, shareworks
from app.parsers import balagan as balagan_parser
from app.rules import classify_merchants, classify_spend_frequency
from app.savings_goal import TARGET_SAVINGS_RATE_PCT, compute_savings_projection
from app.models import (
    Account,
    AlternativeInvestmentEntry,
    Category,
    CategoryKind,
    EquityCompensationEntry,
    HoldingSnapshot,
    Transaction,
)

app = FastAPI(title="Finanzas Personales API")

DATA_IMPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "imports"

# Maps each account's import folder name to its importer function and the
# file extensions it accepts. GBM is intentionally absent: its statements
# are per-contract (AAU94801/AAU94802) and the importer resolves the target
# sub-account from the file's own contents, so it's triggered the same way
# as everything else — just note it isn't a 1:1 folder-to-account mapping.
#
# AMEX is the one account with two source formats landing in the same
# folder: recent history as CSV exports, anything older than AMEX's export
# tool covers as PDF statements (app/parsers/amex_pdf.py) — mapped here by
# extension to their respective importer functions instead of a single
# (fn, exts) pair like every other account.
IMPORTERS = {
    "BBVA": (bbva.import_bbva_statement, {".pdf"}),
    "BBVA TDC": (bbva_credit.import_bbva_credit_statement, {".pdf"}),
    "AMEX": {".csv": amex.import_amex_csv, ".pdf": amex.import_amex_pdf_statement},
    "GBM": (gbm.import_gbm_statement, {".xml"}),
    "Bitso": (bitso.import_bitso_report, {".csv"}),
    "Revolut": (revolut.import_revolut_statement, {".pdf"}),
    "Balagan": (balagan.import_balagan_statement, {".pdf"}),
    "Optimax": (optimax.import_optimax_statement, {".pdf"}),
    "Shareworks": (shareworks.import_shareworks_statement, {".pdf"}),
    "AFORE": (afore.import_afore_statement, {".pdf"}),
}


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.post("/import/{account}")
def trigger_import(account: str) -> dict:
    """Scans data/imports/<account>/ for files matching that source's
    extension and runs its importer on each. Every importer is idempotent
    (dedups against what's already in the database), so this is safe to
    call repeatedly, including on files already imported."""
    if account not in IMPORTERS:
        raise HTTPException(404, f"Unknown account {account!r}. Known: {list(IMPORTERS)}")

    mapping = IMPORTERS[account]
    if isinstance(mapping, dict):
        ext_to_fn = mapping
    else:
        importer_fn, extensions = mapping
        ext_to_fn = {ext: importer_fn for ext in extensions}
    folder = DATA_IMPORTS_DIR / account
    if not folder.exists():
        raise HTTPException(404, f"No import folder at {folder}")

    details = []
    for path in sorted(folder.iterdir()):
        importer_fn = ext_to_fn.get(path.suffix.lower())
        if importer_fn is None:
            continue
        try:
            result = importer_fn(str(path))
        except Exception as exc:  # noqa: BLE001 — surface per-file failures without aborting the batch
            details.append({"file": path.name, "error": str(exc)})
            continue
        details.append({"file": path.name, "result": result})

    return {"account": account, "files_seen": len(details), "details": details}


@app.post("/classify")
def trigger_classify() -> dict:
    """Runs rule-based classification over every uncategorized transaction:
    self-transfers by titular/RFC first (most certain), then card
    autopay/refund/investment-institution/payroll/merchant rules, then
    fijo/variable (relies on category already being set where possible, so
    it runs last). Idempotent and safe to call repeatedly, including after
    new imports."""
    with get_session() as session:
        transfers = classify_transfers(session)
        merchants = classify_merchants(session)
        spend_frequency = classify_spend_frequency(session)
    return {
        "transfers_classified": transfers,
        "merchants_classified": merchants,
        "spend_frequency_tagged": spend_frequency,
    }


@app.get("/accounts")
def list_accounts() -> list[dict]:
    with get_session() as session:
        result = []
        for a in session.query(Account).order_by(Account.id).all():
            entry = {
                "id": a.id,
                "name": a.name,
                "institution": a.institution,
                "kind": a.kind.value,
                "currency": a.currency,
                "parent": a.parent.name if a.parent else None,
                "is_credit_card": a.is_credit_card,
            }
            if a.is_credit_card:
                # Best-effort: business-day counting only skips Sat/Sun, no
                # Mexican bank holidays — see card_dates.py's docstring.
                entry["next_cutoff_date"] = next_cutoff_date(a).isoformat()
                entry["next_payment_due_date"] = next_payment_due_date(a).isoformat()
            result.append(entry)
        return result


@app.get("/transactions")
def list_transactions(
    account: str | None = None,
    category: str | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    limit: int = 200,
) -> list[dict]:
    with get_session() as session:
        q = session.query(Transaction).join(Account)
        if account:
            q = q.filter(Account.name == account)
        if category:
            q = q.join(Category, Transaction.category_id == Category.id).filter(
                Category.name == category
            )
        if date_from:
            q = q.filter(Transaction.date >= date_from)
        if date_to:
            q = q.filter(Transaction.date <= date_to)
        q = q.order_by(Transaction.date.desc()).limit(limit)

        return [
            {
                "id": t.id,
                "account": t.account.name,
                "date": t.date.isoformat(),
                "amount": t.amount,
                "currency": t.currency,
                "description": t.description,
                "category": t.category.name if t.category else None,
            }
            for t in q.all()
        ]


@app.get("/spending-by-category")
def spending_by_category(
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
) -> list[dict]:
    """Nets every transaction in each EXPENSE category — inflows included,
    not just charges — and reports the result as spending. Self-transfers
    (kind=TRANSFER) are excluded entirely: a movement between the user's
    own accounts is neither spending nor income, per docs/04-gotchas.md.

    The netting is the whole point and replaced an earlier `amount < 0`
    filter that was silently wrong for any category where money legitimately
    comes back. Two real cases: 'Mundial' holds both the World Cup tickets
    bought for a group of friends and their SPEI reimbursements ($1,035,647
    out vs $898,245 back), and 'Vivienda' holds both the rent paid and the
    roommates' share of it (~$17,400/mo back). Summing only the charges
    reported the gross outflow as if it were all personal spend — off by
    $890k on Mundial alone. A category whose inflows exceed its outflows
    now correctly reports a negative "amount", i.e. a net inflow.

    'Sin categoría' is deliberately NOT netted (still charges-only): with no
    category there's no way to tell a refund from unrelated income, and
    netting salary-like inflows against it would understate what's left to
    review."""
    with get_session() as session:

        def date_filtered(q):
            if date_from:
                q = q.filter(Transaction.date >= date_from)
            if date_to:
                q = q.filter(Transaction.date <= date_to)
            return q

        categorized = date_filtered(
            session.query(Category.name, Category.nature, func.sum(Transaction.amount))
            .join(Transaction, Transaction.category_id == Category.id)
            .filter(Category.kind == CategoryKind.EXPENSE)
        ).group_by(Category.name, Category.nature)

        uncategorized_total = date_filtered(
            session.query(func.sum(Transaction.amount)).filter(
                Transaction.category_id.is_(None), Transaction.amount < 0
            )
        ).scalar() or 0.0

        result = [
            {
                "category": name,
                "nature": nature.value if nature else None,
                "amount": round(-amt, 2),
            }
            for name, nature, amt in categorized.all()
        ]
        if uncategorized_total:
            result.append({"category": "Sin categoría", "nature": None, "amount": round(-uncategorized_total, 2)})

        return sorted(result, key=lambda r: -r["amount"])


def _compute_monthly_summary(session, currency: str) -> list[dict]:
    """Month-by-month income, expense (by category, net), and cash flow —
    one currency at a time, same principle as /net-worth's per-currency
    totals: no FX conversion, so mixing them would silently produce a
    meaningless number. Defaults to MXN since that's where the
    transactional accounts (BBVA/AMEX/Revolut) and salary live.

    Expense categories are netted exactly like /spending-by-category (a
    category with real reimbursements inside it, e.g. 'Mundial' or
    'Vivienda', reports the true net cost, not the gross outflow) — see
    that endpoint's docstring for why summing only amount<0 would be wrong.
    'Sin categoría' stays charges-only for the same reason: no way to tell
    a refund from unrelated income once uncategorized.

    Shared by /monthly-summary and /savings-goal — both need the same
    per-month income/expense/net, and computing it twice risked the two
    endpoints silently drifting apart.
    """
    month_expr = func.strftime("%Y-%m", Transaction.date)

    income_rows = (
        session.query(month_expr, func.sum(Transaction.amount))
        .join(Category, Transaction.category_id == Category.id)
        .filter(Category.kind == CategoryKind.INCOME, Transaction.currency == currency)
        .group_by(month_expr)
        .all()
    )

    expense_rows = (
        session.query(month_expr, Category.name, Category.nature, func.sum(Transaction.amount))
        .join(Category, Transaction.category_id == Category.id)
        .filter(Category.kind == CategoryKind.EXPENSE, Transaction.currency == currency)
        .group_by(month_expr, Category.name, Category.nature)
        .all()
    )

    uncategorized_rows = (
        session.query(month_expr, func.sum(Transaction.amount))
        .filter(
            Transaction.category_id.is_(None),
            Transaction.amount < 0,
            Transaction.currency == currency,
        )
        .group_by(month_expr)
        .all()
    )

    # Fijo/variable, netted the same way categories are — a month can carry
    # a refund inside either bucket (e.g. a fijo insurance adjustment), and
    # classify_spend_frequency only tags real EXPENSE-kind or
    # amount<0-uncategorized rows, so this stays consistent with
    # total_expense above rather than double-counting transfers/income.
    frequency_rows = (
        session.query(month_expr, Transaction.spend_frequency, func.sum(Transaction.amount))
        .filter(Transaction.currency == currency, Transaction.spend_frequency.isnot(None))
        .group_by(month_expr, Transaction.spend_frequency)
        .all()
    )

    months: dict[str, dict] = {}

    def month_entry(month: str) -> dict:
        return months.setdefault(
            month,
            {
                "month": month,
                "income": 0.0,
                "categories": [],
                "uncategorized_expense": 0.0,
                "fijo_total": 0.0,
                "variable_total": 0.0,
            },
        )

    for month, total in income_rows:
        month_entry(month)["income"] = round(total, 2)

    for month, name, nature, amt in expense_rows:
        month_entry(month)["categories"].append(
            {"category": name, "nature": nature.value if nature else None, "amount": round(-amt, 2)}
        )

    for month, total in uncategorized_rows:
        month_entry(month)["uncategorized_expense"] = round(-total, 2)

    for month, frequency, total in frequency_rows:
        key = "fijo_total" if frequency.value == "fijo" else "variable_total"
        month_entry(month)[key] = round(-total, 2)

    result = []
    for month in sorted(months):
        entry = months[month]
        entry["categories"].sort(key=lambda c: -c["amount"])
        total_expense = sum(c["amount"] for c in entry["categories"]) + entry["uncategorized_expense"]
        entry["total_expense"] = round(total_expense, 2)
        entry["net"] = round(entry["income"] - total_expense, 2)
        result.append(entry)

    return result


@app.get("/monthly-summary")
def monthly_summary(currency: str = "MXN") -> list[dict]:
    """See _compute_monthly_summary — this route just opens the session."""
    with get_session() as session:
        return _compute_monthly_summary(session, currency)


@app.get("/savings-goal")
def savings_goal(currency: str = "MXN") -> dict:
    """Per-month progress against TARGET_SAVINGS_RATE_PCT (app/savings_goal.py
    — a reasoned default, not a fact from any statement, see that module's
    docstring for how it was chosen and why it's a % of income rather than
    a fixed peso amount).

    A month with $0 income (goal not applicable — nothing to measure a
    rate against) reports met=None rather than being silently skipped or
    counted as a miss. current_streak counts consecutive met=True months
    working backward from the most recent evaluated (non-None) month.
    """
    with get_session() as session:
        months = _compute_monthly_summary(session, currency)

    target_pct = TARGET_SAVINGS_RATE_PCT
    result_months = []
    for entry in months:
        income = entry["income"]
        net = entry["net"]
        target_amount = round(income * target_pct / 100, 2) if income > 0 else 0.0
        met = None if income <= 0 else net >= target_amount
        result_months.append(
            {
                "month": entry["month"],
                "income": income,
                "net": net,
                "target_amount": target_amount,
                "met": met,
            }
        )

    evaluated = [m for m in result_months if m["met"] is not None]
    months_met = sum(1 for m in evaluated if m["met"])

    current_streak = 0
    for m in reversed(evaluated):
        if not m["met"]:
            break
        current_streak += 1

    total_income = sum(m["income"] for m in evaluated)
    total_net = sum(m["net"] for m in evaluated)
    historical_rate = round(total_net / total_income * 100, 2) if total_income else 0.0

    return {
        "target_pct": target_pct,
        "months": result_months,
        "summary": {
            "months_evaluated": len(evaluated),
            "months_met": months_met,
            "current_streak": current_streak,
            "historical_savings_rate_pct": historical_rate,
        },
    }


@app.get("/savings-projection")
def savings_projection(currency: str = "MXN") -> dict:
    """Linear multi-year projection for the Meta de ahorro panel — see
    compute_savings_projection's docstring (app/savings_goal.py) for the
    methodology and its caveats (no investment growth assumed, no
    inflation, grounded in the trailing 12 closed months' median income/
    gasto fijo rather than an all-time average).

    Net worth is currency-agnostic (no FX conversion — see /net-worth's
    own docstring), so this always projects against the MXN total
    regardless of `currency`, which only controls which currency's
    monthly income/gasto history is used as the projection's basis.
    """
    with get_session() as session:
        months = _compute_monthly_summary(session, currency)
    current_net_worth_mxn = net_worth()["total_by_currency"].get("MXN", 0.0)
    return compute_savings_projection(months, current_net_worth_mxn)


@app.get("/net-worth")
def net_worth() -> dict:
    """Best-effort current net worth, grouped by currency since no FX
    conversion is wired up (summing MXN and USD together would silently
    produce a wrong number).

    Known gaps, surfaced in `caveats` rather than hidden:
    - GBM: the Addenda block only contains daily interest income, never
      the invested principal (confirmed: GBM does issue a fuller statement
      under an older ZIP+PDF format with a real "Valor del Portafolio"
      figure, but the only contract using it in Drive, BF40HX01, is a
      separate, all-zero account — not AAU94801/AAU94802, which have no
      such statement). Where the user has provided the real aggregate
      balance manually (app/manual_data.py), that figure is used for the
      parent GBM account instead, and the contracts' own interest-only
      balances are excluded from the total to avoid double-counting —
      they're still listed in `by_account` for the transaction history,
      just flagged as folded into the parent.
    - Balagan: no statement ever reports a redeemable balance, and the
      balance is NOT investment + cumulative distributions — the monthly
      "Repartición por punto" is paid out to the user in cash each month,
      it doesn't stay in the account. Balance is just initial_investment
      ($75,000 MXN, Cláusula TERCERA — a constant, since no statement
      reports it). cash_distributed_to_date and average_monthly_return_pct
      in `detail` report the payouts as a return metric, not as balance.
    """
    with get_session() as session:
        accounts = session.query(Account).all()
        manual_override_parent_ids = {
            s.account_id
            for s in session.query(HoldingSnapshot).filter(HoldingSnapshot.source_file.is_(None))
        }

        totals_by_currency: dict[str, float] = defaultdict(float)
        breakdown = []
        caveats = []

        for acc in accounts:
            balance = 0.0
            currency = acc.currency
            detail: dict = {}

            txn_sum = (
                session.query(func.sum(Transaction.amount)).filter_by(account_id=acc.id).scalar()
                or 0.0
            )
            balance += txn_sum

            snapshots = session.query(HoldingSnapshot).filter_by(account_id=acc.id).all()
            latest_by_sub_portfolio: dict[str | None, HoldingSnapshot] = {}
            for s in snapshots:
                existing = latest_by_sub_portfolio.get(s.sub_portfolio)
                if existing is None or s.date > existing.date:
                    latest_by_sub_portfolio[s.sub_portfolio] = s
            if latest_by_sub_portfolio:
                balance += sum(s.market_value for s in latest_by_sub_portfolio.values())
                currency = next(iter(latest_by_sub_portfolio.values())).currency

            latest_equity = (
                session.query(EquityCompensationEntry)
                .filter_by(account_id=acc.id)
                .order_by(EquityCompensationEntry.date.desc())
                .first()
            )
            if latest_equity is not None and latest_equity.market_value is not None:
                balance += latest_equity.market_value
                currency = latest_equity.currency

            alt_sum = (
                session.query(func.sum(AlternativeInvestmentEntry.proportional_income))
                .filter_by(account_id=acc.id)
                .scalar()
            )
            if acc.name == "Balagan":
                # The monthly "Repartición por punto" is paid out to the user
                # in cash each month — it never sits inside Balagan, so it
                # must NOT be added to the recoverable balance (that was a
                # real bug: treating it as retained/compounding earnings
                # inflated the balance to $75,000 + cumulative distributions
                # instead of just the $75,000 principal). The distributions
                # are reported separately as a return metric instead.
                investment = balagan_parser.INITIAL_INVESTMENT_MXN
                balance += investment
                months = (
                    session.query(AlternativeInvestmentEntry).filter_by(account_id=acc.id).count()
                )
                cash_distributed = alt_sum or 0.0
                avg_monthly_return_pct = (
                    round((cash_distributed / months) / investment * 100, 3) if months else None
                )
                detail = {
                    "initial_investment": round(investment, 2),
                    "cash_distributed_to_date": round(cash_distributed, 2),
                    "average_monthly_return_pct": avg_monthly_return_pct,
                }
                caveats.append(
                    f"{acc.name}: balance is the fixed $75,000 investment (Cláusula TERCERA) "
                    "only — monthly 'Repartición por punto' is paid out in cash, not retained "
                    "in the account, so it is NOT added to the balance. cash_distributed_to_date "
                    "is the cumulative amount paid out; average_monthly_return_pct is that cash "
                    "divided by months of data, over the $75,000 principal"
                )
            elif alt_sum:
                balance += alt_sum
                caveats.append(
                    f"{acc.name}: balance is cumulative proportional income only "
                    "(no known initial capital contribution to add)"
                )

            excluded_from_total = False
            if acc.name.startswith("GBM ") and acc.parent_account_id in manual_override_parent_ids:
                excluded_from_total = True
                caveats.append(
                    f"{acc.name}: excluded from total — its accumulated interest is folded "
                    "into the parent GBM account's manually-provided balance"
                )
            elif acc.name.startswith("GBM") and txn_sum != 0 and not snapshots:
                caveats.append(
                    f"{acc.name}: balance is accumulated interest income only "
                    "(GBM's Addenda data doesn't include the invested principal, so this understates true value)"
                )
            if acc.id in manual_override_parent_ids:
                caveats.append(
                    f"{acc.name}: balance is a manually-provided figure, not from an "
                    "imported statement — see notes on its HoldingSnapshot"
                )

            if balance != 0:
                entry = {
                    "account": acc.name,
                    "kind": acc.kind.value,
                    "balance": round(balance, 2),
                    "currency": currency,
                }
                if detail:
                    entry["detail"] = detail
                if excluded_from_total:
                    entry["excluded_from_total"] = True
                breakdown.append(entry)
                if not excluded_from_total:
                    totals_by_currency[currency] += balance

        return {
            "total_by_currency": {c: round(v, 2) for c, v in totals_by_currency.items()},
            "by_account": breakdown,
            "caveats": caveats,
        }
