"""Parser for Allianz OptiMaxx plus monthly "Estado de Cuenta" PDFs.

Clean text-based PDF, consistent filenames (Allianz_Optimax_YYYY-MM.pdf,
already standardized per the brief). Two sections matter:

1. "Resumen de Saldos de Inversión": one row per sub-portfolio (093BON
   Bono de Fidelidad, 093CDD Dinámico Dólares Comprometido, 093DDO
   Dinámico Dólares Inicial) — Aportaciones Acumuladas, Unidades,
   Valor de la Unidad, Monto (= Unidades × Valor, verified). This is
   the point-in-time valuation -> one HoldingSnapshot per sub-portfolio.
2. "Detalle de Aportaciones": per sub-portfolio, the contribution
   transactions posted that period (often zero — Bono de Fidelidad and
   one of the Dólares funds typically have none). Summed per
   sub-portfolio into net_contribution_period.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

import pdfplumber

_PERIOD_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s+al\s+(\d{2})/(\d{2})/(\d{4})")
_PORTFOLIO_ROW_RE = re.compile(
    r"^(\d{3}[A-Z]{3})\s+(.+?)\s+([\d,]+\.\d+)\s+([\d,]+\.\d+)\s+([\d,]+\.\d+)\s+([\d,]+\.\d+)$"
)
_PORTFOLIO_HEADER_RE = re.compile(r"^(\d{3}[A-Z]{3})\s+([A-Za-zÀ-ÿ\s]+)$")
_CONTRIBUTION_ROW_RE = re.compile(
    r"^(\d{2})/(\d{2})/(\d{4})\s+(.+?)\s+([\d,]+\.\d+)$"
)


@dataclass
class OptimaxSubPortfolio:
    code: str
    name: str
    contributions_accumulated: float
    units: float
    unit_value: float
    market_value: float
    net_contribution_period: float = 0.0


@dataclass
class OptimaxStatement:
    period_start: datetime.date
    period_end: datetime.date
    sub_portfolios: list[OptimaxSubPortfolio]


def _parse_money(text: str) -> float:
    return float(text.replace(",", ""))


def _parse_period(lines: list[str]) -> tuple[datetime.date, datetime.date]:
    for line in lines:
        m = _PERIOD_RE.search(line)
        if m:
            d1, m1, y1, d2, m2, y2 = m.groups()
            return (
                datetime.date(int(y1), int(m1), int(d1)),
                datetime.date(int(y2), int(m2), int(d2)),
            )
    raise ValueError("Could not find 'Fechas de Corte' period in Optimax statement")


def _parse_summary(lines: list[str]) -> dict[str, OptimaxSubPortfolio]:
    portfolios: dict[str, OptimaxSubPortfolio] = {}
    for line in lines:
        m = _PORTFOLIO_ROW_RE.match(line.strip())
        if m:
            code, name, aport, units, unit_value, monto = m.groups()
            portfolios[code] = OptimaxSubPortfolio(
                code=code,
                name=name.strip(),
                contributions_accumulated=_parse_money(aport),
                units=_parse_money(units),
                unit_value=_parse_money(unit_value),
                market_value=_parse_money(monto),
            )
    return portfolios


def _parse_contributions(lines: list[str], portfolios: dict[str, OptimaxSubPortfolio]) -> None:
    try:
        start = next(i for i, l in enumerate(lines) if "Detalle de Aportaciones" in l)
    except StopIteration:
        return

    current: OptimaxSubPortfolio | None = None
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if "Agente:" in stripped:
            # End of "2. Detalle de Aportaciones" — a later, differently
            # formatted "3.- Detalle Por Portafolio" section repeats the
            # same 093XXX headers with extra columns (Tipo de Cambio,
            # Unidades, ...) that would otherwise be misparsed as more
            # contribution rows.
            break
        header_m = _PORTFOLIO_HEADER_RE.match(stripped)
        if header_m and header_m.group(1) in portfolios:
            current = portfolios[header_m.group(1)]
            continue
        contrib_m = _CONTRIBUTION_ROW_RE.match(stripped)
        if contrib_m and current is not None:
            amount = _parse_money(contrib_m.group(5))
            current.net_contribution_period += amount


def parse_optimax_statement(path: str) -> OptimaxStatement:
    with pdfplumber.open(path) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    lines = text.splitlines()

    period_start, period_end = _parse_period(lines)
    portfolios = _parse_summary(lines)
    _parse_contributions(lines, portfolios)

    return OptimaxStatement(
        period_start=period_start,
        period_end=period_end,
        sub_portfolios=list(portfolios.values()),
    )
