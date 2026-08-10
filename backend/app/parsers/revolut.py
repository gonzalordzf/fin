"""Parser for Revolut Mexico's "Estado de cuenta de Tarjeta de Crédito" PDF.

The brief assumed a multi-currency debit+credit wallet export; what's
actually in Drive is 3 monthly MXN credit-card statements (no per-txn
foreign-currency column — unlike AMEX, there's nothing to parse there).
The real transaction table is "Cargos, abonos y compras regulares (No a
meses)": Fecha operación, Fecha de cargo, Descripción de movimiento,
Monto — verified against two real statements.

Revolut's sign convention is inverted from ours (like AMEX): positive
Monto = cargo/charge, negative = abono/payment or credit. We negate it
so negative = money out (expense), consistent with every other importer.

Every statement prints its own "Total cargos" / "Total abonos" right
after the transaction table (in Revolut's own, pre-negation sign
convention) — parse_revolut_statement() reconciles the parsed
transactions against these before returning, same discipline as BBVA:
raise instead of silently trusting the extraction.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field

import pdfplumber

_MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}
_MONTH_RE = "|".join(_MESES)
_DATE_RE = re.compile(rf"^(\d{{1,2}})\s+({_MONTH_RE})\s+(\d{{4}})$", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"^[+-]\$[\d,]+\.\d{2}$")
_TOTAL_CARGOS_RE = re.compile(r"Total cargos\s+([+-]\$[\d,]+\.\d{2})")
_TOTAL_ABONOS_RE = re.compile(r"Total abonos\s+([+-]\$[\d,]+\.\d{2})")

# Column boundaries in the "Cargos, abonos y compras regulares" table,
# verified via word x0 coordinates against real statements.
_COL_FECHA_OPERACION_MAX = 150.0
_COL_FECHA_CARGO_MAX = 270.0

_ROW_TOLERANCE = 3.0


@dataclass
class RevolutTransaction:
    operation_date: datetime.date
    charge_date: datetime.date
    description: str
    amount: float
    """Signed: negative = cargo/charge (expense), positive = abono/credit."""
    raw_lines: list[str] = field(default_factory=list)


def _parse_fecha(day: str, mon: str, year: str) -> datetime.date:
    return datetime.date(int(year), _MESES[mon.lower()], int(day))


def _parse_signed_money(text: str) -> float:
    sign = -1.0 if text.startswith("-") else 1.0
    return sign * float(text.lstrip("+-$").replace(",", ""))


def _validate(transactions: list[RevolutTransaction], text: str) -> None:
    cargos_m = _TOTAL_CARGOS_RE.search(text)
    abonos_m = _TOTAL_ABONOS_RE.search(text)
    if not (cargos_m and abonos_m):
        missing = [n for n, m in (("Total cargos", cargos_m), ("Total abonos", abonos_m)) if m is None]
        raise ValueError(
            f"Could not find printed totals in Revolut statement, cannot validate: {', '.join(missing)}"
        )
    printed_cargos = _parse_signed_money(cargos_m.group(1))
    printed_abonos = _parse_signed_money(abonos_m.group(1))

    # Our amount is negated from Revolut's own convention (see module
    # docstring), so re-negating gets back to Revolut's own sign to compare
    # directly against what's printed.
    parsed_cargos = round(sum(-t.amount for t in transactions if t.amount < 0), 2)
    parsed_abonos = round(sum(-t.amount for t in transactions if t.amount > 0), 2)

    errors = []
    if abs(parsed_cargos - printed_cargos) > 0.01:
        errors.append(f"cargos: parsed {parsed_cargos} vs printed {printed_cargos}")
    if abs(parsed_abonos - printed_abonos) > 0.01:
        errors.append(f"abonos: parsed {parsed_abonos} vs printed {printed_abonos}")
    if errors:
        raise ValueError(
            "Revolut statement does not reconcile against its own printed totals: "
            + "; ".join(errors)
        )


def _group_lines(words: list[dict]) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= _ROW_TOLERANCE:
            lines[-1].append(w)
        else:
            lines.append([w])
    for line in lines:
        line.sort(key=lambda w: w["x0"])
    return lines


def _line_text(words: list[dict]) -> str:
    return " ".join(w["text"] for w in words)


def _try_parse_date_group(words: list[dict]) -> datetime.date | None:
    text = _line_text(words)
    m = _DATE_RE.match(text)
    return _parse_fecha(*m.groups()) if m else None


def parse_revolut_statement(path: str) -> list[RevolutTransaction]:
    transactions: list[RevolutTransaction] = []
    full_text_parts: list[str] = []
    with pdfplumber.open(path) as pdf:
        in_table = False
        pending: RevolutTransaction | None = None

        for page in pdf.pages:
            full_text_parts.append(page.extract_text() or "")
            words = page.extract_words()
            if not in_table and not any(
                w["text"] == "Descripción" for w in words
            ):
                continue

            for line_words in _group_lines(words):
                text = _line_text(line_words)
                if text.startswith("Descripción de movimiento") or (
                    "Descripción" in text and "movimiento" in text
                ):
                    in_table = True
                    continue
                if not in_table:
                    continue
                if text.startswith("Total cargos") or text.startswith("Cargo no reconocidos"):
                    in_table = False
                    if pending is not None:
                        transactions.append(pending)
                        pending = None
                    continue

                date1_words = [w for w in line_words if w["x0"] < _COL_FECHA_OPERACION_MAX]
                date2_words = [
                    w
                    for w in line_words
                    if _COL_FECHA_OPERACION_MAX <= w["x0"] < _COL_FECHA_CARGO_MAX
                ]
                rest_words = [w for w in line_words if w["x0"] >= _COL_FECHA_CARGO_MAX]

                op_date = _try_parse_date_group(date1_words) if date1_words else None
                charge_date = _try_parse_date_group(date2_words) if date2_words else None

                if op_date is not None and charge_date is not None:
                    if pending is not None:
                        transactions.append(pending)
                    amount = None
                    desc_words = []
                    for w in rest_words:
                        if _AMOUNT_RE.match(w["text"]):
                            amount = -float(w["text"].replace("$", "").replace(",", ""))
                        else:
                            desc_words.append(w["text"])
                    pending = RevolutTransaction(
                        operation_date=op_date,
                        charge_date=charge_date,
                        description=" ".join(desc_words),
                        amount=amount if amount is not None else 0.0,
                        raw_lines=[text],
                    )
                elif pending is not None:
                    pending.raw_lines.append(text)

        if pending is not None:
            transactions.append(pending)

    _validate(transactions, "\n".join(full_text_parts))
    return transactions
