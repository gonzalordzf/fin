"""Import an Allianz OptiMaxx statement PDF into HoldingSnapshot rows.

Usage:
    python -m app.importers.optimax path/to/Allianz_Optimax_2026-02.pdf

One snapshot per sub-portfolio (Bono de Fidelidad, Dinámico Dólares
Comprometido, Dinámico Dólares Inicial), dated at the period end. Dedup
key is (account_id, date, sub_portfolio).
"""

from __future__ import annotations

import os
import sys

from app.db import get_session, init_db
from app.models import Account, HoldingSnapshot
from app.parsers.optimax import parse_optimax_statement


def import_optimax_statement(pdf_path: str) -> int:
    init_db()
    stmt = parse_optimax_statement(pdf_path)

    source_file = os.path.basename(pdf_path)
    inserted = 0
    with get_session() as session:
        account = session.query(Account).filter_by(name="Optimax (Allianz)").one()
        existing = {
            (s.date, s.sub_portfolio)
            for s in session.query(HoldingSnapshot).filter_by(account_id=account.id)
        }

        for p in stmt.sub_portfolios:
            key = (stmt.period_end, p.name)
            if key in existing:
                continue
            session.add(
                HoldingSnapshot(
                    account_id=account.id,
                    date=stmt.period_end,
                    sub_portfolio=p.name,
                    market_value=p.market_value,
                    net_contribution_period=p.net_contribution_period,
                    currency=account.currency,
                    source_file=source_file,
                    notes=f"units={p.units}, unit_value={p.unit_value}",
                )
            )
            existing.add(key)
            inserted += 1
        session.commit()

    return inserted


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m app.importers.optimax path/to/statement.pdf", file=sys.stderr)
        sys.exit(1)
    count = import_optimax_statement(sys.argv[1])
    print(f"Inserted {count} new snapshots")
