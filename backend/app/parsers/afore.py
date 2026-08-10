"""Parser for AFORE SURA's "detalleMovimientos.pdf" export.

Single-page PDF, but pdfplumber's default extract_text() interleaves the
label column ("SALDO ACTUAL:", "Retiro:", "Vivienda *:", "Voluntario:",
"Saldo en tránsito:") and the value column into two separate blocks rather
than label-value pairs on the same line — a layout quirk, not the same
problem as BBVA's table but the same fix: match by word y-coordinate
(top) instead of trusting line order.

The account's current balance is only ever reported as one snapshot split
into 4 named subcuentas (Retiro, Vivienda, Voluntario, Saldo en tránsito).
Verified: the four sum exactly to "SALDO ACTUAL" on the one real statement
seen (672,001.86 + 378,770.38 + 0 + 0 = 1,050,772.24) — that's the
reconciliation check below.

The "MOVIMIENTOS EN TU CUENTA" section (APORTACION INFONAVIT / PATRONAL /
CESANTIA rows) is deliberately NOT parsed into transactions: those
contributions are already baked into SALDO ACTUAL, so turning them into
Transaction rows and also snapshotting the balance would double-count them
in net worth — the same trap avoided for GBM. If a running contribution
history is ever wanted, it needs its own non-additive treatment, not a
plain Transaction table.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

import pdfplumber

_MONEY_RE = re.compile(r"\$\s*(-|[\d,]+\.\d{2})")
_Y_TOLERANCE = 8.0

_SUBCUENTA_LABELS = ["Retiro", "Vivienda", "Voluntario", "Saldo en tránsito"]


@dataclass
class AforeSnapshot:
    fecha_emision: datetime.date
    saldo_actual: float
    subcuentas: dict[str, float]


def _money(text: str) -> float:
    m = _MONEY_RE.search(text)
    if not m or m.group(1) == "-":
        return 0.0
    return float(m.group(1).replace(",", ""))


def _nearest_value_right_of(words: list[dict], label_top: float, label_x1: float) -> str | None:
    candidates = [
        w
        for w in words
        if w["text"].startswith("$")
        and w["x0"] > label_x1
        and abs(w["top"] - label_top) <= _Y_TOLERANCE
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda w: abs(w["top"] - label_top))["text"]


def parse_afore_statement(path: str) -> AforeSnapshot:
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) != 1:
            raise ValueError(f"Expected a 1-page AFORE statement, got {len(pdf.pages)} pages")
        words = pdf.pages[0].extract_words()

    date_m = re.search(r"(\d{2})/(\d{2})/(\d{4})", " ".join(w["text"] for w in words if w["top"] < 80))
    if not date_m:
        raise ValueError("Could not find 'FECHA Y HORA DE EMISIÓN' on AFORE statement")
    d, m, y = date_m.groups()
    fecha_emision = datetime.date(int(y), int(m), int(d))

    saldo_label = next((w for w in words if w["text"] == "ACTUAL:"), None)
    if saldo_label is None:
        raise ValueError("Could not find 'SALDO ACTUAL' label on AFORE statement")
    saldo_value = _nearest_value_right_of(words, saldo_label["top"], saldo_label["x1"])
    if saldo_value is None:
        raise ValueError("Could not find a value next to 'SALDO ACTUAL'")
    saldo_actual = _money(saldo_value)

    subcuentas: dict[str, float] = {}
    for label in _SUBCUENTA_LABELS:
        first_word = label.split()[0]
        label_word = next(
            (w for w in words if w["text"].rstrip("*:") == first_word.rstrip("*:")), None
        )
        if label_word is None:
            raise ValueError(f"Could not find label {label!r} on AFORE statement")
        # Use the rightmost word of a multi-word label for the x1 anchor.
        same_row = [w for w in words if abs(w["top"] - label_word["top"]) <= 2.0]
        label_x1 = max(w["x1"] for w in same_row if not w["text"].startswith("$"))
        value = _nearest_value_right_of(words, label_word["top"], label_x1)
        if value is None:
            raise ValueError(f"Could not find a value next to label {label!r}")
        subcuentas[label] = _money(value)

    computed_total = round(sum(subcuentas.values()), 2)
    if abs(computed_total - saldo_actual) > 0.01:
        raise ValueError(
            "AFORE statement does not reconcile: subcuentas sum to "
            f"{computed_total} but SALDO ACTUAL is {saldo_actual}"
        )

    return AforeSnapshot(fecha_emision=fecha_emision, saldo_actual=saldo_actual, subcuentas=subcuentas)
