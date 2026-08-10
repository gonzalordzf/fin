"""Parser for Balagan's monthly "Estado de Resultados" PDF.

These are plain text-based PDFs (not scanned), but two things make them
unreliable to parse naively:

1. Filenames are inconsistent ("EDO RES JUNIO BALAGAN.pdf",
   "EDO RES MAYO BALAGAN26.pdf", "Estado de resultados BALAGAN
   ABRIL.pdf") — confirmed exactly as the project brief warned.
2. The "Por el período comprendido..." line inside the PDF itself can be
   wrong (the June statement says "del 1o de Mayo al 31 de Junio de
   2026" — a copy-paste leftover from the May template). The month name
   in that line isn't trustworthy; the year is, though, and the
   filename's month always is.

So: month comes from the filename (case/accent-insensitive full Spanish
month name, in whatever position), year from the trailing "de YYYY" in
the período line. The period is then treated as that whole calendar
month.

The user's participation is confirmed by the actual collaboration
contract (Cláusula QUINTA): a flat 1% of monthly utilidad — verified
against real figures (June: Repartición por punto 3,002 / Utilidad
300,163 ≈ 1%). "Repartición por punto" is Balagan's own pre-computed
proportional income, taken directly rather than re-derived, since it's
authoritative.
"""

from __future__ import annotations

import calendar
import datetime
import re
import unicodedata
from dataclasses import dataclass

import pdfplumber

PARTICIPATION_PCT = 0.01
"""Fixed by the collaboration contract (Cláusula QUINTA), not per-file data."""

INITIAL_INVESTMENT_MXN = 75000.0
"""Fixed by the collaboration contract (Cláusula TERCERA): "$75,000.00 M.N.
(Setenta y cinco mil pesos 00/100 Moneda Nacional)". Not derivable from any
monthly Estado de Resultados — those only report period income, never
this one-time capital contribution — so it's a constant here rather than
parsed."""

_MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12,
}
_MONTH_RE = re.compile(
    "|".join(_MESES), re.IGNORECASE
)
_YEAR_RE = re.compile(r"de (\d{4})")
_NUMBER_TOKEN_RE = re.compile(r"-?[\d,]+\.?\d*")


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def _find_amount(text: str, label: str) -> float:
    """Finds "$ <amount>" after `label`. The amount is joined from every
    consecutive numeric token after the "$", not just the first one:
    confirmed on a real statement that pdfplumber's extraction sometimes
    inserts a stray space inside a number ("$ 2 38,964" printed as
    "$238,964") — matching only the first token silently truncated the
    figure to $2."""
    target = _strip_accents(label).lower()
    for line in text.splitlines():
        if _strip_accents(line).lower().startswith(target):
            tokens = line.split()
            for i, tok in enumerate(tokens):
                if tok != "$":
                    continue
                parts = []
                j = i + 1
                while j < len(tokens) and _NUMBER_TOKEN_RE.fullmatch(tokens[j]):
                    parts.append(tokens[j])
                    j += 1
                joined = "".join(parts)
                if joined in ("", "-"):
                    return 0.0
                return float(joined.replace(",", ""))
            return 0.0
    raise ValueError(f"Could not find line {label!r} in Balagan statement")


def _find_period(text: str, filename: str) -> tuple[datetime.date, datetime.date]:
    month_match = _MONTH_RE.search(filename)
    if not month_match:
        raise ValueError(f"Could not find a Spanish month name in filename {filename!r}")
    month = _MESES[month_match.group(0).lower()]

    year_match = _YEAR_RE.search(text)
    if not year_match:
        raise ValueError("Could not find a 'de YYYY' year in the statement's período line")
    year = int(year_match.group(1))

    last_day = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, 1), datetime.date(year, month, last_day)


@dataclass
class BalaganStatement:
    period_start: datetime.date
    period_end: datetime.date
    revenue: float
    net_income: float
    proportional_income: float
    participation_pct: float = PARTICIPATION_PCT

    @property
    def expenses(self) -> float:
        """Revenue minus net income — robust to the expense categories
        changing month to month (confirmed they do: June drops some
        Gastos de Mtto line items present in April/May)."""
        return self.revenue - self.net_income


def _validate(net_income: float, proportional_income: float) -> None:
    """Repartición por punto should be ~PARTICIPATION_PCT of net_income —
    both are independently parsed from the same statement, so this catches
    a bad extraction even though it isn't a "printed total" in the BBVA
    sense. Tolerance is $1, not a cent: verified against 3 real months
    that "Repartición por punto" is itself printed pre-rounded to whole
    pesos (2,390 / 5,187 / 3,002), so up to ~$0.50 of drift from the exact
    percentage is expected, not an error."""
    expected = net_income * PARTICIPATION_PCT
    if abs(proportional_income - expected) > 1.0:
        raise ValueError(
            "Balagan statement does not reconcile: Repartición por punto "
            f"{proportional_income} is not ~{PARTICIPATION_PCT:.0%} of net_income {net_income} "
            f"(expected ~{expected:.2f})"
        )


def parse_balagan_statement(path: str) -> BalaganStatement:
    with pdfplumber.open(path) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    period_start, period_end = _find_period(text, path)
    revenue = _find_amount(text, "Total de ingresos.")
    net_income = _find_amount(text, "Utilidad de operación despues de impuestos")
    proportional_income = _find_amount(text, "Repartición por punto")
    _validate(net_income, proportional_income)

    return BalaganStatement(
        period_start=period_start,
        period_end=period_end,
        revenue=revenue,
        net_income=net_income,
        proportional_income=proportional_income,
    )
