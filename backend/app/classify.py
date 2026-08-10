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
import unicodedata

from sqlalchemy.orm import Session

from app.models import Account, Category, CategoryKind, Transaction

TRANSFER_NAME_PATTERNS: list[str] = [
    r"ROFG950407NCA",
    r"ROFG950407",
    r"GONZALO\s+RODRIGUEZ\s+FIERRO",
    r"RODRIGUEZ\s+FIERRO\s+GONZALO",
]
"""User-provided (Gonzalo Rodríguez Fierro, RFC ROFG950407NCA) — the RFC
without homoclave is included too since some statements truncate it (same
convention already confirmed for BBVA/GBM document passwords). Name
patterns cover both name-order conventions banks use for SPEI beneficiary
fields. Matched accent-stripped and case-insensitive, never against the
destination bank."""

_TRANSFER_CATEGORY_NAME = "Transferencia entre Cuentas Propias"

_TRANSFER_INELIGIBLE_ACCOUNTS = {"AMEX"}
"""Accounts where matching the account holder's own name produces
guaranteed false positives, not a transfer signal. Found on real data:
AMEX's raw_description serializes the full CSV row, including a "Titular
de la Tarjeta" field that holds the cardholder's own name on EVERY single
transaction (it's card-ownership metadata, never a transfer recipient) —
this wrongly tagged 103 of 104 real purchases as self-transfers before
this exclusion existed. Bank/SPEI-style accounts (BBVA, Revolut, Bitso,
GBM) only include a name in their raw description when it's genuinely a
transfer party, so they don't need this guard."""


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


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

    query = (
        session.query(Transaction)
        .join(Account)
        .filter(
            Transaction.category_id.is_(None),
            Account.name.notin_(_TRANSFER_INELIGIBLE_ACCOUNTS),
        )
    )

    matched = 0
    for txn in query:
        # BBVA's `description` column is deliberately just the first line
        # (see parsers/bbva.py) — the SPEI beneficiary name lives on a
        # continuation line, preserved only in raw_description. Matching
        # description alone would never catch a single real BBVA transfer.
        haystack = f"{txn.raw_description or ''} {txn.description}"
        if pattern.search(_strip_accents(haystack)):
            txn.category_id = category.id
            matched += 1
    session.commit()
    return matched
