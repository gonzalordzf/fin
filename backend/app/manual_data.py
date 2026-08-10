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
