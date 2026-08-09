"""Import a GBM monthly statement XML into the Transactions table.

Usage:
    python -m app.importers.gbm path/to/CB_20267_AAU94801.xml

Each movement is routed to its contract's sub-account ("GBM AAU94801" /
"GBM AAU94802", seeded as children of the GBM account). Folio is a
globally unique movement id, used directly for dedup.
"""

from __future__ import annotations

import os
import sys

from app.db import get_session, init_db
from app.models import Account, Transaction
from app.parsers.gbm import parse_gbm_statement


def import_gbm_statement(xml_path: str) -> int:
    init_db()
    movements = parse_gbm_statement(xml_path)

    source_file = os.path.basename(xml_path)
    inserted = 0
    with get_session() as session:
        accounts_by_contract = {
            a.notes.removeprefix("Contrato "): a
            for a in session.query(Account).filter(Account.name.like("GBM %"))
            if a.notes
        }
        existing_folios: dict[int, set[str]] = {}

        for mov in movements:
            account = accounts_by_contract.get(mov.contract)
            if account is None:
                raise ValueError(
                    f"Unknown GBM contract {mov.contract!r} — seed it as an account first"
                )
            if account.id not in existing_folios:
                existing_folios[account.id] = {
                    t.external_ref
                    for t in session.query(Transaction).filter_by(account_id=account.id)
                    if t.external_ref is not None
                }
            if mov.folio in existing_folios[account.id]:
                continue
            existing_folios[account.id].add(mov.folio)
            session.add(
                Transaction(
                    account_id=account.id,
                    date=mov.date,
                    amount=mov.amount,
                    currency=account.currency,
                    description=mov.description,
                    external_ref=mov.folio,
                    source_file=source_file,
                )
            )
            inserted += 1
        session.commit()

    return inserted


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m app.importers.gbm path/to/statement.xml", file=sys.stderr)
        sys.exit(1)
    count = import_gbm_statement(sys.argv[1])
    print(f"Inserted {count} new transactions")
