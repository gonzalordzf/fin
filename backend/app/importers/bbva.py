"""Import a BBVA statement PDF into the Transactions table.

Usage:
    python -m app.importers.bbva path/to/statement.pdf

Reads the decryption password from the BBVA_STATEMENT_PASSWORD env var
(see .env.example) rather than hardcoding it, since it's derived from the
account holder's RFC.
"""

from __future__ import annotations

import os
import sys

from app.db import get_session, init_db
from app.models import Account, Transaction
from app.parsers.bbva import parse_bbva_statement


def import_bbva_statement(pdf_path: str, password: str | None = None) -> int:
    password = password or os.environ["BBVA_STATEMENT_PASSWORD"]
    init_db()
    txns = parse_bbva_statement(pdf_path, password)

    source_file = os.path.basename(pdf_path)
    inserted = 0
    with get_session() as session:
        account = session.query(Account).filter_by(name="BBVA").one()
        existing_rows = {
            t.source_row
            for t in session.query(Transaction).filter_by(
                account_id=account.id, source_file=source_file
            )
        }

        for txn in txns:
            if txn.row_index in existing_rows:
                continue
            session.add(
                Transaction(
                    account_id=account.id,
                    date=txn.operation_date,
                    amount=txn.amount,
                    currency=account.currency,
                    description=txn.description,
                    external_ref=txn.external_ref,
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
        print("Usage: python -m app.importers.bbva path/to/statement.pdf", file=sys.stderr)
        sys.exit(1)
    count = import_bbva_statement(sys.argv[1])
    print(f"Inserted {count} new transactions")
