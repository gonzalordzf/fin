"""Seed the 8 real-world accounts (+ GBM's sub-contracts) and a starter
expense/income category taxonomy.

Categories are plain data, not parser logic — edit/extend this list any
time without touching import code. Re-running is safe: existing rows
(matched by name) are left alone.
"""

from app.db import get_session, init_db
from app.manual_data import apply_manual_data
from app.models import Account, AccountKind, Category, CategoryKind

ACCOUNTS: list[dict] = [
    {"name": "BBVA", "institution": "BBVA México", "kind": AccountKind.TRANSACTIONAL, "currency": "MXN"},
    {"name": "AMEX", "institution": "American Express", "kind": AccountKind.TRANSACTIONAL, "currency": "MXN"},
    {"name": "Revolut", "institution": "Revolut", "kind": AccountKind.TRANSACTIONAL, "currency": "MXN"},
    {"name": "Bitso", "institution": "Bitso", "kind": AccountKind.INVESTMENT_FORMAL, "currency": "MXN"},
    {"name": "GBM", "institution": "GBM", "kind": AccountKind.INVESTMENT_FORMAL, "currency": "MXN"},
    {"name": "Balagan", "institution": "Balagan", "kind": AccountKind.INVESTMENT_INFORMAL, "currency": "MXN"},
    {"name": "Optimax (Allianz)", "institution": "Allianz", "kind": AccountKind.INVESTMENT_FORMAL, "currency": "MXN"},
    {"name": "Shareworks", "institution": "Shareworks (Coca-Cola)", "kind": AccountKind.EQUITY_COMPENSATION, "currency": "USD"},
]

GBM_CONTRACTS = ["AAU94801", "AAU94802"]

EXPENSE_CATEGORIES = [
    "Vivienda",
    "Transporte",
    "Alimentos y Supermercado",
    "Restaurantes y Café",
    "Delivery",
    "Entretenimiento",
    "Salud",
    "Viajes",
    "Compras",
    "Servicios y Suscripciones",
    "Educación",
    "Cuidado Personal",
    "Impuestos y Comisiones Bancarias",
    "Efectivo (ATM)",
    "Inversión",
    "Otros Gastos",
]
"""'Delivery' separado de 'Restaurantes y Café', 'Efectivo (ATM)' e
'Inversión' agregadas — las tres validadas contra estados reales (Rappi/Uber
Eats, 'RETIRO SIN TARJETA' en BBVA, y aportaciones/rescates hacia
GBM/Bitso/Optimax/Sura respectivamente). 'Inversión' es Necesario y no
gasto recortable per docs/02-categorias.md — no se resta del patrimonio,
solo se excluye de gasto discrecional una vez que exista la capa de
naturaleza (#19)."""

INCOME_CATEGORIES = [
    "Nómina",
    "Ingreso por Inversión",
    "Reembolsos",
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

        for kind, names in (
            (CategoryKind.EXPENSE, EXPENSE_CATEGORIES),
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
