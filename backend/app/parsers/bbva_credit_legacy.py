"""Parser for the older layout of BBVA credit card ("Tarjeta ORO BBVA")
statement PDFs that preceded the "nuevo estado de cuenta universal"
template `parsers/bbva_credit.py` handles (real statements confirmed
ene-2023 through jun-2024; BBVA switched statement templates starting the
jul-2024 statement — the card PRODUCT itself is "Tarjeta ORO BBVA" on
both templates, this isn't a card reissue).

Structurally a different template from the modern one: no explicit +/-
sign token before each amount — instead the movements table prints two
separate x-position columns, "IMPORTE CARGOS" (~x481-520pt) and "IMPORTE
ABONOS" (~x536+pt), so a row's cargo/abono classification is read from
which column its amount lands in, not a sign character. The page-0 summary
block is also laid out in three side-by-side columns (left ~x<192, mid
~x192-419, right ~x419+) that `pdfplumber`'s plain `extract_text()`
scrambles into a single interleaved line order — so this parser reads
`extract_words()` and reconstructs each column's own top-to-bottom text
stream by filtering on x0 before running any regex on it, rather than
matching against the page's flat text like the modern parser does.

Reconciliation is against "Saldo al Corte" (found in the mid column), not
"Pago para no generar Intereses" (right column) — confirmed by chaining
every one of the 18 real Tarjeta Oro statements available: only "Saldo al
Corte" always equals the next statement's own "Saldo Inicial del Periodo".
"Pago para no generar Intereses" is a *smaller* number whenever a "meses
sin intereses" annual-fee installment is active (it excludes the
not-yet-due installments), so chaining against it silently produces a
balance ~1 installment short.

Two wrinkles, both confirmed against real statements:

- **Annual-fee "X DE 03" installment disclosure rows.** When the card's
  annual fee is enrolled in a 3-month interest-free plan, the *first*
  month recognizes the *full* fee against what's owed (via the page-0
  "Comisiones Cobradas" field — confirmed: it's added in full to that
  month's RESUMEN reconciliation, not spread across months), while the
  movements table's own itemized row that same month only shows the first
  third ("01 DE 03 ANUALIDAD $X"), and the two following months separately
  itemize "02 DE 03 .../03 DE 03 ..." — purely as payment-schedule
  disclosure, not new debt (confirmed: BBVA's own printed "TOTAL IMPORTES"
  for the table excludes all of these rows, in every month they appear).
  Recording any of them as real spend would double-count the fee (already
  captured once via the synthetic interest/fees row below) or invent debt
  that was never actually charged that month. All three are dropped.
- **Interest/commissions are summary-only.** Same as the modern parser:
  "Comisiones Cobradas"/"Intereses Ordinarios (sin IVA)"/"Impuesto al
  Valor Agregado" increase what's owed but never appear as their own real
  transaction row (the installment disclosure rows above are the closest
  thing, and those are explicitly excluded). A synthetic transaction is
  added for their sum, dated at the cutoff, so the account's running
  balance and Gastos reflect the real cost.

A real, permanent gap: the Noviembre 2023 statement is missing — both the
"Noviembre 2023" and "Diciembre 2023" files in the connected Drive folder
are the exact same December PDF (confirmed via md5, 2026-08-12); the true
November statement was never uploaded and doesn't exist anywhere in the
connected Drive. `app/manual_data.py` bridges the resulting balance-chain
break with a documented net adjustment (Octubre 2023's real closing
balance vs. Diciembre 2023's real opening balance), and
`app/importers/bbva_credit.py` allowlists this one specific transition so
it doesn't raise like every other broken chain would.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field

import pdfplumber

from app.parsers.bbva_credit import BBVACreditStatement, BBVACreditTransaction

_DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{2})$")
_NUM_RE = re.compile(r"^[\d,]+\.\d{2}-?$")

# The right edge of the "IMPORTE CARGOS" column and the left edge of
# "IMPORTE ABONOS" sit roughly 480-575pt across every real statement
# checked; 520 falls cleanly in the gap between them (confirmed against
# all 18 real files — every cargo amount's x0 is under it, every abono's
# is over it).
_ABONO_X0_THRESHOLD = 520.0

# Column x-ranges for the page-0 summary block, confirmed by inspecting
# real word coordinates: labels/values naturally read in top-to-bottom
# order *within* a column once interleaving from the other columns is
# filtered out (see module docstring).
_LEFT_COL = (0, 192)
_MID_COL = (192, 419)
_RIGHT_COL = (419, 620)

# "01 DE 03 ANUALIDAD", "02 DE 03 ANUALIDAD", ... — see module docstring.
_INSTALLMENT_DISCLOSURE_RE = re.compile(r"^\d{2}\s+DE\s+\d{2}\b")

_INTEREST_CATEGORY_DESCRIPTION = "Intereses y comisiones del periodo"


def _money(text: str) -> float:
    return float(text.replace("$", "").replace(",", "").replace("-", ""))


def _parse_short_date(token: str) -> datetime.date:
    m = _DATE_RE.match(token)
    if not m:
        raise ValueError(f"Not a DD/MM/YY date token: {token!r}")
    day, month, year = (int(g) for g in m.groups())
    return datetime.date(2000 + year, month, day)


def _column_stream(words: list[dict], x_range: tuple[float, float]) -> str:
    x_min, x_max = x_range
    col_words = [w for w in words if x_min <= w["x0"] < x_max]
    col_words.sort(key=lambda w: (round(w["top"]), w["x0"]))
    return " ".join(w["text"] for w in col_words)


def _grab(pattern: str, text: str, field_name: str) -> str:
    m = re.search(pattern, text)
    if m is None:
        raise ValueError(f"Could not find {field_name!r} in BBVA Tarjeta Oro statement summary")
    return m.group(1)


def _find_summary(left: str, mid: str, right: str) -> dict[str, float | datetime.date]:
    previous_balance = _money(_grab(r"Saldo Inicial del Periodo\s+-?\$\s*([\d,]+\.\d{2})", mid, "previous_balance"))
    pagos = _money(_grab(r"Pagos:\s+-?\$\s*([\d,]+\.\d{2})", mid, "pagos"))
    otros_abonos = _money(_grab(r"Otros Abonos\s+\$\s*([\d,]+\.\d{2})", left, "otros_abonos"))
    # Deliberately not "Compras Diferidas ..." — the negative lookbehind
    # keeps this anchored to the plain "Compras +$" bucket that precedes it.
    compras = _money(_grab(r"(?<!Diferidas )Compras\s+\+\$\s*([\d,]+\.\d{2})", mid, "compras"))
    compras_diferidas = _money(_grab(r"Compras Diferidas\s+\+\$\s*([\d,]+\.\d{2})", mid, "compras_diferidas"))
    comisiones_cobradas = _money(_grab(r"Comisiones Cobradas\s+\+\$\s*([\d,]+\.\d{2})", mid, "comisiones_cobradas"))
    otros_cargos = _money(_grab(r"Otros Cargos\s+\+\$\s*([\d,]+\.\d{2})", mid, "otros_cargos"))
    intereses_ordinarios = _money(
        _grab(r"Intereses Ordinarios \(sin IVA\)\s+\+\$\s*([\d,]+\.\d{2})", mid, "intereses_ordinarios")
    )
    iva = _money(_grab(r"Impuesto al Valor Agregado\s+\+\$\s*([\d,]+\.\d{2})", mid, "iva"))
    saldo_al_corte = _money(_grab(r"Saldo al Corte\s+\$\s*([\d,]+\.\d{2})", mid, "saldo_al_corte"))

    cutoff_date = _parse_short_date(_grab(r"Fecha de Corte\s+(\d{2}/\d{2}/\d{2})", right, "cutoff_date"))
    payment_due_date = _parse_short_date(
        _grab(r"Fecha L[ií]mite de Pago\s+(\d{2}/\d{2}/\d{2})", right, "payment_due_date")
    )
    period_start_s, period_end_s = re.search(
        r"Periodo\s+Del\s+(\d{2}/\d{2}/\d{2})\s+al\s+(\d{2}/\d{2}/\d{2})", right
    ).groups()

    return {
        "previous_balance": previous_balance,
        "pagos": pagos,
        "otros_abonos": otros_abonos,
        "compras": compras,
        "compras_diferidas": compras_diferidas,
        "comisiones_cobradas": comisiones_cobradas,
        "otros_cargos": otros_cargos,
        "intereses_ordinarios": intereses_ordinarios,
        "iva": iva,
        "saldo_al_corte": saldo_al_corte,
        "cutoff_date": cutoff_date,
        "payment_due_date": payment_due_date,
        "period_start": _parse_short_date(period_start_s),
        "period_end": _parse_short_date(period_end_s),
    }


def parse_bbva_credit_legacy_statement(path: str) -> BBVACreditStatement:
    transactions: list[BBVACreditTransaction] = []

    with pdfplumber.open(path) as pdf:
        page0_words = pdf.pages[0].extract_words()
        left = _column_stream(page0_words, _LEFT_COL)
        mid = _column_stream(page0_words, _MID_COL)
        right = _column_stream(page0_words, _RIGHT_COL)
        summary = _find_summary(left, mid, right)

        in_table = False
        for page in pdf.pages:
            words = page.extract_words()
            rows: dict[int, list[dict]] = {}
            for w in words:
                rows.setdefault(round(w["top"]), []).append(w)
            for top in sorted(rows):
                line_words = sorted(rows[top], key=lambda w: w["x0"])
                joined = " ".join(w["text"] for w in line_words)
                if "Movimientos Efectuados" in joined:
                    in_table = True
                    continue
                if joined.startswith("TOTAL IMPORTES"):
                    in_table = False
                    continue
                if "Resumen Informativo de Beneficios" in joined:
                    in_table = False
                    continue
                if not in_table:
                    continue

                first = line_words[0]["text"]
                second = line_words[1]["text"] if len(line_words) > 1 else ""
                if not (_DATE_RE.match(first) and _DATE_RE.match(second)):
                    continue  # a payment-allocation continuation line (Iva:/Interes:/...), not a row

                operation_date = _parse_short_date(first)
                charge_date = _parse_short_date(second)

                amount: float | None = None
                amount_x0: float | None = None
                for i, w in enumerate(line_words):
                    if w["text"] == "$" and i + 1 < len(line_words) and _NUM_RE.match(line_words[i + 1]["text"]):
                        amount = _money(line_words[i + 1]["text"])
                        amount_x0 = line_words[i + 1]["x0"]
                        break
                if amount is None:
                    # A continuation line happened to start with two date-shaped
                    # tokens but never resolved to a real amount — shouldn't
                    # happen on a well-formed statement, so surface it loudly.
                    raise ValueError(f"Could not find an amount on transaction row: {joined!r}")

                desc_words = [
                    w["text"]
                    for w in line_words[2:]
                    if w["text"] != "$" and not _NUM_RE.match(w["text"])
                ]
                description = " ".join(desc_words)
                is_installment_disclosure = bool(_INSTALLMENT_DISCLOSURE_RE.match(description))
                signed = amount if amount_x0 >= _ABONO_X0_THRESHOLD else -amount
                transactions.append(
                    BBVACreditTransaction(
                        operation_date=operation_date,
                        charge_date=charge_date,
                        description=description,
                        amount=signed,
                        row_index=len(transactions),
                        raw_lines=[joined],
                        is_meses_enrollment=is_installment_disclosure,
                    )
                )

    cargos_regulares = summary["compras"] + summary["otros_cargos"]
    cargos_meses = summary["compras_diferidas"]
    monto_intereses = summary["intereses_ordinarios"]
    monto_comisiones = summary["comisiones_cobradas"]
    iva = summary["iva"]
    pagos_abonos = summary["pagos"] + summary["otros_abonos"]
    previous_balance = summary["previous_balance"]
    new_balance = summary["saldo_al_corte"]

    # Table-level check: every real (non-installment-disclosure) row summed
    # should equal RESUMEN's own cargos/abonos buckets — BBVA's own printed
    # "TOTAL IMPORTES" already excludes the installment-disclosure rows too
    # (confirmed across every real statement that has one), so this and the
    # printed total agree once those rows are dropped here.
    parsed_cargos = round(sum(-t.amount for t in transactions if t.amount < 0 and not t.is_meses_enrollment), 2)
    parsed_abonos = round(sum(t.amount for t in transactions if t.amount > 0 and not t.is_meses_enrollment), 2)
    expected_cargos = round(cargos_regulares + cargos_meses, 2)
    expected_abonos = round(pagos_abonos, 2)

    errors = []
    if abs(parsed_cargos - expected_cargos) > 0.02:
        errors.append(f"cargos: parsed {parsed_cargos} vs RESUMEN Compras+Otros Cargos+Compras Diferidas {expected_cargos}")
    if abs(parsed_abonos - expected_abonos) > 0.02:
        errors.append(f"abonos: parsed {parsed_abonos} vs RESUMEN Pagos+Otros Abonos {expected_abonos}")

    reconciled = round(
        previous_balance + cargos_regulares + cargos_meses + monto_intereses + monto_comisiones + iva - pagos_abonos,
        2,
    )
    if abs(reconciled - new_balance) > 0.02:
        errors.append(
            f"balance: Saldo Inicial {previous_balance} + Compras {summary['compras']} + Otros Cargos "
            f"{summary['otros_cargos']} + Compras Diferidas {cargos_meses} + Intereses Ordinarios "
            f"{monto_intereses} + Comisiones Cobradas {monto_comisiones} + IVA {iva} - Pagos "
            f"{summary['pagos']} - Otros Abonos {summary['otros_abonos']} = {reconciled}, but 'Saldo al "
            f"Corte' prints {new_balance}"
        )
    if errors:
        raise ValueError(
            "BBVA Tarjeta Oro statement does not reconcile against its own printed totals: "
            + "; ".join(errors)
        )

    real_transactions = [t for t in transactions if not t.is_meses_enrollment]

    interest_and_fees = round(monto_intereses + monto_comisiones + iva, 2)
    if interest_and_fees:
        real_transactions.append(
            BBVACreditTransaction(
                operation_date=summary["cutoff_date"],
                charge_date=summary["cutoff_date"],
                description=_INTEREST_CATEGORY_DESCRIPTION,
                amount=-interest_and_fees,
                row_index=len(transactions),
                raw_lines=[
                    f"Intereses Ordinarios={monto_intereses} Comisiones Cobradas={monto_comisiones} "
                    f"IVA={iva} (synthetic row — never itemized as its own row in the movements table)"
                ],
            )
        )

    return BBVACreditStatement(
        transactions=real_transactions,
        period_start=summary["period_start"],
        period_end=summary["period_end"],
        cutoff_date=summary["cutoff_date"],
        payment_due_date=summary["payment_due_date"],
        previous_balance=previous_balance,
        new_balance=new_balance,
    )
