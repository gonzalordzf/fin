"""Import a BBVA statement PDF into the Transactions table.

Usage:
    python -m app.importers.bbva path/to/statement.pdf

Reads the decryption password from the BBVA_STATEMENT_PASSWORD env var
(see .env.example) rather than hardcoding it, since it's derived from the
account holder's RFC.

parse_bbva_statement() already rejects a statement that doesn't reconcile
against its own printed totals. This importer adds the other half of
docs/04-gotchas.md's "cadena de saldos": statement N's saldo_anterior must
equal the immediately preceding statement's saldo_final, or a statement is
missing between them — checked against StatementSummary rows left by
earlier imports, before any transaction from the new file is written.

Verified against real statements: importing Jul then Aug 2026 (consecutive)
reconciles cleanly; the check only looks backward for a statement ending
before the new one starts, so it assumes the normal chronological "corte"
workflow (newest statement imported each month) — it does NOT catch a gap
if an older statement is backfilled after newer ones are already loaded.
"""

from __future__ import annotations

import os
import sys

from app.db import get_session, init_db
from app.models import Account, StatementSummary, Transaction
from app.parsers.bbva import parse_bbva_statement


def import_bbva_statement(pdf_path: str, password: str | None = None) -> int:
    password = password or os.environ["BBVA_STATEMENT_PASSWORD"]
    init_db()
    stmt = parse_bbva_statement(pdf_path, password)

    source_file = os.path.basename(pdf_path)
    inserted = 0
    with get_session() as session:
        account = session.query(Account).filter_by(name="BBVA").one()

        if session.query(StatementSummary).filter_by(account_id=account.id, source_file=source_file).one_or_none() is None:
            preceding = (
                session.query(StatementSummary)
                .filter(StatementSummary.account_id == account.id, StatementSummary.period_end <= stmt.period_start)
                .order_by(StatementSummary.period_end.desc())
                .first()
            )
            if preceding is not None and abs(preceding.saldo_final - stmt.saldo_anterior) > 0.01:
                raise ValueError(
                    "Balance chain broken: statement "
                    f"{preceding.period_end} saldo_final={preceding.saldo_final} does not match "
                    f"{source_file}'s saldo_anterior={stmt.saldo_anterior} — a statement is likely missing "
                    "between them."
                )
            session.add(
                StatementSummary(
                    account_id=account.id,
                    source_file=source_file,
                    period_start=stmt.period_start,
                    period_end=stmt.period_end,
                    saldo_anterior=stmt.saldo_anterior,
                    saldo_final=stmt.saldo_final,
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
