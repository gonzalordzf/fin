"""Import a BBVA credit card ("TARJETA PLATINUM BBVA") statement PDF into
the Transactions table.

Usage:
    python -m app.importers.bbva_credit path/to/statement.pdf

Not password-protected (unlike BBVA's checking/débito statement — see
importers/bbva.py). parse_bbva_credit_statement() already rejects a
statement that doesn't reconcile against its own printed totals; this
importer adds the same balance-chain check the débito importer does:
statement N's previous_balance must equal the immediately preceding
statement's new_balance, or a statement is missing between them.
"""

from __future__ import annotations

import os
import sys

from app.db import get_session, init_db
from app.models import Account, StatementSummary, Transaction
from app.parsers.bbva_credit import parse_bbva_credit_statement


def import_bbva_credit_statement(pdf_path: str) -> int:
    init_db()
    stmt = parse_bbva_credit_statement(pdf_path)

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
            if preceding is not None and abs(preceding.saldo_final - stmt.previous_balance) > 0.01:
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
