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

from app.db import get_session, init_db
from app.importers import amex, balagan, bbva, bitso, gbm, optimax, revolut, shareworks
from app.parsers import balagan as balagan_parser
from app.models import (
    Account,
    AlternativeInvestmentEntry,
    Category,
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
IMPORTERS = {
    "BBVA": (bbva.import_bbva_statement, {".pdf"}),
    "AMEX": (amex.import_amex_csv, {".csv"}),
    "GBM": (gbm.import_gbm_statement, {".xml"}),
    "Bitso": (bitso.import_bitso_report, {".csv"}),
    "Revolut": (revolut.import_revolut_statement, {".pdf"}),
    "Balagan": (balagan.import_balagan_statement, {".pdf"}),
    "Optimax": (optimax.import_optimax_statement, {".pdf"}),
    "Shareworks": (shareworks.import_shareworks_statement, {".pdf"}),
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

    importer_fn, extensions = IMPORTERS[account]
    folder = DATA_IMPORTS_DIR / account
    if not folder.exists():
        raise HTTPException(404, f"No import folder at {folder}")

    details = []
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in extensions:
            continue
        try:
            result = importer_fn(str(path))
        except Exception as exc:  # noqa: BLE001 — surface per-file failures without aborting the batch
            details.append({"file": path.name, "error": str(exc)})
            continue
        details.append({"file": path.name, "result": result})

    return {"account": account, "files_seen": len(details), "details": details}


@app.get("/accounts")
def list_accounts() -> list[dict]:
    with get_session() as session:
        return [
            {
                "id": a.id,
                "name": a.name,
                "institution": a.institution,
                "kind": a.kind.value,
                "currency": a.currency,
                "parent": a.parent.name if a.parent else None,
            }
            for a in session.query(Account).order_by(Account.id).all()
        ]


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
    """Sums expense (negative-amount) transactions by category. No
    auto-categorization is wired up yet, so today every result will be
    'Sin categoría' — this reflects real project state, not a bug."""
    with get_session() as session:

        def date_filtered(q):
            if date_from:
                q = q.filter(Transaction.date >= date_from)
            if date_to:
                q = q.filter(Transaction.date <= date_to)
            return q

        categorized = date_filtered(
            session.query(Category.name, func.sum(Transaction.amount))
            .join(Transaction, Transaction.category_id == Category.id)
            .filter(Transaction.amount < 0)
        ).group_by(Category.name)

        uncategorized_total = date_filtered(
            session.query(func.sum(Transaction.amount)).filter(
                Transaction.category_id.is_(None), Transaction.amount < 0
            )
        ).scalar() or 0.0

        result = [{"category": name, "amount": round(-amt, 2)} for name, amt in categorized.all()]
        if uncategorized_total:
            result.append({"category": "Sin categoría", "amount": round(-uncategorized_total, 2)})

        return sorted(result, key=lambda r: -r["amount"])


@app.get("/net-worth")
def net_worth() -> dict:
    """Best-effort current net worth, grouped by currency since no FX
    conversion is wired up (summing MXN and USD together would silently
    produce a wrong number).

    Known gaps, surfaced in `caveats` rather than hidden:
    - GBM: the Addenda block only contains daily interest income, never
      the invested principal, so each contract's balance is accumulated
      interest only — it understates true value. Checked whether a fuller
      statement exists (GBM does have one, under an older ZIP+PDF format
      with a real "Valor del Portafolio" figure) but the only contract
      using that format in Drive (BF40HX01) is a separate, all-zero
      account — not AAU94801/AAU94802, which have no such statement.
    - Balagan: no statement ever reports a redeemable balance, so this
      uses cumulative proportional_income (profit share to date) plus the
      $75,000 MXN initial capital contribution (Cláusula TERCERA of the
      collaboration contract — not in any monthly Estado de Resultados,
      so it's a constant, not parsed).
    """
    with get_session() as session:
        accounts = session.query(Account).all()
        totals_by_currency: dict[str, float] = defaultdict(float)
        breakdown = []
        caveats = []

        for acc in accounts:
            balance = 0.0
            currency = acc.currency

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
            if alt_sum:
                balance += alt_sum
                if acc.name == "Balagan":
                    balance += balagan_parser.INITIAL_INVESTMENT_MXN
                    caveats.append(
                        f"{acc.name}: balance = "
                        f"${balagan_parser.INITIAL_INVESTMENT_MXN:,.0f} MXN initial "
                        "investment (Cláusula TERCERA of the contract) + cumulative proportional "
                        "income to date — not a redeemable balance from any statement"
                    )
                else:
                    caveats.append(
                        f"{acc.name}: balance is cumulative proportional income only "
                        "(no known initial capital contribution to add)"
                    )

            if acc.name.startswith("GBM") and txn_sum != 0 and not snapshots:
                caveats.append(
                    f"{acc.name}: balance is accumulated interest income only "
                    "(GBM's Addenda data doesn't include the invested principal, so this understates true value)"
                )

            if balance != 0:
                breakdown.append(
                    {
                        "account": acc.name,
                        "kind": acc.kind.value,
                        "balance": round(balance, 2),
                        "currency": currency,
                    }
                )
                totals_by_currency[currency] += balance

        return {
            "total_by_currency": {c: round(v, 2) for c, v in totals_by_currency.items()},
            "by_account": breakdown,
            "caveats": caveats,
        }
