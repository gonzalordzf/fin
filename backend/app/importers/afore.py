"""Import an AFORE SURA statement PDF into HoldingSnapshot rows.

Usage:
    python -m app.importers.afore path/to/detalleMovimientos.pdf

One snapshot per subcuenta (Retiro, Vivienda, Voluntario, Saldo en
tránsito), dated at the statement's own emission date/time — not the
filename, which SURA's export doesn't date-stamp. Dedup key is
(account_id, date, sub_portfolio), same as Optimax.
"""

from __future__ import annotations

import os
import sys

from app.db import get_session, init_db
from app.models import Account, HoldingSnapshot
from app.parsers.afore import parse_afore_statement


def import_afore_statement(pdf_path: str) -> int:
    init_db()
    snapshot = parse_afore_statement(pdf_path)

    source_file = os.path.basename(pdf_path)
    inserted = 0
    with get_session() as session:
        account = session.query(Account).filter_by(name="AFORE (Sura)").one()
        existing = {
            (s.date, s.sub_portfolio)
            for s in session.query(HoldingSnapshot).filter_by(account_id=account.id)
        }

        for sub_portfolio, market_value in snapshot.subcuentas.items():
            key = (snapshot.fecha_emision, sub_portfolio)
            if key in existing:
                continue
            session.add(
                HoldingSnapshot(
                    account_id=account.id,
                    date=snapshot.fecha_emision,
                    sub_portfolio=sub_portfolio,
                    market_value=market_value,
                    currency=account.currency,
                    source_file=source_file,
                )
            )
            existing.add(key)
            inserted += 1
        session.commit()

    return inserted


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m app.importers.afore path/to/detalleMovimientos.pdf", file=sys.stderr)
        sys.exit(1)
    count = import_afore_statement(sys.argv[1])
    print(f"Inserted {count} new snapshots")
