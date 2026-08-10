"""Transfer detection: a movement between the user's own accounts is not
income or an expense.

The recipient must be identified by TITULAR (the user's own name or RFC as
the transfer's destinatario), never by destination bank. "SPEI ENVIADO A
BBVA" can be rent to a landlord who happens to bank at BBVA — matching on
bank name silently misclassifies real spending as a self-transfer. This is
the single most expensive classification mistake seen in practice (a
bank-based rule erased 7 months of real rent from a ledger), per
docs/04-gotchas.md from the shared método kit.

TRANSFER_NAME_PATTERNS is empty until the user's real name/RFC (as they
appear in SPEI and transfer descriptions across BBVA/Revolut/Bitso/GBM) are
known — never guess or infer them.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models import Category, CategoryKind, Transaction

TRANSFER_NAME_PATTERNS: list[str] = []
"""Regexes (case/accent-insensitive) matched against Transaction.description.
Each should identify the transfer's recipient as the account holder — their
full name or RFC — not the destination bank or institution name."""

_TRANSFER_CATEGORY_NAME = "Transferencia entre Cuentas Propias"


def classify_transfers(session: Session) -> int:
    """Assigns the self-transfer category to any uncategorized transaction
    whose description matches TRANSFER_NAME_PATTERNS. Idempotent: only
    touches transactions with category_id is None, so re-running never
    overwrites a manual or rule-based categorization made elsewhere."""
    if not TRANSFER_NAME_PATTERNS:
        return 0

    category = (
        session.query(Category)
        .filter_by(name=_TRANSFER_CATEGORY_NAME, kind=CategoryKind.TRANSFER)
        .one()
    )
    pattern = re.compile("|".join(TRANSFER_NAME_PATTERNS), re.IGNORECASE)

    matched = 0
    for txn in session.query(Transaction).filter(Transaction.category_id.is_(None)):
        if pattern.search(txn.description):
            txn.category_id = category.id
            matched += 1
    session.commit()
    return matched
