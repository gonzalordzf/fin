"""Parser for GBM's monthly CFDI 4.0 XML.

Like BBVA, the top-level CFDI (Conceptos, Total, etc.) is a $0.01 filler
"Servicios de Facturación" invoice — not the data. GBM's actual movements
are embedded as a CSV-like text block inside a bare, non-namespaced
<Movimientos> element under <cfdi:Addenda>:

    Movimientos:
    Contrato, Descripción, Monto, Fecha, Folio

     AAU94801, Premio en vencimiento de reporto, 0.17, 01-07-2026, 1935118371
     ...

Verified across both contracts (AAU94801/AAU94802) and multiple months:
same 5 columns, dates as DD-MM-YYYY, one row per daily reporto interest
accrual. Folio is a globally unique movement id, used for dedup.
"""

from __future__ import annotations

import datetime
import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass
class GBMMovement:
    contract: str
    description: str
    amount: float
    date: datetime.date
    folio: str


def _find_movimientos_text(root: ET.Element) -> str | None:
    for el in root.iter():
        if el.tag == "Movimientos" and el.text:
            return el.text
    return None


def parse_gbm_statement(path: str) -> list[GBMMovement]:
    tree = ET.parse(path)
    text = _find_movimientos_text(tree.getroot())
    if text is None:
        return []

    lines = [line.strip() for line in text.splitlines()]
    # Skip "Movimientos:", the "Contrato, Descripción, ..." header, and
    # blank lines; every remaining line is a data row.
    data_lines = [
        line
        for line in lines
        if line and not line.startswith("Movimientos") and not line.startswith("Contrato,")
    ]

    movements = []
    for line in data_lines:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        contrato, *desc_parts, monto, fecha, folio = parts
        movements.append(
            GBMMovement(
                contract=contrato,
                description=", ".join(desc_parts),
                amount=float(monto.replace(",", "")),
                date=datetime.datetime.strptime(fecha, "%d-%m-%Y").date(),
                folio=folio,
            )
        )
    return movements
