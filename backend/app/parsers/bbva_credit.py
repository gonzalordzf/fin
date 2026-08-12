"""Parser for BBVA credit card ("TARJETA PLATINUM BBVA") statement PDFs.

Distinct account and distinct statement format from `parsers/bbva.py`
(BBVA's checking/débito account) — this one is not password-protected, and
its transaction table ("CARGOS,COMPRAS Y ABONOS REGULARES(NO A MESES)") has
only 4 columns (fecha de la operación, fecha de cargo, descripción, monto)
with an explicit +/- sign token before each amount, so rows are found by
that sign token rather than by column x-position like the débito parser.

Sign convention: the statement prints "+" for a cargo (a purchase — money
you owe more of) and "-" for an abono (a payment reducing what you owe).
That's inverted from this project's convention (negative = gasto, positive
= ingreso/abono) — same situation as AMEX/Revolut's raw sign, per
CLAUDE.md — so it's flipped here: cargo → negative, abono/pago → positive.
A card payment nets out as a self-transfer (see app/rules.py's
"BMOVIL.PAGO TDC" rule to "Pago de Tarjeta de Crédito"), so this doesn't
double the cargo side into spend.

Every statement prints "TOTAL CARGOS" / "TOTAL ABONOS" for the parsed
table (validated against the summed transaction rows), plus a separate
RESUMEN block (Adeudo del periodo anterior, Cargos regulares, Cargos
compras a meses, Monto de intereses/comisiones, IVA, Pagos y abonos, Pago
para no generar intereses) that must reconcile on its own:
adeudo_anterior + cargos_regulares + cargos_meses + intereses + comisiones
+ iva - abonos == pago_no_intereses. Per docs/04-gotchas.md, a statement
that doesn't reconcile stops and says so; it never gets estimated.

Two real wrinkles, both confirmed against actual statements:

- **"Meses sin intereses" enrollment row.** The regular table prints the
  *full* purchase price as its own cargo row the month a "meses sin
  intereses" plan starts (e.g. "MERPAGO*PBG A 03 MESES S/I +$9,082.00"),
  separately from the monthly installment charge ("01 DE 03 MERPAGO*PBG
  +$3,028.00") — and RESUMEN's own arithmetic confirms the enrollment
  row never actually increases what's owed (only the installment does).
  Recording both as real spend would double-count the purchase over its
  lifetime and permanently inflate this account's balance by the full
  price. The enrollment row is still needed to validate against the
  table's printed TOTAL CARGOS (which does include it), but it's dropped
  before returning — real installment charges are kept.
- **Interest/commissions are summary-only.** When a balance carries
  (confirmed on real statements — this card doesn't always pay in full),
  "Monto de intereses"/"Monto de comisiones"/"IVA de intereses y
  comisiones" increase what's owed but never appear as their own row in
  the movements table. A synthetic transaction is added for their sum so
  the account's running balance (and Gastos) reflect the real cost
  instead of silently under-counting it.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field

import pdfplumber

_DATE_RE = re.compile(r"^(\d{2})-([a-zé]{3})-(\d{4}|\d{2})$", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"^\$[\d,]+\.\d{2}$")

_MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}

# Statements before ~2025 print 2-digit years ("08-jun-24"); 2025+ ones
# print 4-digit years ("08-jul-2026") — both real, confirmed across the
# account's history, not a single fixed template.
_DATE_TOKEN = r"\d{2}-[a-zé]{3}-(?:\d{4}|\d{2})"
_PERIODO_RE = re.compile(
    rf"Periodo:\s*({_DATE_TOKEN})\s+al\s+({_DATE_TOKEN})", re.IGNORECASE
)
_CORTE_RE = re.compile(rf"Fecha de corte:\s*({_DATE_TOKEN})", re.IGNORECASE)
_LIMITE_PAGO_RE = re.compile(
    rf"Fecha límite de pago:\d*\s+\w+,\s*({_DATE_TOKEN})", re.IGNORECASE
)
_ADEUDO_ANTERIOR_RE = re.compile(r"Adeudo del periodo anterior\s+\$?([\d,]+\.\d{2})")
_CARGOS_REGULARES_RE = re.compile(r"Cargos regulares \(no a meses\)\s+\+\s*\$?([\d,]+\.\d{2})")
_CARGOS_MESES_RE = re.compile(r"Cargos compras a meses \(capital\)\d*\s+\+\s*\$?([\d,]+\.\d{2})")
_MONTO_INTERESES_RE = re.compile(r"Monto de intereses\d*\s+\+\s*\$?([\d,]+\.\d{2})")
_MONTO_COMISIONES_RE = re.compile(r"Monto de comisiones\s+\+\s*\$?([\d,]+\.\d{2})")
_IVA_RE = re.compile(r"IVA de intereses y comisiones\s+\+\s*\$?([\d,]+\.\d{2})")
_PAGOS_ABONOS_RE = re.compile(r"Pagos y abonos\s+-\s*\$?([\d,]+\.\d{2})")
_PAGO_NO_INTERESES_RE = re.compile(r"Pago para no generar intereses:\d*\s+\$?([\d,]+\.\d{2})")
_TOTAL_CARGOS_RE = re.compile(r"TOTAL CARGOS\s+\$?([\d,]+\.\d{2})")
_TOTAL_ABONOS_RE = re.compile(r"TOTAL ABONOS\s+-?\$?([\d,]+\.\d{2})")

# The one-time "meses sin intereses" enrollment row — "MERPAGO*PBG A 03
# MESES S/I" — as opposed to a monthly installment row ("01 DE 03
# MERPAGO*PBG"), which doesn't match this and is kept as real spend.
_MESES_ENROLLMENT_RE = re.compile(r"\bA\s+\d{2}\s+MESES\b", re.IGNORECASE)

_INTEREST_CATEGORY_DESCRIPTION = "Intereses y comisiones del periodo"

# A late-payment fee is both itemized as its own cargo row ("PENALIZACION
# POR PAGO TARDIO") AND reported in RESUMEN's "Monto de comisiones" — but
# unlike every other itemized cargo, BBVA's own printed TOTAL CARGOS for
# the table excludes it (confirmed: summing every *other* row on a real
# statement lands exactly on TOTAL CARGOS, off by precisely this row's
# amount). Treated as the one known case of an itemized row that's also
# reflected in the comisiones/intereses summary bucket, so it's excluded
# from the table-total check and from the synthetic interest/fees row
# (once, not twice) rather than raising on every future late fee.
_KNOWN_ITEMIZED_FEE_RE = re.compile(r"PENALIZACION", re.IGNORECASE)

_ROW_TOLERANCE = 3.0


@dataclass
class BBVACreditTransaction:
    operation_date: datetime.date
    charge_date: datetime.date
    description: str
    amount: float
    """Signed per project convention: negative = cargo (gasto), positive =
    abono/pago (reduces what's owed)."""
    row_index: int
    raw_lines: list[str] = field(default_factory=list)
    is_meses_enrollment: bool = False
    """The one-time full-price row a 'meses sin intereses' plan starts
    with — counted toward the table's printed TOTAL CARGOS for validation,
    but excluded from the returned transactions (see module docstring)."""


@dataclass
class BBVACreditStatement:
    transactions: list[BBVACreditTransaction]
    period_start: datetime.date
    period_end: datetime.date
    cutoff_date: datetime.date
    payment_due_date: datetime.date
    previous_balance: float
    new_balance: float
    """"Pago para no generar intereses" — the regular (no-meses) balance
    owed at the end of this period."""


def _parse_date_token(token: str) -> datetime.date:
    m = _DATE_RE.match(token)
    if not m:
        raise ValueError(f"Not a date token: {token!r}")
    day, mon_abbr, year = m.groups()
    year_num = int(year) if len(year) == 4 else 2000 + int(year)
    return datetime.date(year_num, _MESES[mon_abbr.lower()], int(day))


def _money(text: str) -> float:
    return float(text.replace("$", "").replace(",", ""))


def _find_summary(text: str) -> dict:
    patterns = {
        "period_start": _PERIODO_RE,
        "cutoff_date": _CORTE_RE,
        "payment_due_date": _LIMITE_PAGO_RE,
        "previous_balance": _ADEUDO_ANTERIOR_RE,
        "cargos_regulares": _CARGOS_REGULARES_RE,
        "cargos_meses": _CARGOS_MESES_RE,
        "monto_intereses": _MONTO_INTERESES_RE,
        "monto_comisiones": _MONTO_COMISIONES_RE,
        "iva": _IVA_RE,
        "pagos_abonos": _PAGOS_ABONOS_RE,
        "new_balance": _PAGO_NO_INTERESES_RE,
    }
    found = {}
    missing = []
    for name, pattern in patterns.items():
        m = pattern.search(text)
        if m is None:
            missing.append(name)
        else:
            found[name] = m
    if missing:
        raise ValueError(
            "Could not find expected fields in BBVA TDC statement summary: "
            + ", ".join(missing)
        )
    return found


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


def parse_bbva_credit_statement(path: str) -> BBVACreditStatement:
    transactions: list[BBVACreditTransaction] = []
    full_text_parts: list[str] = []
    pending: BBVACreditTransaction | None = None
    in_table = False
    table_done = False

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            full_text_parts.append(page_text)
            if table_done:
                continue

            words = page.extract_words()
            for line_words in _group_lines(words):
                joined = " ".join(w["text"] for w in line_words)
                if "CARGOS,COMPRAS" in joined and "REGULARES" in joined:
                    in_table = True
                    continue
                if joined.startswith("TOTAL CARGOS") or joined.startswith("TOTAL ABONOS"):
                    table_done = True
                    in_table = False
                    continue
                if not in_table:
                    continue

                first, second = line_words[0]["text"], line_words[1]["text"] if len(line_words) > 1 else ""
                if _DATE_RE.match(first) and _DATE_RE.match(second):
                    if pending is not None:
                        transactions.append(pending)
                    operation_date = _parse_date_token(first)
                    charge_date = _parse_date_token(second)

                    rest = line_words[2:]
                    desc_words: list[str] = []
                    sign: str | None = None
                    amount: float | None = None
                    for i, w in enumerate(rest):
                        # A bare "+"/"-" only counts as the amount's sign
                        # when the very next token is an actual amount — a
                        # merchant name can itself contain a literal hyphen
                        # word token (real case: "HARD CLUB - TURISMO DE"),
                        # which isn't the sign and must stay in the
                        # description instead of ending the scan early.
                        if (
                            w["text"] in ("+", "-")
                            and i + 1 < len(rest)
                            and _AMOUNT_RE.match(rest[i + 1]["text"])
                        ):
                            sign = w["text"]
                            amount = _money(rest[i + 1]["text"])
                            break
                        desc_words.append(w["text"])
                    if sign is None or amount is None:
                        raise ValueError(
                            f"Could not find a signed amount on transaction row: {joined!r}"
                        )
                    raw_signed = amount if sign == "+" else -amount
                    description = " ".join(desc_words)
                    pending = BBVACreditTransaction(
                        operation_date=operation_date,
                        charge_date=charge_date,
                        description=description,
                        amount=-raw_signed,
                        row_index=len(transactions),
                        raw_lines=[joined],
                        is_meses_enrollment=bool(_MESES_ENROLLMENT_RE.search(description)),
                    )
                elif pending is not None:
                    pending.raw_lines.append(joined)

        if pending is not None:
            transactions.append(pending)

    full_text = "\n".join(full_text_parts)
    summary = _find_summary(full_text)

    period_start_m = _PERIODO_RE.search(full_text)
    period_start = _parse_date_token(period_start_m.group(1))
    period_end = _parse_date_token(period_start_m.group(2))
    cutoff_date = _parse_date_token(summary["cutoff_date"].group(1))
    payment_due_date = _parse_date_token(summary["payment_due_date"].group(1))
    previous_balance = _money(summary["previous_balance"].group(1))
    cargos_regulares = _money(summary["cargos_regulares"].group(1))
    cargos_meses = _money(summary["cargos_meses"].group(1))
    monto_intereses = _money(summary["monto_intereses"].group(1))
    monto_comisiones = _money(summary["monto_comisiones"].group(1))
    iva = _money(summary["iva"].group(1))
    pagos_abonos = _money(summary["pagos_abonos"].group(1))
    new_balance = _money(summary["new_balance"].group(1))

    total_cargos_m = _TOTAL_CARGOS_RE.search(full_text)
    total_abonos_m = _TOTAL_ABONOS_RE.search(full_text)
    if total_cargos_m is None or total_abonos_m is None:
        raise ValueError("Could not find TOTAL CARGOS / TOTAL ABONOS in BBVA TDC statement table")
    total_cargos = _money(total_cargos_m.group(1))
    total_abonos = _money(total_abonos_m.group(1))

    # Validated against ALL parsed rows, including a meses-enrollment one —
    # that's what the table's own printed totals include. A known-itemized
    # fee (e.g. a late-payment penalty) is excluded: BBVA's own TOTAL
    # CARGOS excludes it too, even though the row is listed above it.
    itemized_fee_amount = round(
        sum(-t.amount for t in transactions if t.amount < 0 and _KNOWN_ITEMIZED_FEE_RE.search(t.description)),
        2,
    )
    parsed_cargos = round(
        sum(-t.amount for t in transactions if t.amount < 0) - itemized_fee_amount, 2
    )
    parsed_abonos = round(sum(t.amount for t in transactions if t.amount > 0), 2)

    errors = []
    if abs(parsed_cargos - total_cargos) > 0.01:
        errors.append(f"cargos: parsed {parsed_cargos} vs printed TOTAL CARGOS {total_cargos}")
    if abs(parsed_abonos - total_abonos) > 0.01:
        errors.append(f"abonos: parsed {parsed_abonos} vs printed TOTAL ABONOS {total_abonos}")
    # RESUMEN's own arithmetic, independent of the transaction rows: this
    # is what actually determines next period's previous_balance, and it's
    # the only reconciliation that stays correct whether or not a meses
    # enrollment or interest/commissions appear this period.
    reconciled = round(
        previous_balance + cargos_regulares + cargos_meses + monto_intereses + monto_comisiones
        + iva - pagos_abonos,
        2,
    )
    if abs(reconciled - new_balance) > 0.01:
        errors.append(
            f"balance: adeudo_anterior {previous_balance} + cargos_regulares {cargos_regulares} + "
            f"cargos_meses {cargos_meses} + intereses {monto_intereses} + comisiones {monto_comisiones} "
            f"+ iva {iva} - abonos {pagos_abonos} = {reconciled}, but 'Pago para no generar intereses' "
            f"prints {new_balance}"
        )
    if errors:
        raise ValueError(
            "BBVA TDC statement does not reconcile against its own printed totals: "
            + "; ".join(errors)
        )

    real_transactions = [t for t in transactions if not t.is_meses_enrollment]

    # Only the portion of intereses/comisiones/iva not already itemized as
    # its own row (e.g. a late fee) needs a synthetic transaction — adding
    # the full RESUMEN amount on top of an already-itemized fee row would
    # double it.
    interest_and_fees = round(monto_intereses + monto_comisiones + iva - itemized_fee_amount, 2)
    if interest_and_fees < 0:
        raise ValueError(
            f"BBVA TDC statement: itemized fee {itemized_fee_amount} exceeds RESUMEN's "
            f"intereses+comisiones+iva ({monto_intereses + monto_comisiones + iva}) — "
            "the known-fee assumption doesn't hold for this statement, needs review."
        )
    if interest_and_fees:
        real_transactions.append(
            BBVACreditTransaction(
                operation_date=cutoff_date,
                charge_date=cutoff_date,
                description=_INTEREST_CATEGORY_DESCRIPTION,
                amount=-interest_and_fees,
                row_index=len(transactions),
                raw_lines=[
                    f"Monto de intereses={monto_intereses} Monto de comisiones={monto_comisiones} "
                    f"IVA={iva} itemized_fee_amount={itemized_fee_amount} (synthetic row for the "
                    "portion not itemized in the movements table)"
                ],
            )
        )

    return BBVACreditStatement(
        transactions=real_transactions,
        period_start=period_start,
        period_end=period_end,
        cutoff_date=cutoff_date,
        payment_due_date=payment_due_date,
        previous_balance=previous_balance,
        new_balance=new_balance,
    )
