"""Auto-categorization: ordered regex rules matched against a transaction's
raw_description + description. First match wins. Anything left unmatched
stays uncategorized — /spending-by-category already reports that under
"Sin categoría", and force-assigning a literal "Otros" row would hide the
signal of what still needs a rule.

Seeded and validated against this user's real statements — not guessed from
the shared kit's generic examples. Patterns marked "not yet seen in real
data" are best-effort based on institution names and should be confirmed
once a real matching transaction shows up.

Re-validated against the full real BBVA history (18 statements, Dec 2024-
Aug 2026) plus AMEX/Balagan/Optimax/Shareworks/Bitso/AFORE: this surfaced
that every leading-anchor (`^`) and leading-\b pattern here was silently
matching zero real transactions, because the haystack is raw_description +
description and raw_description never starts with the merchant text
(BBVA's own date prefix, AMEX's dict-repr CSV row) nor has a clean word
boundary before words BBVA's PDF extraction concatenates without a space
(e.g. "0022386NOMINA", "RECIBIDOGBM"). Fixed by dropping the anchors/
boundaries that don't survive that concatenation — see inline comments on
SELF_PAYMENT_RULES, INVESTMENT_INSTITUTION_PATTERNS, INCOME_RULES, and the
Transporte/Efectivo entries in MERCHANT_RULES.
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
    #
    # No leading ^ anchor: haystack is raw_description + description, and
    # raw_description always starts with something else first (BBVA's own
    # date prefix, AMEX's dict-repr CSV row), so an anchored pattern here
    # never matches anything — confirmed against the full 18-month BBVA
    # history, where these two patterns matched zero of 41 real occurrences
    # until the anchor was dropped.
    (r"PAGO TARJETA DE CREDITO", "Pago de Tarjeta de Crédito"),
    (r"AMERICAN EXPRESS \d+", "Pago de Tarjeta de Crédito"),
    (r"GRACIAS POR SU PAGO", "Pago de Tarjeta de Crédito"),
    # A credit for a disputed/unrecognized charge is a refund, not a
    # transfer — validated: real AMEX line "CREDITO POR CARGO NO
    # RECONOCIDO".
    (r"CREDITO POR CARGO NO RECONOCIDO", "Reembolsos"),
    # Order matters and is load-bearing: "COMISION POR PAGO DEVUELTO"
    # contains "PAGO DEVUELTO", and SELF_PAYMENT_RULES is evaluated before
    # MERCHANT_RULES in full, so the commission has to be caught here — not
    # in MERCHANT_RULES — or the broader rule below would swallow it.
    (r"COMISION POR PAGO DEVUELTO", "Impuestos y Comisiones Bancarias"),
    # A bounced card payment reversing back onto the balance: not spending,
    # it's the undo of a card payment, so it belongs with them and nets out.
    (r"PAGO DEVUELTO", "Pago de Tarjeta de Crédito"),
]

INVESTMENT_INSTITUTION_PATTERNS: list[str] = [
    # No leading \b: BBVA's own PDF text extraction concatenates "SPEI
    # RECIBIDOGBM" as one word with no space before the institution name
    # (confirmed on 4 real GBM withdrawal transactions — \bGBM\b matched 0
    # of them since there's no word boundary between "O" and "G").
    r"GBM\b",
    r"BITSO\b",  # not yet seen in a real contribution transaction — best effort
    r"ALLIANZ\b",  # not yet seen in a real contribution transaction — best effort
    r"OPTIMAX\b",  # not yet seen in a real contribution transaction — best effort
]
_INVESTMENT_CATEGORY = "Inversión"

INCOME_RULES: list[tuple[str, str]] = [
    # No leading \b, same concatenation issue as GBM above: BBVA renders
    # payroll SPEI as e.g. "0022386NOMINA" with no space — confirmed on 47
    # of 47 real payroll deposits, all invisible to \bNOMINA\b.
    (r"NOMINA\b", "Nómina"),
    # NOT the AFORE SURA retirement account (that's a separate account,
    # imported on its own from AFORE's own PDF, never touches BBVA) — the
    # user confirmed these "SURA INVESTMENT MANAGEMENT MEXICO" SPEI
    # deposits into BBVA (recurring ~twice a year) are payouts from an
    # employer caja de ahorro/savings fund, not an investment contribution.
    # Previously miscategorized as "Inversión" before this was confirmed.
    (r"\bSURA\b", "Fondo de Ahorro"),
]

# Recurring transfers to named individuals — not merchants, but confirmed
# by the user as fixed, recognizable real expenses that BBVA's generic
# "PAGO CUENTA DE TERCERO"/"SPEI ENVIADO" descriptions don't otherwise
# distinguish from one-off personal transfers.
KNOWN_PERSON_RULES: list[tuple[str, str]] = [
    (r"REMIS", "Salud"),  # Begoña Remis — psicóloga, recurring
    (r"BATIZ", "Vivienda"),  # Ceci Batiz — renta del depto, recurring
    (r"CASTILLO MEADE", "Regalos"),  # Maria Luis Castillo Meade — regalo de boda, one-off
    # Full name, not just "RAMOS": a "Daniel Ramos" appears unrelated in
    # the World Cup ticket reimbursement thread — matching on the surname
    # alone would collide with that real person.
    (r"CLARA RAMOS", "Vivienda"),  # servicio de limpieza doméstica, recurring
]

# World Cup 2026: ticket purchases (mostly from Federación Mexicana de
# Fútbol) and reimbursements from the friend group that paid Gonzalo back.
# One category, kind=EXPENSE, covers both directions on purpose — the
# outflow (buying tickets) and the inflow (getting reimbursed) are tagged
# the same, so /spending-by-category's amount<0 filter naturally nets out
# to only the real personal cost (confirmed against real data: of
# $698,370 paid to the Federación across 3 statements, $672,386 came back
# from named reimbursers, leaving ~$25,984 that was genuinely Gonzalo's
# own tickets — that residual is what should show as "gasto", not the
# gross $698,370). Matched only on unambiguous markers confirmed against
# real memos; vaguer ones ("gonzalo", "Y SI Si") are left uncategorized
# rather than guessed.
WORLD_CUP_PATTERNS: list[str] = [
    r"MUNDIAL", r"\bFIFA\b", r"\bFWC\b", r"FEDERACION MEXICANA DE FUTBOL",
    # Ticket broker (Calabasas, CA) the user confirmed as the semifinal
    # tickets bought on AMEX and reimbursed by SPEI — the two real charges
    # ($209,207 / $57,150) are World Cup, not generic entertainment.
    r"SPOTLIGHT TICKET",
]
_WORLD_CUP_CATEGORY = "Mundial"

MERCHANT_RULES: list[tuple[str, str]] = [
    # Transporte
    (r"\bUBER TRIP\b", "Transporte"),
    # No leading ^: same anchor-vs-haystack-prefix issue as SELF_PAYMENT_RULES
    # above — confirmed 0/24 real AMEX toll charges matched with the anchor.
    (r"PASE ", "Transporte"),  # toll-road tag charges
    (r"MUEVE CIUDAD", "Transporte"),
    (r"RETIRO SIN TARJETA", "Efectivo (ATM)"),
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
    (r"GYMPASS", "Cuidado Personal"),
    # Same vendor, three different descriptors across the years (CONEKTA
    # gateway, the tilde variant, and their own SAPI entity) — all one gym
    # membership, confirmed by the matching amounts and cadence.
    (r"TOTAL\s*[~*]?\s*PASS", "Cuidado Personal"),
    # Entretenimiento
    (r"PLAYSTATION NETWORK", "Entretenimiento"),
    (r"TICKETMASTER", "Entretenimiento"),
    (r"CINEPOLIS|CINEMEX", "Entretenimiento"),
    # Matched on the ASCII-safe fragment: AMEX's own export mojibakes this
    # merchant ("CONECTAPP*PÃ DELRANGERS"), so the accented name can't be
    # relied on. Padel court bookings.
    (r"DELRANGERS", "Entretenimiento"),
    # Compras
    (r"AMAZON MX", "Compras"),
    (r"LIVERPOOL", "Compras"),
    (r"FANTASIAS", "Compras"),
    (r"APPLE (STORE|MEXICO)", "Compras"),
    (r"UNIQLO", "Compras"),
    (r"ADIDAS", "Compras"),
    (r"SWATCH", "Compras"),
    (r"HELLY HANSEN", "Compras"),
    (r"\bREI #", "Compras"),
    (r"MOBLUM", "Compras"),  # muebles
    (r"EMMASLEEP", "Compras"),  # colchón
    # Viajes
    (r"AEROMEXICO", "Viajes"),
    (r"AIR FRANCE", "Viajes"),
    (r"HTL\*", "Viajes"),
    (r"AIRBNB", "Viajes"),
    (r"EXPEDIA", "Viajes"),
    (r"CELEBRITY CRUISES", "Viajes"),
    (r"COPA AIRLINES", "Viajes"),
    (r"VIVAAEROBUS", "Viajes"),
    (r"Concesionaria Vuela", "Viajes"),  # Volaris
    (r"PALACE RESORTS", "Viajes"),
    (r"\bHERTZ\b", "Viajes"),
    (r"VAIL SKI PASS|VAL THORENS", "Viajes"),
    # Restaurantes (bloque tardío: comercios confirmados al ampliar el
    # historial de AMEX a 2023-2026)
    (r"AMIGAS CONDESA", "Restaurantes y Café"),
    # El restaurante en el que el usuario es inversionista — consumo propio
    # en el local, no tiene relación con la aportación de capital (esa vive
    # en AlternativeInvestmentEntry, no en Transaction, así que no colisiona).
    (r"BALAGAN", "Restaurantes y Café"),
    (r"PAVIRO", "Restaurantes y Café"),
    # Impuestos y Comisiones Bancarias — cuotas y cargos de la propia AMEX
    (r"CUOTA ANUAL", "Impuestos y Comisiones Bancarias"),
    (r"IVA APLICABLE", "Impuestos y Comisiones Bancarias"),
    (r"AJUSTE DE DEBITO", "Impuestos y Comisiones Bancarias"),
    (r"\*AMEX INTERNET", "Impuestos y Comisiones Bancarias"),
    (r"RET CAJ OTRO BCO|RETIRO CAJERO AUTOMATICO", "Efectivo (ATM)"),
    # Transporte
    (r"\bDIDI\b", "Transporte"),
    # Aseguradoras de auto (Qualitas, ANA) — Transporte, no Vivienda: son
    # pólizas vehiculares, confirmado por el nombre de la aseguradora.
    (r"QUALITAS|ANA COMPA", "Transporte"),
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
            re.search(p, haystack, re.IGNORECASE) for p in WORLD_CUP_PATTERNS
        ):
            category_name = _WORLD_CUP_CATEGORY
        if category_name is None and any(
            re.search(p, haystack, re.IGNORECASE) for p in INVESTMENT_INSTITUTION_PATTERNS
        ):
            category_name = _INVESTMENT_CATEGORY
        if category_name is None:
            category_name = _first_match(haystack, INCOME_RULES)
        if category_name is None:
            category_name = _first_match(haystack, KNOWN_PERSON_RULES)
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
