"""Import AMEX activity into the Transactions table, from either format
AMEX itself provides:

- CSV export (import_amex_csv): covers roughly the last 2 years, has a
  reliable native "Referencia" per row.
- PDF "Estado de Cuenta" (import_amex_pdf_statement): the only format AMEX's
  own export tool produces for anything older than that — see
  app/parsers/amex_pdf.py. Same "AMEX" Account, just a second on-ramp for
  history the CSV export doesn't reach.

Both dedupe primarily by (account_id, external_ref) — unlike BBVA, AMEX's
own reference is reliably unique per transaction within either format on its
own. But the two formats' references are NOT the same value or scheme for
the same real transaction (confirmed against real Aug-2024-era data: CSV's
"Referencia" is a quoted 'AT26...' banking-network reference; the PDF's
"/REF..." is a merchant/processor auth code) — so at the boundary where a
transaction could plausibly appear in both a CSV and a PDF, ref-matching
alone can't catch the duplicate. _cross_format_duplicate() adds a narrow
(date, amount) fallback that only fires when an existing transaction for
that same date+amount came from the *other* file format, so it never
suppresses a same-day-same-amount coincidence within a single format (e.g.
two real same-amount MERCADOPAGO charges on different days are unaffected;
only a literal cross-format re-appearance is skipped).
"""

from __future__ import annotations

import os
import sys

from app.db import get_session, init_db
from app.models import Account, Transaction
from app.parsers.amex import parse_amex_csv
from app.parsers.amex_pdf import parse_amex_pdf_statement


def _dedup_state(session, account_id: int) -> tuple[set[str], set[tuple], dict[tuple, set[str]]]:
    """Returns (existing_refs, existing_rows, date_amount_to_extensions) for
    every transaction already in this account, across both formats."""
    existing = list(session.query(Transaction).filter_by(account_id=account_id))
    existing_refs = {t.external_ref for t in existing if t.external_ref is not None}
    existing_rows = {
        (t.source_file, t.source_row) for t in existing if t.external_ref is None
    }
    date_amount_to_exts: dict[tuple, set[str]] = {}
    for t in existing:
        if t.source_file is None:
            continue
        ext = os.path.splitext(t.source_file)[1].lower()
        key = (t.date, round(t.amount, 2))
        date_amount_to_exts.setdefault(key, set()).add(ext)
    return existing_refs, existing_rows, date_amount_to_exts


def _cross_format_duplicate(
    date_amount_to_exts: dict[tuple, set[str]], date, amount: float, this_ext: str
) -> bool:
    exts = date_amount_to_exts.get((date, round(amount, 2)))
    return bool(exts and (exts - {this_ext}))


def import_amex_csv(csv_path: str) -> int:
    init_db()
    txns = parse_amex_csv(csv_path)

    source_file = os.path.basename(csv_path)
    this_ext = os.path.splitext(source_file)[1].lower()
    inserted = 0
    with get_session() as session:
        account = session.query(Account).filter_by(name="AMEX").one()
        existing_refs, existing_rows, date_amount_to_exts = _dedup_state(session, account.id)

        for i, txn in enumerate(txns):
            if txn.external_ref is not None:
                if txn.external_ref in existing_refs:
                    continue
            elif (source_file, i) in existing_rows:
                continue
            if _cross_format_duplicate(date_amount_to_exts, txn.purchase_date, txn.amount, this_ext):
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
            date_amount_to_exts.setdefault((txn.purchase_date, round(txn.amount, 2)), set()).add(this_ext)
            inserted += 1
        session.commit()

    return inserted


def import_amex_pdf_statement(pdf_path: str) -> int:
    init_db()
    stmt = parse_amex_pdf_statement(pdf_path)

    source_file = os.path.basename(pdf_path)
    this_ext = os.path.splitext(source_file)[1].lower()
    inserted = 0
    with get_session() as session:
        account = session.query(Account).filter_by(name="AMEX").one()
        existing_refs, existing_rows, date_amount_to_exts = _dedup_state(session, account.id)

        for txn in stmt.transactions:
            if txn.external_ref is not None:
                if txn.external_ref in existing_refs:
                    continue
            elif (source_file, txn.row_index) in existing_rows:
                continue
            if _cross_format_duplicate(date_amount_to_exts, txn.date, txn.amount, this_ext):
                continue
            session.add(
                Transaction(
                    account_id=account.id,
                    date=txn.date,
                    amount=txn.amount,
                    currency=account.currency,
                    description=txn.description,
                    external_ref=txn.external_ref,
                    source_file=source_file,
                    source_row=txn.row_index,
                    raw_description="\n".join(txn.raw_lines),
                )
            )
            if txn.external_ref is not None:
                existing_refs.add(txn.external_ref)
            date_amount_to_exts.setdefault((txn.date, round(txn.amount, 2)), set()).add(this_ext)
            inserted += 1
        session.commit()

    return inserted


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(
            "Usage: python -m app.importers.amex path/to/activity.csv  (or ...statement.pdf)",
            file=sys.stderr,
        )
        sys.exit(1)
    path = sys.argv[1]
    if path.lower().endswith(".pdf"):
        count = import_amex_pdf_statement(path)
    else:
        count = import_amex_csv(path)
    print(f"Inserted {count} new transactions")
