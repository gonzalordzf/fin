"""Parser for BBVA "Estado de Cuenta" PDFs.

The BBVA XML that comes in the same monthly ZIP is a $0.01 filler CFDI
("Servicios de Facturación") — it does not contain the account's actual
movements. The real transaction detail ("Detalle de Movimientos
Realizados") only exists in the password-protected PDF, as a table with
columns: FECHA OPER, FECHA LIQ, DESCRIPCION, REFERENCIA, CARGOS, ABONOS,
SALDO OPERACION, SALDO LIQUIDACION.

This parser locates that table by column x-position (not by counting
whitespace, which breaks across pdfplumber/pdftotext versions and page
continuations that omit the header), classifying each amount into CARGOS
vs ABONOS by which column's x-range its right-aligned edge falls in —
verified against a real statement, since the two columns can visually
overlap for wide numbers.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field

import pdfplumber

_DATE_RE = re.compile(r"^(\d{2})/([A-Zé]{3})$", re.IGNORECASE)
_FULL_DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
_AMOUNT_RE = re.compile(r"^-?[\d,]+\.\d{2}$")
_REFERENCIA_RE = re.compile(r"^\d{6,}$")

_MESES = {
    "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12,
}

_HEADER_LABELS = [
    "OPER", "LIQ", "DESCRIPCION", "REFERENCIA",
    "CARGOS", "ABONOS", "OPERACION", "LIQUIDACION",
]

_ROW_TOLERANCE = 3.0  # points; words within this vertical band are one line


@dataclass
class BBVATransaction:
    operation_date: datetime.date
    liquidation_date: datetime.date
    description: str
    amount: float
    """Signed: negative = cargo (money out), positive = abono (money in)."""
    balance_operacion: float | None
    balance_liquidacion: float | None
    external_ref: str | None
    row_index: int
    """0-based position in the statement's Detalle de Movimientos, in
    document order. BBVA doesn't always give a usable reference number
    (e.g. Banxico compensation fees repeat the same date/amount/description
    with no reference), so this — not the field values — is what makes
    re-importing the same file idempotent without merging distinct rows."""
    raw_lines: list[str] = field(default_factory=list)


@dataclass
class _Columns:
    oper: float
    liq: float
    descripcion: float
    referencia: float
    cargos: float
    abonos: float
    operacion: float
    liquidacion: float

    def classify(self, x1: float) -> str:
        """Classify a right-aligned number by its right edge x1."""
        boundaries = [
            (self.cargos, self.abonos, "cargos"),
            (self.abonos, self.operacion, "abonos"),
            (self.operacion, self.liquidacion, "balance_operacion"),
            (self.liquidacion, float("inf"), "balance_liquidacion"),
        ]
        for lo, hi, name in boundaries:
            if lo <= x1 < hi:
                return name
        return "unknown"


def _find_period(first_page_words: list[dict]) -> tuple[datetime.date, datetime.date]:
    for i, w in enumerate(first_page_words):
        if w["text"] == "Periodo":
            dates = []
            for w2 in first_page_words[i : i + 6]:
                m = _FULL_DATE_RE.match(w2["text"])
                if m:
                    d, mo, y = (int(g) for g in m.groups())
                    dates.append(datetime.date(y, mo, d))
            if len(dates) == 2:
                return dates[0], dates[1]
    raise ValueError("Could not find 'Periodo' on statement's first page")


def _find_columns(words: list[dict]) -> _Columns | None:
    found = {w["text"]: w["x0"] for w in words if w["text"] in _HEADER_LABELS}
    if not all(label in found for label in _HEADER_LABELS):
        return None
    return _Columns(
        oper=found["OPER"],
        liq=found["LIQ"],
        descripcion=found["DESCRIPCION"],
        referencia=found["REFERENCIA"],
        cargos=found["CARGOS"],
        abonos=found["ABONOS"],
        operacion=found["OPERACION"],
        liquidacion=found["LIQUIDACION"],
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


def _resolve_year(month_abbr: str, period_start: datetime.date, period_end: datetime.date) -> int:
    month = _MESES[month_abbr.upper()]
    if month == period_start.month:
        return period_start.year
    if month == period_end.month:
        return period_end.year
    # Statement period spans at most two calendar months; fall back to the
    # closer one if a date somehow falls outside that range.
    return period_end.year


def _start_transaction(
    line_words: list[dict],
    columns: _Columns,
    period_start: datetime.date,
    period_end: datetime.date,
    row_index: int,
) -> BBVATransaction:
    oper_m = _DATE_RE.match(line_words[0]["text"])
    liq_m = _DATE_RE.match(line_words[1]["text"])
    oper_day, oper_mon = oper_m.groups()
    liq_day, liq_mon = liq_m.groups()
    operation_date = datetime.date(
        _resolve_year(oper_mon, period_start, period_end), _MESES[oper_mon.upper()], int(oper_day)
    )
    liquidation_date = datetime.date(
        _resolve_year(liq_mon, period_start, period_end), _MESES[liq_mon.upper()], int(liq_day)
    )

    desc_words = []
    amount = None
    balance_operacion = None
    balance_liquidacion = None
    for w in line_words[2:]:
        text = w["text"]
        if _AMOUNT_RE.match(text):
            value = float(text.replace(",", ""))
            col = columns.classify(w["x1"])
            if col == "cargos":
                amount = -value
            elif col == "abonos":
                amount = value
            elif col == "balance_operacion":
                balance_operacion = value
            elif col == "balance_liquidacion":
                balance_liquidacion = value
        elif w["x0"] < columns.referencia:
            desc_words.append(text)

    return BBVATransaction(
        operation_date=operation_date,
        liquidation_date=liquidation_date,
        description=" ".join(desc_words),
        amount=amount if amount is not None else 0.0,
        balance_operacion=balance_operacion,
        balance_liquidacion=balance_liquidacion,
        external_ref=None,
        row_index=row_index,
        raw_lines=[" ".join(w["text"] for w in line_words)],
    )


def _append_continuation(txn: BBVATransaction, line_words: list[dict]) -> None:
    txn.raw_lines.append(" ".join(w["text"] for w in line_words))
    texts = [w["text"] for w in line_words]
    if "Referencia" in texts and txn.external_ref is None:
        idx = texts.index("Referencia")
        rest = texts[idx + 1 :]
        if rest and _REFERENCIA_RE.match(rest[0]):
            txn.external_ref = rest[0]


def parse_bbva_statement(path: str, password: str) -> list[BBVATransaction]:
    transactions: list[BBVATransaction] = []
    with pdfplumber.open(path, password=password) as pdf:
        period_start = period_end = None
        for page in pdf.pages[:3]:
            try:
                period_start, period_end = _find_period(page.extract_words())
                break
            except ValueError:
                continue
        if period_start is None:
            raise ValueError("Could not find 'Periodo' in the first pages of the statement")

        columns: _Columns | None = None
        pending: BBVATransaction | None = None
        in_table = False

        for page in pdf.pages:
            words = page.extract_words()
            if columns is None:
                columns = _find_columns(words)
                if columns is None:
                    continue

            for line_words in _group_lines(words):
                first_text = line_words[0]["text"]
                if first_text == "Total" and len(line_words) > 1 and line_words[1]["text"] == "de":
                    # "Total de Movimientos" footer — table ends here.
                    in_table = False
                    continue
                if len(line_words) >= 2 and _DATE_RE.match(first_text) and _DATE_RE.match(line_words[1]["text"]):
                    in_table = True
                    if pending is not None:
                        transactions.append(pending)
                    pending = _start_transaction(
                        line_words, columns, period_start, period_end, row_index=len(transactions)
                    )
                elif in_table and pending is not None and line_words[0]["x0"] < columns.referencia + 50:
                    _append_continuation(pending, line_words)

        if pending is not None:
            transactions.append(pending)

    return transactions
