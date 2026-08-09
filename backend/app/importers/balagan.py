"""Import a Balagan monthly Estado de Resultados PDF into the database.

Usage:
    python -m app.importers.balagan "path/to/EDO RES JUNIO BALAGAN.pdf"

Dedup key is (account_id, period_start, period_end) — one entry per
calendar month, re-importing the same month's (possibly corrected) PDF
overwrites rather than duplicating.
"""

from __future__ import annotations

import os
import sys

from app.db import get_session, init_db
from app.models import Account, AlternativeInvestmentEntry
from app.parsers.balagan import parse_balagan_statement


def import_balagan_statement(pdf_path: str) -> bool:
    """Returns True if a new entry was inserted, False if it already existed."""
    init_db()
    stmt = parse_balagan_statement(pdf_path)

    with get_session() as session:
        account = session.query(Account).filter_by(name="Balagan").one()
        existing = (
            session.query(AlternativeInvestmentEntry)
            .filter_by(
                account_id=account.id,
                period_start=stmt.period_start,
                period_end=stmt.period_end,
            )
            .one_or_none()
        )
        if existing is not None:
            return False

        session.add(
            AlternativeInvestmentEntry(
                account_id=account.id,
                period_start=stmt.period_start,
                period_end=stmt.period_end,
                revenue=stmt.revenue,
                expenses=stmt.expenses,
                net_income=stmt.net_income,
                participation_pct=stmt.participation_pct,
                proportional_income=stmt.proportional_income,
                currency=account.currency,
                source_file=os.path.basename(pdf_path),
            )
        )
        session.commit()

    return True


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m app.importers.balagan path/to/statement.pdf", file=sys.stderr)
        sys.exit(1)
    inserted = import_balagan_statement(sys.argv[1])
    print("Inserted 1 new entry" if inserted else "Entry already existed, skipped")
