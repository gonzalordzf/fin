"""Import a Revolut MX credit card statement PDF into Transactions.

Usage:
    python -m app.importers.revolut path/to/statement.pdf

No reliable per-transaction reference number in this statement, so dedup
uses (source_file, source_row) like BBVA.
"""

from __future__ import annotations

import os
import sys

from app.db import get_session, init_db
from app.models import Account, Transaction
from app.parsers.revolut import parse_revolut_statement


def import_revolut_statement(pdf_path: str) -> int:
    init_db()
    txns = parse_revolut_statement(pdf_path)

    source_file = os.path.basename(pdf_path)
    inserted = 0
    with get_session() as session:
        account = session.query(Account).filter_by(name="Revolut").one()
        existing_rows = {
            t.source_row
            for t in session.query(Transaction).filter_by(
                account_id=account.id, source_file=source_file
            )
        }

        for i, txn in enumerate(txns):
            if i in existing_rows:
                continue
            session.add(
                Transaction(
                    account_id=account.id,
                    date=txn.operation_date,
                    amount=txn.amount,
                    currency=account.currency,
                    description=txn.description,
                    source_file=source_file,
                    source_row=i,
                    raw_description="\n".join(txn.raw_lines),
                )
            )
            inserted += 1
        session.commit()

    return inserted


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m app.importers.revolut path/to/statement.pdf", file=sys.stderr)
        sys.exit(1)
    count = import_revolut_statement(sys.argv[1])
    print(f"Inserted {count} new transactions")
