"""Import a Shareworks quarterly statement PDF into EquityCompensationEntry.

Usage:
    python -m app.importers.shareworks "path/to/Quarterly Statement 06_30_2026.pdf"

One row per quarter, dated at the period's closing date. Dedup key is
(account_id, date).
"""

from __future__ import annotations

import os
import sys

from app.db import get_session, init_db
from app.models import Account, EquityCompensationEntry
from app.parsers.shareworks import parse_shareworks_statement


def import_shareworks_statement(pdf_path: str) -> bool:
    """Returns True if a new entry was inserted, False if it already existed."""
    init_db()
    stmt = parse_shareworks_statement(pdf_path)

    with get_session() as session:
        account = session.query(Account).filter_by(name="Shareworks").one()
        existing = (
            session.query(EquityCompensationEntry)
            .filter_by(account_id=account.id, date=stmt.period_end)
            .one_or_none()
        )
        if existing is not None:
            return False

        session.add(
            EquityCompensationEntry(
                account_id=account.id,
                date=stmt.period_end,
                own_contribution=stmt.own_contribution,
                company_match=stmt.company_match,
                market_value=stmt.market_value,
                vested_units=stmt.vested_units,
                currency=account.currency,
                source_file=os.path.basename(pdf_path),
                notes=(
                    f"period {stmt.period_start} to {stmt.period_end}; "
                    "company_match is estimated from vested-share value, "
                    "not a literal cash-match line (see parser docstring)"
                ),
            )
        )
        session.commit()

    return True


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(
            "Usage: python -m app.importers.shareworks path/to/statement.pdf", file=sys.stderr
        )
        sys.exit(1)
    inserted = import_shareworks_statement(sys.argv[1])
    print("Inserted 1 new entry" if inserted else "Entry already existed, skipped")
