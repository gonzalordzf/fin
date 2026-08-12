"""Facts the user provided directly, that don't come from any importable
statement — e.g. GBM never issues a document (XML or PDF) that states
AAU94801/AAU94802's actual balance, only daily interest income (see
app/parsers/gbm.py). Applied once at seed time, same as accounts/categories,
so it survives a full DB rebuild.

Each entry here should cite exactly where the number came from and when,
the same way a parser docstring cites the statement section it reads.
"""

from __future__ import annotations

import datetime

from app.db import get_session
from app.models import Account, HoldingSnapshot

MANUAL_HOLDING_SNAPSHOTS = [
    {
        "account_name": "GBM",
        "date": datetime.date(2026, 6, 18),
        "sub_portfolio": None,
        "market_value": 1_419_570.98,
        "currency": "MXN",
        "notes": (
            "User-provided screenshot of the GBM+ app 'Mis cuentas' screen "
            "('Total invertido', updated 18/06/2026 10:32 per the app). "
            "Aggregate across the whole GBM relationship (Smart Cash "
            "$607,939.47 + Smart Cash Dólares $0 + Trading MX $574,296.79 + "
            "Trading USA $0 + ~$237k not itemized on that screen) — the app "
            "doesn't expose a breakdown by contract, so this is recorded "
            "against the parent GBM account, not AAU94801/AAU94802 "
            "individually. Net worth treats this as authoritative for the "
            "whole GBM relationship and excludes the contracts' own "
            "interest-only balances from the total to avoid double-counting."
        ),
    },
    {
        "account_name": "Grupo Arreola",
        "date": datetime.date(2025, 7, 1),
        "sub_portfolio": None,
        "market_value": 7_500.00,
        "currency": "EUR",
        "notes": (
            "GRF - CONTRATO DE PRÉSTAMO CONVERTIBLE BORRADOR.docx.pdf: "
            "EUR 7,500 convertible loan to Olivares & Herrera, LDA, dated "
            "1-jul-2025 (Cláusula Segunda), no interest (Cláusula Tercera). "
            "Converts to equity 3 years after the fund's Cierre de la Ronda "
            "(Cláusula Cuarta) — not yet converted, so this is face value, "
            "not a current equity valuation. Corroborated by 6 real BBVA "
            "SPEI transfers to albo totaling $166,000 MXN over 9-11 sep "
            "2025 (memo just 'gonzalo' — no reference to the fund, found "
            "and confirmed by the user only after being flagged as "
            "unclassified). Arreola Herrera Fund I's own Year-1 report "
            "(Lavande_Informe_Ano1-3.pdf) covers the underlying laundromat "
            "business's P&L, not Gonzalo's specific share, so it isn't a "
            "usable mark for this position."
        ),
    },
    {
        "account_name": "Cañadas de Malta",
        "date": datetime.date(2021, 8, 6),
        "sub_portfolio": None,
        "market_value": 50_000.00,
        "currency": "MXN",
        "notes": (
            "Declaración de Reconocimiento de Derechos y Obligaciones, "
            "CONTRATO MUTUO MERCANTIL and Contrato de Compraventa Acciones, "
            "all signed 6-ago-2021: $25,000 MXN as a 3-year commercial loan "
            "at 12%/year fixed interest (mutuo mercantil, due back with "
            "interest ~ago-2024), plus $25,000 MXN as equity — 625 shares "
            "at $40 each, Serie B Clase II, 0.5% of Grupo Alvarez Lomelín "
            "Martínez, S.A.P.I. de C.V. (owner of the brewery). Recorded at "
            "the original $50,000 face value, not net of anything received: "
            "the user confirmed a real $4,500 partial interest payment on "
            "26-jul-2025 (BBVA memo 'Pago Deuda 2', tagged as Transaction "
            "income under 'Ingreso por Inversión', not reflected here), but "
            "says the rest of what's owed is still being disputed and has "
            "not been paid — the 3-year loan term lapsed around ago-2024 "
            "with no full repayment. Whether the remainder is ever "
            "collected is unresolved — see "
            "CLAUDE.md's abierto/sin resolver."
        ),
    },
    {
        # BBVA TDC's imported transaction history only covers Jul-2024
        # onward — Ene-2023 to Jun-2024 statements use an older,
        # structurally different template ("Tarjeta Oro BBVA": DD/MM/YY
        # dates, separate CARGOS/ABONOS columns, no explicit +/- sign
        # token) that parsers/bbva_credit.py doesn't parse yet, see
        # CLAUDE.md's abierto/sin resolver. Without this anchor, the
        # account's balance (sum of imported transactions) would read as
        # whatever those 26 months net to on their own, not the real
        # amount owed.
        "account_name": "BBVA TDC",
        "date": datetime.date(2024, 6, 4),
        "sub_portfolio": None,
        "market_value": -12_448.27,
        "currency": "MXN",
        "notes": (
            "Opening balance anchor, sourced from the first imported "
            "statement's own printed 'Adeudo del periodo anterior: "
            "$12,448.27' (Jul-2024 statement, as of the 04-jun-2024 "
            "cutoff of the preceding, unimported period) — negative here "
            "since it's debt owed, per this project's sign convention. "
            "Confirmed as the same ongoing credit line rather than a "
            "different account: the last 'Tarjeta Oro BBVA' statement "
            "before the card was reissued as 'Tarjeta Platinum BBVA' "
            "prints the identical closing balance for the same date."
        ),
    },
]


def apply_manual_data() -> int:
    """Idempotent: re-running only inserts snapshots not already present
    for that (account, date, sub_portfolio)."""
    inserted = 0
    with get_session() as session:
        for spec in MANUAL_HOLDING_SNAPSHOTS:
            account = session.query(Account).filter_by(name=spec["account_name"]).one()
            existing = (
                session.query(HoldingSnapshot)
                .filter_by(
                    account_id=account.id,
                    date=spec["date"],
                    sub_portfolio=spec["sub_portfolio"],
                )
                .one_or_none()
            )
            if existing is not None:
                continue
            session.add(
                HoldingSnapshot(
                    account_id=account.id,
                    date=spec["date"],
                    sub_portfolio=spec["sub_portfolio"],
                    market_value=spec["market_value"],
                    currency=spec["currency"],
                    source_file=None,
                    notes=spec["notes"],
                )
            )
            inserted += 1
        session.commit()
    return inserted


if __name__ == "__main__":
    count = apply_manual_data()
    print(f"Inserted {count} new manual snapshots")
