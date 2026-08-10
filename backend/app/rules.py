"""Auto-categorization: ordered regex rules matched against a transaction's
raw_description + description. First match wins. Anything left unmatched
stays uncategorized — /spending-by-category already reports that under
"Sin categoría", and force-assigning a literal "Otros" row would hide the
signal of what still needs a rule.

Seeded and validated against this user's real statements (BBVA Jul/Aug/Dec
2025-Jan/Aug 2026, one live AMEX CSV export) — not guessed from the shared
kit's generic examples. Patterns marked "not yet seen in real data" are
best-effort based on institution names and should be confirmed once a real
matching transaction shows up.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models import Category, Transaction

SELF_PAYMENT_RULES: list[tuple[str, str]] = [
    # Paying off your own credit card moves money between your own
    # accounts, it isn't spending — validated on both sides of the same
    # real event: BBVA's "AMERICAN EXPRESS 01429 -309,542.23" line and
    # AMEX's own "GRACIAS POR SU PAGO CON CARGO A BBVA" line.
    (r"^PAGO TARJETA DE CREDITO", "Pago de Tarjeta de Crédito"),
    (r"^AMERICAN EXPRESS \d+", "Pago de Tarjeta de Crédito"),
    (r"GRACIAS POR SU PAGO", "Pago de Tarjeta de Crédito"),
    # A credit for a disputed/unrecognized charge is a refund, not a
    # transfer — validated: real AMEX line "CREDITO POR CARGO NO
    # RECONOCIDO".
    (r"CREDITO POR CARGO NO RECONOCIDO", "Reembolsos"),
]

INVESTMENT_INSTITUTION_PATTERNS: list[str] = [
    r"\bSURA\b",  # validated: real BBVA SPEI RECIBIDO from "SURA INVESTMENT MANAGEMENT MEXICO"
    r"\bGBM\b",  # not yet seen in a real contribution transaction — best effort
    r"\bBITSO\b",  # not yet seen in a real contribution transaction — best effort
    r"\bALLIANZ\b",  # not yet seen in a real contribution transaction — best effort
    r"\bOPTIMAX\b",  # not yet seen in a real contribution transaction — best effort
]
_INVESTMENT_CATEGORY = "Inversión"

INCOME_RULES: list[tuple[str, str]] = [
    (r"\bNOMINA\b", "Nómina"),  # validated: real BBVA payroll deposits
]

MERCHANT_RULES: list[tuple[str, str]] = [
    # Transporte
    (r"\bUBER TRIP\b", "Transporte"),
    (r"^PASE ", "Transporte"),  # toll-road tag charges
    (r"MUEVE CIUDAD", "Transporte"),
    (r"^RETIRO SIN TARJETA", "Efectivo (ATM)"),
    # Delivery — kept separate from restaurants, per docs/02-categorias.md:
    # it's cut back differently in a real budget squeeze.
    (r"UBER EATS", "Delivery"),
    (r"RAPPI\*RAPPI", "Delivery"),
    (r"RAPPI\*PRIME", "Delivery"),
    # Alimentos y Supermercado
    (r"SUPERAMA", "Alimentos y Supermercado"),
    (r"\bOXXO\b", "Alimentos y Supermercado"),
    # Restaurantes y Café — generic markers plus specific merchants seen in
    # this user's real statements
    (r"\bREST(AURANTE)?\b", "Restaurantes y Café"),
    (r"\bCAFE\b", "Restaurantes y Café"),
    (r"\bBAR\s", "Restaurantes y Café"),
    (r"MERCADOPAGO\*(PIOLA|MORAMORA)\b", "Restaurantes y Café"),
    (r"BZPAY\*RESTAURANTE", "Restaurantes y Café"),
    (r"NETPAY\*ROKAI", "Restaurantes y Café"),
    (r"SR PAGO\*REST", "Restaurantes y Café"),
    (r"TACOS ATARANTADOS", "Restaurantes y Café"),
    (r"^TORINO\b", "Restaurantes y Café"),
    (r"CALIFA (MAZARYK|PALMAS)", "Restaurantes y Café"),
    (r"MI COMPA CHAVA", "Restaurantes y Café"),
    (r"CENTRO LIBANES", "Restaurantes y Café"),
    (r"COM RAP MAIZAJO", "Restaurantes y Café"),
    (r"CHIQUITO CAFE", "Restaurantes y Café"),
    (r"EL MANDARINO", "Restaurantes y Café"),
    (r"LA PESCADERIA", "Restaurantes y Café"),
    # Servicios y Suscripciones
    (r"NETFLIX", "Servicios y Suscripciones"),
    (r"APPLE\.COM", "Servicios y Suscripciones"),
    (r"AMAZON PRIME", "Servicios y Suscripciones"),
    (r"OURARING", "Servicios y Suscripciones"),
    (r"TELEFONOS DE MEXICO", "Servicios y Suscripciones"),
    (r"CONEKTA\*TOTALPASS", "Cuidado Personal"),
    # Entretenimiento
    (r"PLAYSTATION NETWORK", "Entretenimiento"),
    # Compras
    (r"AMAZON MX", "Compras"),
    (r"LIVERPOOL", "Compras"),
    (r"FANTASIAS", "Compras"),
    # Viajes
    (r"AEROMEXICO", "Viajes"),
    (r"AIR FRANCE", "Viajes"),
    (r"HTL\*", "Viajes"),
    # Vivienda
    (r"ROTOPLAS", "Vivienda"),
]


def _first_match(text: str, rules: list[tuple[str, str]]) -> str | None:
    for pattern, category_name in rules:
        if re.search(pattern, text, re.IGNORECASE):
            return category_name
    return None


def classify_merchants(session: Session) -> int:
    """Applies SELF_PAYMENT, investment-institution, income, and merchant
    rules, in that order, to every still-uncategorized transaction.
    Idempotent — only touches category_id is None, so it never overwrites a
    category classify_transfers (or a human) already assigned."""
    categories = {c.name: c for c in session.query(Category).all()}

    matched = 0
    for txn in session.query(Transaction).filter(Transaction.category_id.is_(None)):
        haystack = f"{txn.raw_description or ''} {txn.description}"

        category_name = _first_match(haystack, SELF_PAYMENT_RULES)
        if category_name is None and any(
            re.search(p, haystack, re.IGNORECASE) for p in INVESTMENT_INSTITUTION_PATTERNS
        ):
            category_name = _INVESTMENT_CATEGORY
        if category_name is None:
            category_name = _first_match(haystack, INCOME_RULES)
        if category_name is None:
            category_name = _first_match(haystack, MERCHANT_RULES)

        if category_name is None:
            continue
        category = categories.get(category_name)
        if category is None:
            continue
        txn.category_id = category.id
        matched += 1

    session.commit()
    return matched
