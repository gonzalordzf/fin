"""Parser for AMEX's own PDF "Estado de Cuenta" (used for statements older than
~2 years, which AMEX's CSV export tool doesn't cover — see app/parsers/amex.py
for the CSV path that handles everything more recent).

Verified against 4 real statements spanning Jan 2023-Aug 2024 (2023-01-03,
2023-08-03, 2024-03-03, 2024-08-03), word-level x/y-coordinate extraction via
pdfplumber — naive extract_text() interleaves the transaction table with a
completely separate right-column "correspondence address" block that happens
to share vertical position with table/footer rows on some pages, corrupting
line order if you don't filter by x-position (see _TABLE_X0_MAX /
_ADDRESS_BLOCK_X0_RANGE below).

Real structure, confirmed against the 4 samples:
- Two different card numbers appear across statements for the same person
  (e.g. "3766-695614-92008" on some months, "3766-695614-94004" on others,
  "3766-695614-92024" appears once in passing text on a Puntos-Premier page
  of a statement whose actual Tarjetahabiente/transaction card is 94004) —
  a card renewal/product-switch, not a different person. All of it lands in
  the single existing "AMEX" Account; card number is captured for
  traceability only, never used for routing or dedup.
- The transaction table ("Fecha y Detalle de las operaciones ... Importe en
  MN.") can span multiple pages; a page continues the table if it repeats
  that header, otherwise (once "Puntos Premier..." or similar starts) the
  table has ended for good — confirmed: every real statement's non-table
  pages come strictly after its table pages, never interleaved.
- Each transaction line has: day + "de"+MonthName (fused with no space in
  the source, e.g. "deJulio" — pdfplumber emits it as one word UNLESS the
  line wraps, in which case "de" and the month land on separate lines: this
  actually happened in the Jan-2023 statement across a PAGE break, splitting
  one transaction's date+reference away from its own description+amount by
  an entire page. Handled by treating "this line carries an amount" as the
  ONLY signal for "start a new transaction" (not "this line has a date") —
  date/reference are filled in opportunistically from whatever line(s)
  carry them, before or after, same pending transaction either way.
- A payment or refund/credit prints its amount as a plain positive number
  with a "CR" suffix on the *next* line (e.g. "PAGO RECIBIDO, GRACIAS
  47,107.20" then "CR"), confirmed against two real "PAGO RECIBIDO" lines
  whose CR-tagged amounts sum exactly to the statement's own "Créditos"
  total in the printed reconciliation equation. A plain charge has no CR
  suffix. So: CR present -> positive in our schema (money in / reduces what
  you owe, mirrors the CSV path's negative Importe for a credit); no CR ->
  negative (expense), matching app/parsers/amex.py's CSV convention exactly
  (that parser negates positive-CSV-Importe charges the same way).
- Every statement prints a literal reconciliation equation "Saldo Anterior -
  Créditos + Cargos = Saldo al corte [Saldo a Pagar]" — parse_amex_pdf_statement()
  reconciles parsed cargos/créditos against these two printed figures before
  returning, same "cuadra al centavo" discipline as bbva.py/revolut.py.
- The reference used for dedup is whatever follows "/REF" on a transaction's
  continuation line (e.g. "/REFWDTOMXM1ARAH" -> "WDTOMXM1ARAH"). Confirmed
  against real CSV/PDF data for the same real transaction that this is a
  *different* value and format than the CSV's own "Referencia" column (CSV:
  quoted 'AT26...' banking-network reference; PDF: a merchant/processor
  auth code) — the two id spaces never collide, but also never match for a
  transaction present in both a CSV and a PDF, so cross-format dedup can't
  rely on external_ref alone (see app/importers/amex.py for the (date,
  amount) fallback this requires).
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field

import pdfplumber

_MESES_FULL = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12,
}
_MESES_ABBR = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}

_DAY_RE = re.compile(r"^\d{1,2}$")
_AMOUNT_RE = re.compile(r"^-?[\d,]+\.\d{2}$")
_CARD_NUMBER_RE = re.compile(r"^\d{4}-\d{6}-\d{5}$")
_CORTE_DATE_RE = re.compile(r"^(\d{2})-([A-Za-zé]{3})-(\d{4})$")
_DIAS_PERIODO_RE = re.compile(r"D[ií]as del periodo:\s*(\d+)\s*d[ií]as")
_RECON_RE = re.compile(
    r"([\d,]+\.\d{2})\s*-\s*([\d,]+\.\d{2})\s*\+\s*([\d,]+\.\d{2})\s*=\s*([\d,]+\.\d{2})"
)

# The amount column starts around x0=485 (the "Importe" header word itself
# sits at x0=485.3 in every sample checked); everything at or past 480 in
# the transaction-table region is the Importe column, never description
# text (descriptions end by x0~300 at the latest).
_AMOUNT_COL_X0_MIN = 480.0

# A right-column "correspondence address" block (independent of the
# transaction table) sits at x0 in roughly [300, 460) on pages that also
# show the "Saldo a Pagar" footer — its rows can share a `top` coordinate
# with table/footer rows on the left, which would corrupt naive
# top-based line-grouping. There is no legitimate table or footer content
# in this x-range (descriptions end ~300, Importe starts ~480), so words
# in this band are dropped before grouping into lines.
_ADDRESS_BLOCK_X0_RANGE = (298.0, 480.0)

_SKIP_LINE_FIRST_WORDS = {
    "Nuevos", "Número", "Saldo", "Fecha", "Período", "Días", "Puntos",
    "GONZALO", "CLL", "Total", "Estado", "Tarjetahabiente", "Página", "Este",
    "Para", "Favor", "americanexpress.com.mx",
}

_ROW_TOLERANCE = 3.0


@dataclass
class AmexPdfTransaction:
    date: datetime.date
    description: str
    amount: float
    """Signed: negative = charge (expense), positive = credit/refund/payment,
    same convention as app/parsers/amex.py's CSV path."""
    external_ref: str | None
    row_index: int
    raw_lines: list[str] = field(default_factory=list)


@dataclass
class AmexPdfStatement:
    transactions: list[AmexPdfTransaction]
    period_start: datetime.date
    period_end: datetime.date
    card_numbers: set[str]
    saldo_anterior: float
    creditos: float
    cargos: float


@dataclass
class _Pending:
    day: int | None
    month_name: str | None
    description_words: list[str]
    amount: float | None
    is_credit: bool
    external_ref: str | None
    row_index: int
    raw_lines: list[str] = field(default_factory=list)


def _group_lines(words: list[dict]) -> list[list[dict]]:
    filtered = [
        w for w in words
        if not (_ADDRESS_BLOCK_X0_RANGE[0] <= w["x0"] < _ADDRESS_BLOCK_X0_RANGE[1])
    ]
    lines: list[list[dict]] = []
    for w in sorted(filtered, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= _ROW_TOLERANCE:
            lines[-1].append(w)
        else:
            lines.append([w])
    for line in lines:
        line.sort(key=lambda w: w["x0"])
    return lines


def _has_table_header(words: list[dict]) -> bool:
    texts = {w["text"] for w in words}
    return "Importe" in texts and "operaciones" in texts


def _find_period(words: list[dict]) -> tuple[datetime.date, datetime.date] | None:
    """Returns (period_end, next_period_end) from the two "...de Corte"
    dates in the header, in document reading order (confirmed: "Fecha de
    Corte" always prints before "Siguiente Fecha de Corte" on the same
    row)."""
    dates = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        m = _CORTE_DATE_RE.match(w["text"])
        if m:
            day, mon_abbr, year = m.groups()
            month = _MESES_ABBR.get(mon_abbr.lower())
            if month is not None:
                dates.append(datetime.date(int(year), month, int(day)))
        if len(dates) == 2:
            return dates[0], dates[1]
    return None


def _find_card_number(words: list[dict]) -> str | None:
    for i, w in enumerate(words):
        if w["text"] == "Tarjetahabiente":
            for w2 in words[i : i + 6]:
                if _CARD_NUMBER_RE.match(w2["text"]):
                    return w2["text"]
    return None


def _resolve_year(month_name: str, period_start: datetime.date, period_end: datetime.date) -> int:
    month = _MESES_FULL[month_name.lower()]
    if month == period_start.month:
        return period_start.year
    if month == period_end.month:
        return period_end.year
    # An AMEX billing period spans at most two calendar months in every
    # sample seen; fall back to the closer one if that's ever not true.
    return period_end.year


def _is_bare_month(text: str) -> bool:
    return text.lower() in _MESES_FULL


def _match_de_month(text: str) -> tuple[str, str] | None:
    """Matches a "de<Month>" token, which pdfplumber sometimes emits fused
    with the start of the following merchant name and no separating space —
    confirmed on real "Noviembre" statements (e.g. "deNoviembreRAPPI*RAPPI",
    "deNoviembreAPPLE"), apparently because that particular month name is
    long enough to tighten the kerning against whatever text follows it in
    the source PDF. Matches the month as a case-insensitive prefix and
    returns (month_name, leftover_suffix) so the leftover — the actual
    start of the description — isn't silently dropped."""
    lower = text.lower()
    if not lower.startswith("de"):
        return None
    rest = lower[2:]
    for month in _MESES_FULL:
        if rest.startswith(month):
            return month, text[2 + len(month) :]
    return None


def _new_pending(row_index: int) -> _Pending:
    return _Pending(
        day=None, month_name=None, description_words=[], amount=None,
        is_credit=False, external_ref=None, row_index=row_index,
    )


def _consume_leading_date(texts: list[str]) -> tuple[int | None, str | None, int, str]:
    """Returns (day, month_name, tokens_consumed, leftover_suffix) from the
    start of a line. leftover_suffix is any description text fused onto the
    "de<Month>" token (see _match_de_month)."""
    if not texts or not _DAY_RE.match(texts[0]):
        return None, None, 0, ""
    day = int(texts[0])
    if len(texts) > 1:
        m = _match_de_month(texts[1])
        if m:
            month_name, leftover = m
            return day, month_name, 2, leftover
        if texts[1] == "de":
            return day, None, 2, ""
    return day, None, 1, ""


def _apply_row(pending: _Pending, line_words: list[dict], *, is_start: bool) -> None:
    texts = [w["text"] for w in line_words]
    consumed = 0
    leftover = ""
    if is_start:
        day, month_name, consumed, leftover = _consume_leading_date(texts)
        if day is not None:
            pending.day = day
        if month_name is not None:
            pending.month_name = month_name
        if leftover:
            pending.description_words.append(leftover)
    else:
        # Backfill day/month from a continuation line only if still unknown
        # — this is what recovers a transaction whose date+reference were
        # pushed to an orphan block by a page break (see module docstring).
        if pending.day is None:
            day, month_name, n, _leftover = _consume_leading_date(texts)
            if day is not None:
                pending.day = day
                pending.month_name = month_name
                consumed = n
        elif pending.month_name is None:
            for t in texts:
                if _is_bare_month(t):
                    pending.month_name = t
                    break

    for w in line_words[consumed:]:
        text = w["text"]
        if text == "CR" and w["x0"] >= _AMOUNT_COL_X0_MIN:
            pending.is_credit = True
        elif w["x0"] >= _AMOUNT_COL_X0_MIN and _AMOUNT_RE.match(text):
            pending.amount = float(text.replace(",", ""))
        elif text.startswith("/REF"):
            if pending.external_ref is None:
                pending.external_ref = text[4:]
        elif text.startswith("RFC"):
            pass  # merchant/processor RFC, not needed
        elif is_start:
            pending.description_words.append(text)

    pending.raw_lines.append(" ".join(texts))


def _line_has_amount(line_words: list[dict]) -> bool:
    return any(
        w["x0"] >= _AMOUNT_COL_X0_MIN and _AMOUNT_RE.match(w["text"])
        for w in line_words
    )


def _finalize(
    pending: _Pending, period_start: datetime.date, period_end: datetime.date,
    fallback_date: datetime.date | None,
) -> AmexPdfTransaction:
    if pending.day is not None and pending.month_name is not None:
        year = _resolve_year(pending.month_name, period_start, period_end)
        date = datetime.date(year, _MESES_FULL[pending.month_name.lower()], pending.day)
    elif fallback_date is not None:
        # Real gap seen once (Jan-2023 statement): a transaction's date+
        # reference landed in an orphan block that never actually carried
        # a day+month pair recoverable this way. cuadra-al-centavo totals
        # still hold either way (date isn't part of the reconciliation),
        # so fall back to the nearest known date rather than failing the
        # whole statement over one row's date field.
        date = fallback_date
    else:
        raise ValueError(
            f"AMEX PDF: transaction at row {pending.row_index} has no resolvable date "
            f"(desc={' '.join(pending.description_words)!r}) and no prior date to fall back to"
        )

    amount = pending.amount if pending.amount is not None else 0.0
    signed = amount if pending.is_credit else -amount
    return AmexPdfTransaction(
        date=date,
        description=" ".join(pending.description_words),
        amount=signed,
        external_ref=pending.external_ref,
        row_index=pending.row_index,
        raw_lines=list(pending.raw_lines),
    )


def _validate(saldo_anterior: float, creditos: float, cargos: float, transactions: list[AmexPdfTransaction]) -> None:
    parsed_cargos = round(sum(-t.amount for t in transactions if t.amount < 0), 2)
    parsed_creditos = round(sum(t.amount for t in transactions if t.amount > 0), 2)
    errors = []
    if abs(parsed_cargos - cargos) > 0.01:
        errors.append(f"cargos: parsed {parsed_cargos} vs printed {cargos}")
    if abs(parsed_creditos - creditos) > 0.01:
        errors.append(f"créditos: parsed {parsed_creditos} vs printed {creditos}")
    if errors:
        raise ValueError(
            "AMEX PDF statement does not reconcile against its own printed "
            "totals: " + "; ".join(errors)
        )


def parse_amex_pdf_statement(path: str) -> AmexPdfStatement:
    transactions: list[AmexPdfTransaction] = []
    card_numbers: set[str] = set()
    period_start = period_end = None
    saldo_anterior = creditos = cargos = None
    last_resolved_date: datetime.date | None = None

    with pdfplumber.open(path) as pdf:
        pending: _Pending | None = None
        found_table = False

        for page in pdf.pages:
            words = page.extract_words()

            if period_start is None:
                period = _find_period(words)
                if period is not None:
                    period_end, next_period_end = period
                    text0 = page.extract_text() or ""
                    m = _DIAS_PERIODO_RE.search(text0)
                    if m:
                        period_start = period_end - datetime.timedelta(days=int(m.group(1)) - 1)
                    recon = _RECON_RE.search(text0)
                    if recon:
                        saldo_anterior = float(recon.group(1).replace(",", ""))
                        creditos = float(recon.group(2).replace(",", ""))
                        cargos = float(recon.group(3).replace(",", ""))

            card = _find_card_number(words)
            if card is not None:
                card_numbers.add(card)

            if not _has_table_header(words):
                if found_table:
                    break
                continue
            found_table = True

            for line_words in _group_lines(words):
                if not line_words:
                    continue
                first_text = line_words[0]["text"]
                if first_text in _SKIP_LINE_FIRST_WORDS:
                    continue

                if _line_has_amount(line_words):
                    if pending is not None:
                        finalized = _finalize(pending, period_start, period_end, last_resolved_date)
                        transactions.append(finalized)
                        last_resolved_date = finalized.date
                    pending = _new_pending(row_index=len(transactions))
                    _apply_row(pending, line_words, is_start=True)
                elif pending is not None:
                    _apply_row(pending, line_words, is_start=False)
                # else: stray non-table text before the first real
                # transaction row (shouldn't happen once found_table is
                # True, but nothing to attach it to if it did) — dropped.

        if pending is not None:
            finalized = _finalize(pending, period_start, period_end, last_resolved_date)
            transactions.append(finalized)

    if period_start is None or period_end is None:
        raise ValueError("Could not find 'Fecha de Corte' / period info in AMEX PDF statement")
    if saldo_anterior is None or creditos is None or cargos is None:
        raise ValueError("Could not find the printed reconciliation equation in AMEX PDF statement")

    _validate(saldo_anterior, creditos, cargos, transactions)

    return AmexPdfStatement(
        transactions=transactions,
        period_start=period_start,
        period_end=period_end,
        card_numbers=card_numbers,
        saldo_anterior=saldo_anterior,
        creditos=creditos,
        cargos=cargos,
    )
