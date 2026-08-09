"""Parser for Shareworks/Morgan Stanley quarterly ESPP statements
(Coca-Cola stock plan).

Real filenames are already clean and consistent: "Quarterly Statement
MM_DD_YYYY.pdf" (the date is the period's closing date). Two sections
matter, both on plain extracted text (no encryption, no layout tricks
needed):

1. "Share Purchase and Holdings Summary": Opening/Closing Value as of
   two dates, giving the period and the closing Total Account Value.
2. "SHARE PURCHASE AND HOLDINGS" transaction table: mixed row shapes
   depending on Activity Type (Payroll Credit has 1 trailing number,
   Buy/Dividend Reinvested have qty+price+2 amounts, Release has only
   qty+price and no dollar figure at all, etc).

The brief expected an explicit company-match cash line; the real
transaction history doesn't have one. What it does have: "Payroll
Credit" (the employee's own payroll-deducted ESPP contribution, verified
across two quarters to sum exactly to the quarter's closing Cash Value)
and "Release" (RSU-style vesting with a quantity and a price but no
dollar amount — shares becoming available, not a cash event). Absent a
literal matching-contribution line, company_match is modeled as the
market value of vested shares (Release quantity × price) — the closest
real analog to an employer contribution in this data. This is a
judgment call, flagged here rather than silently assumed.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

import pdfplumber

_AS_OF_RE = re.compile(r"\(as of (\d{1,2})/(\d{1,2})/(\d{2,4})\)")
_TOTAL_ACCOUNT_VALUE_RE = re.compile(
    r"Total Account Value\s+\$?([\d,]+\.\d+)\s+\$?([\d,]+\.\d+)"
)
_ROW_RE = re.compile(r"^(\d{1,2}/\d{1,2}/\d{2})\s+(.+)$")
_MONEY_RE = re.compile(r"\(?\$?(-?[\d,]+\.\d+)\)?")


def _parse_year(text: str) -> int:
    year = int(text)
    return year if year > 100 else 2000 + year


def _parse_money(text: str) -> float:
    negative = text.startswith("(") and text.endswith(")")
    value = float(text.strip("()$").replace(",", ""))
    return -value if negative else value


@dataclass
class ShareworksStatement:
    period_start: datetime.date
    period_end: datetime.date
    own_contribution: float
    """Sum of 'Payroll Credit' transactions."""
    vested_units: float
    """Sum of 'Release' quantities."""
    company_match: float
    """Market value of vested units (see module docstring for rationale)."""
    market_value: float
    """Closing Total Account Value."""


def _parse_period(text: str) -> tuple[datetime.date, datetime.date]:
    matches = _AS_OF_RE.findall(text)
    if len(matches) < 2:
        raise ValueError("Could not find both 'as of' dates in Shareworks statement")
    (m1, d1, y1), (m2, d2, y2) = matches[0], matches[1]
    return (
        datetime.date(_parse_year(y1), int(m1), int(d1)),
        datetime.date(_parse_year(y2), int(m2), int(d2)),
    )


def _parse_market_value(text: str) -> float:
    m = _TOTAL_ACCOUNT_VALUE_RE.search(text)
    if not m:
        raise ValueError("Could not find 'Total Account Value' in Shareworks statement")
    return float(m.group(2).replace(",", ""))


def _parse_transactions(text: str) -> tuple[float, float, float]:
    own_contribution = 0.0
    vested_units = 0.0
    company_match = 0.0

    for line in text.splitlines():
        m = _ROW_RE.match(line.strip())
        if not m:
            continue
        rest = m.group(2)

        if "Payroll Credit" in rest:
            amounts = _MONEY_RE.findall(rest)
            if amounts:
                own_contribution += _parse_money(amounts[-1])
        elif "Release" in rest:
            tokens = rest.replace("Release", "").split()
            if len(tokens) >= 2:
                qty, price = float(tokens[0]), float(tokens[1].lstrip("$"))
                vested_units += qty
                company_match += qty * price

    return own_contribution, vested_units, company_match


def parse_shareworks_statement(path: str) -> ShareworksStatement:
    with pdfplumber.open(path) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    period_start, period_end = _parse_period(text)
    market_value = _parse_market_value(text)
    own_contribution, vested_units, company_match = _parse_transactions(text)

    return ShareworksStatement(
        period_start=period_start,
        period_end=period_end,
        own_contribution=own_contribution,
        vested_units=vested_units,
        company_match=company_match,
        market_value=market_value,
    )
