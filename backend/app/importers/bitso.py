"""Import a Bitso "Reporte de transacciones" CSV into the database.

Usage:
    python -m app.importers.bitso path/to/transaction-report...csv

Writes two things: one HoldingSnapshot per asset (end-of-period balance
and USD value), and one Transaction per movement (Rendimiento accruals),
recorded in the asset's own native currency/units since Bitso values them
at USD 0.
"""

from __future__ import annotations

import os
import sys

from app.db import get_session, init_db
from app.models import Account, HoldingSnapshot, Transaction
from app.parsers.bitso import parse_bitso_report


def import_bitso_report(csv_path: str) -> tuple[int, int]:
    init_db()
    snapshots, movements = parse_bitso_report(csv_path)

    source_file = os.path.basename(csv_path)
    snapshots_inserted = 0
    movements_inserted = 0
    with get_session() as session:
        account = session.query(Account).filter_by(name="Bitso").one()

        existing_snapshots = {
            (s.date, s.sub_portfolio)
            for s in session.query(HoldingSnapshot).filter_by(account_id=account.id)
        }
        for snap in snapshots:
            key = (snap.date, snap.asset)
            if key in existing_snapshots:
                continue
            session.add(
                HoldingSnapshot(
                    account_id=account.id,
                    date=snap.date,
                    sub_portfolio=snap.asset,
                    market_value=snap.market_value_usd,
                    currency="USD",
                    source_file=source_file,
                    notes=f"balance: {snap.balance}",
                )
            )
            existing_snapshots.add(key)
            snapshots_inserted += 1

        existing_rows = {
            t.source_row
            for t in session.query(Transaction).filter_by(
                account_id=account.id, source_file=source_file
            )
        }
        for i, mov in enumerate(movements):
            if i in existing_rows:
                continue
            session.add(
                Transaction(
                    account_id=account.id,
                    date=mov.date,
                    amount=mov.crypto_amount,
                    currency=mov.symbol,
                    description=f"{mov.operation}: {mov.asset}",
                    source_file=source_file,
                    source_row=i,
                )
            )
            movements_inserted += 1

        session.commit()

    return snapshots_inserted, movements_inserted


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m app.importers.bitso path/to/report.csv", file=sys.stderr)
        sys.exit(1)
    snaps, movs = import_bitso_report(sys.argv[1])
    print(f"Inserted {snaps} new snapshots, {movs} new transactions")
