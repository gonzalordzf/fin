"""Parser for Bitso's "Reporte de transacciones" CSV export.

Real encoding is Latin-1 (ISO-8859-1), not UTF-8 — accented characters
(Descripción, Operación, etc.) will raise UnicodeDecodeError otherwise.

The file isn't a flat table. It has two parts:

1. A per-asset balance snapshot, comparing start-of-period vs
   end-of-period Balance/Precio/Saldo en wallet (fiat) — this is a
   point-in-time valuation, so it maps to HoldingSnapshot (one row per
   asset, dated at the period's end).
2. A movement log below a "Fecha de ejecución de orden, ..." header,
   with a "Otras operaciones" section (in the one real account seen so
   far, the only content: weekly "Rendimiento" staking-reward accruals
   in ETH and USDC, valued at USD 0 — so amounts are recorded in the
   asset's own native units/currency, not coerced to a meaningless $0).

The file ends with a second, identical-looking header for a "moneda
local" section that is entirely blank padding in every real export seen
— parsing stops there.
"""

from __future__ import annotations

import csv
import datetime
import re
from dataclasses import dataclass

_HEADER_TEXT = "Fecha de ejecución de orden"
_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")

_MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}


@dataclass
class BitsoSnapshot:
    asset: str
    date: datetime.date
    balance: float
    market_value_usd: float


@dataclass
class BitsoMovement:
    date: datetime.date
    operation: str
    asset: str
    symbol: str
    crypto_amount: float
    fiat_value_usd: float


def _parse_money(text: str) -> float:
    return float(text.replace("USD", "").replace(",", "").strip())


def _parse_period_end(rows: list[list[str]]) -> datetime.date:
    for row in rows:
        if "Periodo" in row:
            period = row[row.index("Periodo") + 1]
            end_text = period.split(" - ")[1].strip()
            mon_str, day_str, year_str = end_text.split(" ")
            return datetime.date(int(year_str), _MESES[mon_str.lower()[:3]], int(day_str))
    raise ValueError("Could not find 'Periodo' in Bitso report header")


def _parse_snapshots(rows: list[list[str]], period_end: datetime.date) -> list[BitsoSnapshot]:
    header_idx = next(i for i, r in enumerate(rows) if len(r) > 1 and r[1] == "Activo")
    snapshots = []
    for row in rows[header_idx + 1 :]:
        if len(row) < 9 or not row[1]:
            break
        snapshots.append(
            BitsoSnapshot(
                asset=row[1],
                date=period_end,
                balance=float(row[6]),
                market_value_usd=_parse_money(row[8]),
            )
        )
    return snapshots


def _parse_movements(rows: list[list[str]]) -> list[BitsoMovement]:
    header_idxs = [i for i, r in enumerate(rows) if r and r[0] == _HEADER_TEXT]
    if not header_idxs:
        return []
    start = header_idxs[0] + 1
    end = header_idxs[1] if len(header_idxs) > 1 else len(rows)

    movements = []
    for row in rows[start:end]:
        if not row or not _DATE_RE.match(row[0]):
            continue
        day, month, year = (int(p) for p in row[0].split("/"))
        movements.append(
            BitsoMovement(
                date=datetime.date(year, month, day),
                operation=row[1],
                asset=row[3],
                symbol=row[4],
                crypto_amount=float(row[5]),
                fiat_value_usd=_parse_money(row[7]),
            )
        )
    return movements


def parse_bitso_report(path: str) -> tuple[list[BitsoSnapshot], list[BitsoMovement]]:
    with open(path, encoding="latin-1", newline="") as f:
        rows = list(csv.reader(f))
    period_end = _parse_period_end(rows)
    return _parse_snapshots(rows, period_end), _parse_movements(rows)
