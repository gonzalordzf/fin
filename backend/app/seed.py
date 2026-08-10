"""Seed the real-world accounts (+ GBM's sub-contracts) and a starter
expense/income category taxonomy.

Categories are plain data, not parser logic — edit/extend this list any
time without touching import code. Re-running is safe: existing rows
(matched by name) are left alone.
"""

from app.db import get_session, init_db
from app.manual_data import apply_manual_data
from app.models import Account, AccountKind, Category, CategoryKind, CategoryNature

ACCOUNTS: list[dict] = [
    {"name": "BBVA", "institution": "BBVA México", "kind": AccountKind.TRANSACTIONAL, "currency": "MXN"},
    {"name": "AMEX", "institution": "American Express", "kind": AccountKind.TRANSACTIONAL, "currency": "MXN"},
    {"name": "Revolut", "institution": "Revolut", "kind": AccountKind.TRANSACTIONAL, "currency": "MXN"},
    {"name": "Bitso", "institution": "Bitso", "kind": AccountKind.INVESTMENT_FORMAL, "currency": "MXN"},
    {"name": "GBM", "institution": "GBM", "kind": AccountKind.INVESTMENT_FORMAL, "currency": "MXN"},
    {"name": "Balagan", "institution": "Balagan", "kind": AccountKind.INVESTMENT_INFORMAL, "currency": "MXN"},
    {"name": "Optimax (Allianz)", "institution": "Allianz", "kind": AccountKind.INVESTMENT_FORMAL, "currency": "MXN"},
    {"name": "Shareworks", "institution": "Shareworks (Coca-Cola)", "kind": AccountKind.EQUITY_COMPENSATION, "currency": "USD"},
    {"name": "AFORE (Sura)", "institution": "AFORE SURA", "kind": AccountKind.INVESTMENT_FORMAL, "currency": "MXN"},
    # Convertible loan (EUR 7,500, no interest, converts to equity at the
    # 3-year mark) into "Arreola Herrera Fund I" — a fund run by Jose
    # Manuel Arreola (a former roommate, see rules.py's DATED_PERSON_RULES)
    # and Paola Herrera that owns Olivares & Herrera, LDA, trading as
    # "Lavande" (a laundromat in Lisbon). User confirmed and provided the
    # contract docs after this was found unclassified in BBVA — currency
    # kept as EUR rather than converted, same principle as Bitso/Shareworks
    # staying in USD.
    {"name": "Grupo Arreola", "institution": "Olivares & Herrera, LDA (Lavande)", "kind": AccountKind.INVESTMENT_INFORMAL, "currency": "EUR"},
    # $50,000 MXN into "Grupo Alvarez Lomelín Martínez, S.A.P.I. de C.V.",
    # which owns the brewery "Cañadas de Malta": $25,000 as a 3-year
    # commercial loan (mutuo mercantil) at 12%/year fixed, and $25,000 as
    # equity (625 shares, Serie B Clase II, 0.5%). Predates this app's BBVA
    # history (signed ago-2021, data starts feb-2023), so there is no
    # statement to import against — same situation as GBM's manual balance.
    {"name": "Cañadas de Malta", "institution": "Grupo Alvarez Lomelín Martínez, S.A.P.I. de C.V.", "kind": AccountKind.INVESTMENT_INFORMAL, "currency": "MXN"},
]

GBM_CONTRACTS = ["AAU94801", "AAU94802"]

EXPENSE_CATEGORIES: list[tuple[str, CategoryNature | None]] = [
    ("Vivienda", CategoryNature.BASICO),
    ("Transporte", CategoryNature.NECESARIO),
    ("Alimentos y Supermercado", CategoryNature.BASICO),
    ("Restaurantes y Café", CategoryNature.ESTILO_DE_VIDA),
    ("Delivery", CategoryNature.ESTILO_DE_VIDA),
    ("Entretenimiento", CategoryNature.ESTILO_DE_VIDA),
    ("Salud", CategoryNature.BASICO),
    ("Viajes", CategoryNature.ESTILO_DE_VIDA),
    ("Compras", CategoryNature.ESTILO_DE_VIDA),
    # Mezcla telefonía (Básico per docs/02-categorias.md) y suscripciones de
    # streaming/nube (Necesario ahí) en una sola categoría — decisión de
    # seed previa a esta capa, no algo que se resuelva solo. NECESARIO es
    # el punto medio defendible; separarla en dos categorías es una mejora
    # futura, no una que se está haciendo aquí.
    ("Servicios y Suscripciones", CategoryNature.NECESARIO),
    ("Educación", CategoryNature.NECESARIO),
    # Sin equivalente directo en docs/02-categorias.md — juicio propio:
    # cuidado personal discrecional (spa, salón) tiende a Estilo de vida
    # más que a Necesario.
    ("Cuidado Personal", CategoryNature.ESTILO_DE_VIDA),
    ("Impuestos y Comisiones Bancarias", CategoryNature.BASICO),
    ("Efectivo (ATM)", CategoryNature.NECESARIO),
    ("Inversión", CategoryNature.NECESARIO),
    # Mundial 2026: kind=EXPENSE on purpose, unlike the self-transfer/
    # pass-through categories below — real ticket purchases (mostly SPEI
    # to Federación Mexicana de Fútbol) AND the reimbursements from the
    # friend group who paid Gonzalo back are both tagged here. Since
    # /spending-by-category only sums amount<0 rows, the reimbursements
    # (positive) don't count as gasto, so this nets to the real personal
    # cost automatically instead of the gross ticket price — confirmed
    # against real data: $698,370 paid, $672,386 reimbursed, ~$25,984
    # actually Gonzalo's own tickets. Also covers AMEX charges made at
    # the stadiums themselves, not just the tickets.
    ("Mundial", CategoryNature.ESTILO_DE_VIDA),
    ("Regalos", CategoryNature.ESTILO_DE_VIDA),
    ("Otros Gastos", None),
]
"""'Delivery' separado de 'Restaurantes y Café', 'Efectivo (ATM)' e
'Inversión' agregadas — las tres validadas contra estados reales (Rappi/Uber
Eats, 'RETIRO SIN TARJETA' en BBVA, y aportaciones/rescates hacia
GBM/Bitso/Optimax respectivamente). 'Inversión' es Necesario y no gasto
recortable per docs/02-categorias.md — no se resta del patrimonio, solo se
excluye de gasto discrecional en el análisis de recorte.
'Otros Gastos' no lleva naturaleza: por definición es lo sin clasificar."""

INCOME_CATEGORIES = [
    "Nómina",
    "Ingreso por Inversión",
    "Reembolsos",
    # SPEI recurrentes (~semestrales) de "SURA INVESTMENT MANAGEMENT
    # MEXICO" hacia BBVA — confirmado por el usuario: caja/fondo de ahorro
    # de la empresa, no una aportación de inversión (no confundir con la
    # cuenta AFORE SURA, que es una cuenta totalmente separada).
    "Fondo de Ahorro",
    "Otros Ingresos",
]

TRANSFER_CATEGORIES = [
    "Transferencia entre Cuentas Propias",
    "Pago de Tarjeta de Crédito",
]


def seed() -> None:
    init_db()
    with get_session() as session:
        accounts_by_name: dict[str, Account] = {}
        for spec in ACCOUNTS:
            existing = session.query(Account).filter_by(name=spec["name"]).one_or_none()
            if existing is None:
                existing = Account(**spec)
                session.add(existing)
                session.flush()
            accounts_by_name[spec["name"]] = existing

        gbm = accounts_by_name["GBM"]
        for contract in GBM_CONTRACTS:
            name = f"GBM {contract}"
            existing = session.query(Account).filter_by(name=name).one_or_none()
            if existing is None:
                session.add(
                    Account(
                        name=name,
                        institution="GBM",
                        kind=AccountKind.INVESTMENT_FORMAL,
                        currency="MXN",
                        parent_account_id=gbm.id,
                        notes=f"Contrato {contract}",
                    )
                )

        for name, nature in EXPENSE_CATEGORIES:
            existing = (
                session.query(Category).filter_by(name=name, kind=CategoryKind.EXPENSE).one_or_none()
            )
            if existing is None:
                session.add(Category(name=name, kind=CategoryKind.EXPENSE, nature=nature))

        for kind, names in (
            (CategoryKind.INCOME, INCOME_CATEGORIES),
            (CategoryKind.TRANSFER, TRANSFER_CATEGORIES),
        ):
            for name in names:
                existing = (
                    session.query(Category).filter_by(name=name, kind=kind).one_or_none()
                )
                if existing is None:
                    session.add(Category(name=name, kind=kind))

        session.commit()

    apply_manual_data()


if __name__ == "__main__":
    seed()
