"""Computes a credit card's next statement cutoff and payment-due dates
from its stored terms (Account.statement_cutoff_day,
payment_due_offset_days, payment_due_offset_type) — see seed.py for where
those numbers came from (user-provided 2026-08-12, confirmed against real
BBVA TDC statements: corte 04-ago-2026, fecha límite de pago 24-ago-2026
== +20 días naturales).

Business-day counting (AMEX: 15 días hábiles) only skips Saturday/Sunday —
Mexican bank holidays aren't accounted for, so a due date landing near one
may be off by a day or two against the real statement.
"""

from __future__ import annotations

import calendar
import datetime

from app.models import Account, PaymentDueOffsetType


def _clamp_day(year: int, month: int, day: int) -> int:
    last_day = calendar.monthrange(year, month)[1]
    return min(day, last_day)


def _add_business_days(start: datetime.date, days: int) -> datetime.date:
    current = start
    added = 0
    while added < days:
        current += datetime.timedelta(days=1)
        if current.weekday() < 5:  # Monday-Friday
            added += 1
    return current


def next_cutoff_date(account: Account, today: datetime.date | None = None) -> datetime.date:
    if account.statement_cutoff_day is None:
        raise ValueError(f"{account.name} has no statement_cutoff_day configured")
    today = today or datetime.date.today()
    day = _clamp_day(today.year, today.month, account.statement_cutoff_day)
    this_month_cutoff = datetime.date(today.year, today.month, day)
    if today <= this_month_cutoff:
        return this_month_cutoff
    next_month = today.month % 12 + 1
    next_year = today.year + (1 if today.month == 12 else 0)
    day = _clamp_day(next_year, next_month, account.statement_cutoff_day)
    return datetime.date(next_year, next_month, day)


def next_payment_due_date(account: Account, today: datetime.date | None = None) -> datetime.date:
    cutoff = next_cutoff_date(account, today)
    if account.payment_due_offset_days is None or account.payment_due_offset_type is None:
        raise ValueError(f"{account.name} has no payment terms configured")
    if account.payment_due_offset_type == PaymentDueOffsetType.NATURAL:
        return cutoff + datetime.timedelta(days=account.payment_due_offset_days)
    return _add_business_days(cutoff, account.payment_due_offset_days)
