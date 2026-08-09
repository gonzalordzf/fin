"""Parser for AMEX "activity" CSV exports.

Real header (verified against downloaded exports):
Fecha, Fecha de Compra, Descripción, Titular de la Tarjeta, Cuenta,
Importe, Monto en moneda extranjera, Tipo de Cambio, Información
Adicional, Aparece en su Estado de Cuenta como, Dirección,
Población/Provincia, Código postal, País, Referencia

Importe's sign is the opposite of our schema convention: AMEX uses
positive for a charge (money the cardholder owes) and negative for a
credit/refund/payment. We negate it so, consistent with every other
importer, negative = money out (expense), positive = money in.
"""

from __future__ import annotations

import csv
import datetime
from dataclasses import dataclass


@dataclass
class AmexTransaction:
    purchase_date: datetime.date
    post_date: datetime.date
    description: str
    amount: float
    """Signed: negative = charge (expense), positive = credit/refund/payment."""
    foreign_amount: str | None
    """Raw 'amount CUR' string (e.g. '89.52 USD'), kept as-is; no FX columns
    in the schema yet since only AMEX carries this and it's informational."""
    external_ref: str | None
    raw_row: dict[str, str]


def _parse_date(text: str) -> datetime.date:
    return datetime.datetime.strptime(text.strip(), "%d %b %Y").date()


def parse_amex_csv(path: str) -> list[AmexTransaction]:
    transactions: list[AmexTransaction] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            importe = float(row["Importe"].replace(",", ""))
            ref = row.get("Referencia", "").strip().strip("'") or None
            transactions.append(
                AmexTransaction(
                    purchase_date=_parse_date(row["Fecha de Compra"]),
                    post_date=_parse_date(row["Fecha"]),
                    description=row["Descripción"].strip(),
                    amount=-importe,
                    foreign_amount=row.get("Monto en moneda extranjera", "").strip() or None,
                    external_ref=ref,
                    raw_row=row,
                )
            )
    return transactions
