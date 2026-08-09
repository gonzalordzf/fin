"""Import an AMEX activity CSV export into the Transactions table.

Usage:
    python -m app.importers.amex path/to/activity.csv

Unlike BBVA, AMEX's own Referencia is reliably unique per transaction, so
dedup keys off (account_id, external_ref) directly.
"""

from __future__ import annotations

import os
import sys

from app.db import get_session, init_db
from app.models import Account, Transaction
from app.parsers.amex import parse_amex_csv


def import_amex_csv(csv_path: str) -> int:
    init_db()
    txns = parse_amex_csv(csv_path)

    source_file = os.path.basename(csv_path)
    inserted = 0
    with get_session() as session:
        account = session.query(Account).filter_by(name="AMEX").one()
        existing = list(session.query(Transaction).filter_by(account_id=account.id))
        existing_refs = {t.external_ref for t in existing if t.external_ref is not None}
        existing_rows = {
            (t.source_file, t.source_row) for t in existing if t.external_ref is None
        }

        for i, txn in enumerate(txns):
            if txn.external_ref is not None:
                if txn.external_ref in existing_refs:
                    continue
            elif (source_file, i) in existing_rows:
                continue
            session.add(
                Transaction(
                    account_id=account.id,
                    date=txn.purchase_date,
                    amount=txn.amount,
                    currency=account.currency,
                    description=txn.description,
                    external_ref=txn.external_ref,
                    source_file=source_file,
                    source_row=i,
                    raw_description=str(txn.raw_row),
                )
            )
            if txn.external_ref is not None:
                existing_refs.add(txn.external_ref)
            inserted += 1
        session.commit()

    return inserted


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m app.importers.amex path/to/activity.csv", file=sys.stderr)
        sys.exit(1)
    count = import_amex_csv(sys.argv[1])
    print(f"Inserted {count} new transactions")
