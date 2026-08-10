from __future__ import annotations

import datetime
import enum

from sqlalchemy import (
    ForeignKey,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AccountKind(str, enum.Enum):
    TRANSACTIONAL = "transactional"
    INVESTMENT_FORMAL = "investment_formal"
    INVESTMENT_INFORMAL = "investment_informal"
    EQUITY_COMPENSATION = "equity_compensation"


class CategoryKind(str, enum.Enum):
    EXPENSE = "expense"
    INCOME = "income"
    TRANSFER = "transfer"


class Account(Base):
    """One of the 8 real-world accounts, or a sub-account/contract under one
    (e.g. GBM's AAU94801 / AAU94802 contracts hang off the GBM account via
    parent_account_id)."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    institution: Mapped[str]
    kind: Mapped[AccountKind]
    currency: Mapped[str] = mapped_column(default="MXN")
    parent_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    notes: Mapped[str | None]

    parent: Mapped[Account | None] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list[Account]] = relationship(back_populates="parent")

    transactions: Mapped[list[Transaction]] = relationship(back_populates="account")
    snapshots: Mapped[list[HoldingSnapshot]] = relationship(back_populates="account")
    equity_entries: Mapped[list[EquityCompensationEntry]] = relationship(back_populates="account")
    alternative_entries: Mapped[list[AlternativeInvestmentEntry]] = relationship(back_populates="account")

    def __repr__(self) -> str:  # pragma: no cover
        return f"Account({self.name!r})"


class Category(Base):
    """Expense/income taxonomy. Seed data, not hardcoded logic — safe to
    edit/extend later without touching parser or app code."""

    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("name", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    kind: Mapped[CategoryKind]
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))

    parent: Mapped[Category | None] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list[Category]] = relationship(back_populates="parent")

    def __repr__(self) -> str:  # pragma: no cover
        return f"Category({self.name!r})"


class Transaction(Base):
    """A single movement in a transactional account (BBVA, AMEX, Revolut)."""

    __tablename__ = "transactions"
    __table_args__ = (
        # Field values (date/amount/description/external_ref) aren't a
        # reliable identity: BBVA repeats the same date+amount+description
        # with no reference for same-day fee entries (e.g. Banxico
        # compensation charges), and reuses a reference for a sent SPEI and
        # its reversal. (source_file, source_row) — the transaction's
        # position in the statement — is what re-imports dedupe against.
        UniqueConstraint("account_id", "source_file", "source_row", name="uq_transaction_dedupe"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    date: Mapped[datetime.date]
    amount: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False))
    """Signed: negative = money out (cargo/expense), positive = money in (abono/income)."""
    currency: Mapped[str] = mapped_column(default="MXN")
    description: Mapped[str]
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    benefit_tag: Mapped[str | None]
    """AMEX-only: cashback/benefit category redeemed on this charge."""
    external_ref: Mapped[str | None]
    """Bank reference/folio, kept for cross-referencing; not used for dedup."""
    source_file: Mapped[str | None]
    source_row: Mapped[int | None]
    """Position of this transaction within source_file, in document order."""
    raw_description: Mapped[str | None]
    """Original, unedited description as it appeared in the source statement."""

    account: Mapped[Account] = relationship(back_populates="transactions")
    category: Mapped[Category | None] = relationship()

    def __repr__(self) -> str:  # pragma: no cover
        return f"Transaction({self.date}, {self.amount}, {self.description!r})"


class HoldingSnapshot(Base):
    """A point-in-time valuation for a formal investment account (GBM, Bitso,
    Optimax). One row per statement period, optionally split by
    sub_portfolio (Optimax's 3 sub-portfolios; GBM's Addenda movements roll
    up here per contract/period)."""

    __tablename__ = "holding_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    date: Mapped[datetime.date]
    sub_portfolio: Mapped[str | None]
    market_value: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False))
    net_contribution_period: Mapped[float | None] = mapped_column(Numeric(14, 2, asdecimal=False))
    currency: Mapped[str] = mapped_column(default="MXN")
    source_file: Mapped[str | None]
    notes: Mapped[str | None]

    account: Mapped[Account] = relationship(back_populates="snapshots")


class StatementSummary(Base):
    """The printed opening/closing balance of one imported statement, kept
    so a later import can verify the balance chain: statement N's
    saldo_anterior must equal statement N-1's saldo_final, or a statement is
    missing between them (docs/04-gotchas.md — this is how a missing
    statement gets caught instead of silently producing a wrong balance)."""

    __tablename__ = "statement_summaries"
    __table_args__ = (UniqueConstraint("account_id", "source_file", name="uq_statement_summary"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    source_file: Mapped[str]
    period_start: Mapped[datetime.date]
    period_end: Mapped[datetime.date]
    saldo_anterior: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False))
    saldo_final: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False))

    account: Mapped[Account] = relationship()


class EquityCompensationEntry(Base):
    """Shareworks: one row per vesting/contribution event."""

    __tablename__ = "equity_compensation_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    date: Mapped[datetime.date]
    own_contribution: Mapped[float | None] = mapped_column(Numeric(14, 2, asdecimal=False))
    company_match: Mapped[float | None] = mapped_column(Numeric(14, 2, asdecimal=False))
    market_value: Mapped[float | None] = mapped_column(Numeric(14, 2, asdecimal=False))
    vested_units: Mapped[float | None] = mapped_column(Numeric(14, 4, asdecimal=False))
    currency: Mapped[str] = mapped_column(default="USD")
    source_file: Mapped[str | None]
    notes: Mapped[str | None]

    account: Mapped[Account] = relationship(back_populates="equity_entries")


class AlternativeInvestmentEntry(Base):
    """Balagan: one row per monthly Estado de Resultados."""

    __tablename__ = "alternative_investment_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    period_start: Mapped[datetime.date]
    period_end: Mapped[datetime.date]
    revenue: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False))
    expenses: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False))
    net_income: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False))
    participation_pct: Mapped[float] = mapped_column(Numeric(6, 4, asdecimal=False))
    proportional_income: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False))
    currency: Mapped[str] = mapped_column(default="MXN")
    source_file: Mapped[str | None]
    notes: Mapped[str | None]

    account: Mapped[Account] = relationship(back_populates="alternative_entries")
