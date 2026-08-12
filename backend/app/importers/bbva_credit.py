"""Import a BBVA credit card statement PDF into the Transactions table —
either template, dispatched automatically by peeking at the statement's
own movements-table header (see `_detect_format`): the "nuevo estado de
cuenta universal" template (jul-2024 onward, `parsers/bbva_credit.py`) or
the older layout (ene-2023 through jun-2024, `parsers/bbva_credit_legacy.py`)
— same account, same underlying "Tarjeta ORO BBVA" product, same
Transaction/StatementSummary tables, just a different PDF template.

Usage:
    python -m app.importers.bbva_credit path/to/statement.pdf

Neither format is password-protected (unlike BBVA's checking/débito
statement — see importers/bbva.py). Both parsers already reject a
statement that doesn't reconcile against its own printed totals; this
importer adds the same balance-chain check the débito importer does:
statement N's previous_balance must equal the immediately preceding
statement's new_balance, or a statement is missing between them — except
for the one documented, permanent gap in _KNOWN_CHAIN_GAPS.
"""

from __future__ import annotations

import os
import sys

import pdfplumber

from app.db import get_session, init_db
from app.models import Account, StatementSummary, Transaction
from app.parsers.bbva_credit import BBVACreditStatement, parse_bbva_credit_statement
from app.parsers.bbva_credit_legacy import parse_bbva_credit_legacy_statement

# The Noviembre 2023 "Tarjeta Oro" statement is permanently missing — both
# the "Noviembre 2023" and "Diciembre 2023" files in the connected Drive
# folder are the exact same December PDF (md5-confirmed 2026-08-12); the
# real November statement was never uploaded and isn't recoverable from
# Drive. app/manual_data.py records the resulting net balance change
# (Octubre 2023's real closing balance vs. Diciembre 2023's real opening
# balance) as a documented adjustment transaction, so this one specific
# transition is allowed to skip the chain check rather than raising like
# every other broken chain would. Keyed by the two real balances either
# side of the gap so it can never silently swallow a *different* break.
_KNOWN_CHAIN_GAPS = {("BBVA TDC", 18985.52, 7038.29)}


def _detect_format(pdf_path: str) -> str:
    # The card PRODUCT is called "Tarjeta ORO BBVA" in both templates — that
    # text alone doesn't distinguish them (confirmed against a real jul-2024
    # statement). Each template's own movements-table header does, though:
    # modern prints "CARGOS,COMPRAS Y ABONOS REGULARES(NO A MESES)"; legacy
    # prints "Movimientos Efectuados Tarjeta Titular". Checked across every
    # page since either header can land past page 0 depending on account
    # activity that period.
    with pdfplumber.open(pdf_path) as pdf:
        full_text = "\n".join((page.extract_text() or "") for page in pdf.pages)
    has_modern_header = "CARGOS,COMPRAS" in full_text
    has_legacy_header = "Movimientos Efectuados" in full_text
    if has_modern_header and not has_legacy_header:
        return "platinum"
    if has_legacy_header and not has_modern_header:
        return "oro"
    raise ValueError(
        f"{pdf_path}: could not distinguish BBVA TDC statement format — modern header "
        f"present={has_modern_header}, legacy header present={has_legacy_header} (expected exactly one)"
    )


def import_bbva_credit_statement(pdf_path: str) -> int:
    init_db()
    fmt = _detect_format(pdf_path)
    stmt: BBVACreditStatement = (
        parse_bbva_credit_statement(pdf_path) if fmt == "platinum" else parse_bbva_credit_legacy_statement(pdf_path)
    )

    source_file = os.path.basename(pdf_path)
    inserted = 0
    with get_session() as session:
        account = session.query(Account).filter_by(name="BBVA TDC").one()

        if session.query(StatementSummary).filter_by(account_id=account.id, source_file=source_file).one_or_none() is None:
            preceding = (
                session.query(StatementSummary)
                .filter(StatementSummary.account_id == account.id, StatementSummary.period_end <= stmt.period_start)
                .order_by(StatementSummary.period_end.desc())
                .first()
            )
            is_known_gap = preceding is not None and (
                "BBVA TDC",
                round(preceding.saldo_final, 2),
                round(stmt.previous_balance, 2),
            ) in _KNOWN_CHAIN_GAPS
            if preceding is not None and abs(preceding.saldo_final - stmt.previous_balance) > 0.01 and not is_known_gap:
                raise ValueError(
                    "Balance chain broken: statement "
                    f"{preceding.period_end} saldo_final={preceding.saldo_final} does not match "
                    f"{source_file}'s previous_balance={stmt.previous_balance} — a statement is likely "
                    "missing between them."
                )
            session.add(
                StatementSummary(
                    account_id=account.id,
                    source_file=source_file,
                    period_start=stmt.period_start,
                    period_end=stmt.period_end,
                    saldo_anterior=stmt.previous_balance,
                    saldo_final=stmt.new_balance,
                )
            )

        existing_rows = {
            t.source_row
            for t in session.query(Transaction).filter_by(
                account_id=account.id, source_file=source_file
            )
        }

        for txn in stmt.transactions:
            if txn.row_index in existing_rows:
                continue
            session.add(
                Transaction(
                    account_id=account.id,
                    date=txn.operation_date,
                    amount=txn.amount,
                    currency=account.currency,
                    description=txn.description,
                    source_file=source_file,
                    source_row=txn.row_index,
                    raw_description="\n".join(txn.raw_lines),
                )
            )
            inserted += 1
        session.commit()

    return inserted


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m app.importers.bbva_credit path/to/statement.pdf", file=sys.stderr)
        sys.exit(1)
    count = import_bbva_credit_statement(sys.argv[1])
    print(f"Inserted {count} new transactions")
