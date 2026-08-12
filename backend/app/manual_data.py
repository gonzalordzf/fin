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
from app.models import Account, HoldingSnapshot, Transaction

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
        # BBVA TDC's imported transaction history now covers ene-2023
        # onward (parsers/bbva_credit_legacy.py handles the older
        # "Tarjeta ORO BBVA" template used through jun-2024; see
        # parsers/bbva_credit.py for the "nuevo estado de cuenta
        # universal" template used from jul-2024). This is the earliest
        # point any statement reports a balance for — everything before
        # 05-dic-2022 is genuinely unknown, no statement for it exists in
        # the connected Drive.
        "account_name": "BBVA TDC",
        "date": datetime.date(2022, 12, 5),
        "sub_portfolio": None,
        "market_value": -610.08,
        "currency": "MXN",
        "notes": (
            "Opening balance anchor, sourced from the earliest imported "
            "statement's (Ene-2023, cutoff 04-ene-2023) own printed "
            "'Saldo Inicial del Periodo: -$610.08' as of the period's "
            "05-dic-2022 start — negative here since it's debt owed, per "
            "this project's sign convention."
        ),
    },
]

MANUAL_TRANSACTIONS = [
    {
        # The Noviembre 2023 "Tarjeta ORO BBVA" statement is permanently
        # missing: both the "Noviembre 2023" and "Diciembre 2023" files in
        # the connected Drive folder are the exact same December PDF
        # (md5-confirmed 2026-08-12) — the real November statement was
        # never uploaded and doesn't exist anywhere in the connected
        # Drive. app/importers/bbva_credit.py allowlists this one specific
        # balance-chain break (_KNOWN_CHAIN_GAPS) so it doesn't raise like
        # every other broken chain would; this transaction is what makes
        # the running Transaction-sum balance read correctly across it.
        "account_name": "BBVA TDC",
        "date": datetime.date(2023, 11, 5),
        "amount": 11_947.23,
        "currency": "MXN",
        "description": "Ajuste: estado de cuenta de noviembre 2023 no disponible",
        "source_file": "GAP_NOV2023_ADJUSTMENT",
        "source_row": 0,
        "notes": (
            "Net reconciling amount between Octubre 2023's real closing "
            "balance ('Saldo al Corte' $18,985.52, 04-oct-2023 cutoff) "
            "and Diciembre 2023's real opening balance ('Saldo Inicial "
            "del Periodo' $7,038.29, 05-nov-2023 period start) — both "
            "sourced from real statements either side of the gap. This "
            "is NOT an estimate of what was actually charged or paid "
            "during November, which is unknown and unrecoverable; it's "
            "only the known net effect, positive here (an abono/paydown) "
            "since the balance decreased across the gap."
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

        for spec in MANUAL_TRANSACTIONS:
            account = session.query(Account).filter_by(name=spec["account_name"]).one()
            existing = (
                session.query(Transaction)
                .filter_by(account_id=account.id, source_file=spec["source_file"], source_row=spec["source_row"])
                .one_or_none()
            )
            if existing is not None:
                continue
            session.add(
                Transaction(
                    account_id=account.id,
                    date=spec["date"],
                    amount=spec["amount"],
                    currency=spec["currency"],
                    description=spec["description"],
                    source_file=spec["source_file"],
                    source_row=spec["source_row"],
                    raw_description=spec["notes"],
                )
            )
            inserted += 1

        session.commit()
    return inserted


if __name__ == "__main__":
    count = apply_manual_data()
    print(f"Inserted {count} new manual snapshots/transactions")
