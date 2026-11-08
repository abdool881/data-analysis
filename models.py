"""STEP 1.5: The data model, now stronger.

What changed since Step 1:
  1. Money is stored as integer hundredths, so there are no tiny rounding errors.
  2. Sales remember the DATE AND TIME (so we can find busy hours later).
  3. Product names are unique ignoring capital letters ("Paracetamol" == "paracetamol").
  4. NEW table: StockAdjustment, for damaged / expired / stolen / miscounted items.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates


# ---------- money helpers ----------
def to_kobo(naira: float) -> int:
    """Convert a major-currency amount to the stored hundredths (e.g. 300.50 -> 30050)."""
    return int((Decimal(str(naira)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def naira_view(kobo_column: str):
    """Expose a major-currency amount while storing it as an integer number of hundredths."""
    def getter(self):
        value = getattr(self, kobo_column)
        return None if value is None else value / 100

    def setter(self, naira):
        setattr(self, kobo_column, to_kobo(naira))
    return property(getter, setter)


class Base(DeclarativeBase):
    """Every table class inherits from this."""


class Shop(Base):
    __tablename__ = "shops"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    owner_phone: Mapped[str | None] = mapped_column(String(20))
    owner_email: Mapped[str | None] = mapped_column(String(120))
    entry_mode: Mapped[str] = mapped_column(String(20), default="individual")   # or "daily_totals"
    currency_code: Mapped[str] = mapped_column(String(3), default="NGN")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    products: Mapped[list["Product"]] = relationship(back_populates="shop", cascade="all, delete-orphan")
    customers: Mapped[list["Customer"]] = relationship(back_populates="shop", cascade="all, delete-orphan")

    @validates("currency_code")
    def _normalize_currency_code(self, _key, value: str) -> str:
        value = value.strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise ValueError("Currency code must be a three-letter code.")
        return value


class Product(Base):
    __tablename__ = "products"
    # name_key is the lowercase copy of the name; THAT is what must be unique per shop
    __table_args__ = (UniqueConstraint("shop_id", "name_key", name="uq_product_name_per_shop"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    name_key: Mapped[str] = mapped_column(String(120))                  # filled in automatically
    unit_name: Mapped[str] = mapped_column(String(30), default="unit")
    unit_cost_kobo: Mapped[int] = mapped_column(Integer)                # current buy price
    unit_cost_as_of: Mapped[date] = mapped_column(Date, default=date.today)
    selling_price_kobo: Mapped[int] = mapped_column(Integer)            # default sell price
    reorder_level: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=0)
    lead_time_days: Mapped[int] = mapped_column(Integer, default=7)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    unit_cost = naira_view("unit_cost_kobo")
    selling_price = naira_view("selling_price_kobo")

    shop: Mapped["Shop"] = relationship(back_populates="products")
    sales: Mapped[list["Sale"]] = relationship(back_populates="product")
    restocks: Mapped[list["Restock"]] = relationship(back_populates="product")
    adjustments: Mapped[list["StockAdjustment"]] = relationship(back_populates="product")

    @validates("name")
    def _clean_name(self, _key, value: str) -> str:
        """Runs every time a name is set: trims spaces and fills in the lowercase copy."""
        value = " ".join(value.split())
        self.name_key = value.lower()
        return value


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("shop_id", "phone", name="uq_customer_phone_per_shop"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    name: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(20))

    shop: Mapped["Shop"] = relationship(back_populates="customers")


class Sale(Base):
    __tablename__ = "sales"
    __table_args__ = (Index("ix_sales_shop_time", "shop_id", "sold_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    return_of_sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id"), index=True)
    sold_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)   # date AND time
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))                    # negative = return of return_of_sale_id
    unit_price_kobo: Mapped[int] = mapped_column(Integer)                        # price actually charged
    unit_cost_kobo: Mapped[int] = mapped_column(Integer)                         # cost at the time of sale

    unit_price = naira_view("unit_price_kobo")
    unit_cost = naira_view("unit_cost_kobo")

    product: Mapped["Product"] = relationship(back_populates="sales")
    customer: Mapped["Customer | None"] = relationship()
    credit_debt: Mapped["CustomerDebt | None"] = relationship(back_populates="sale", uselist=False)

    @property
    def sold_on(self) -> date:
        return self.sold_at.date()

    @property
    def revenue(self) -> float:
        return float(self.quantity * self.unit_price_kobo / 100)

    @property
    def profit(self) -> float:
        return float(self.quantity * (self.unit_price_kobo - self.unit_cost_kobo) / 100)


class CustomerDebt(Base):
    __tablename__ = "customer_debts"

    id: Mapped[int] = mapped_column(primary_key=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), unique=True, index=True)
    principal_kobo: Mapped[int] = mapped_column(Integer)
    due_date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    sale: Mapped["Sale"] = relationship(back_populates="credit_debt")
    entries: Mapped[list["DebtEntry"]] = relationship(back_populates="debt", cascade="all, delete-orphan")


class DebtEntry(Base):
    __tablename__ = "debt_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    debt_id: Mapped[int] = mapped_column(ForeignKey("customer_debts.id"), index=True)
    amount_kobo: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20), default="payment")
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    note: Mapped[str | None] = mapped_column(String(200))

    debt: Mapped["CustomerDebt"] = relationship(back_populates="entries")

    @validates("kind")
    def _check_kind(self, _key, value: str) -> str:
        if value not in {"payment", "return_credit"}:
            raise ValueError("Debt entry kind must be 'payment' or 'return_credit'.")
        return value


class Restock(Base):
    """Stock coming IN."""
    __tablename__ = "restocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    received_on: Mapped[date] = mapped_column(Date)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_cost_kobo: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20), default="purchase")        # "opening" or "purchase"
    expiry_date: Mapped[date | None] = mapped_column(Date)
    supplier: Mapped[str | None] = mapped_column(String(120))

    unit_cost = naira_view("unit_cost_kobo")
    product: Mapped["Product"] = relationship(back_populates="restocks")


ADJUSTMENT_REASONS = ("damaged", "expired", "stolen", "miscount", "found", "other")


class StockAdjustment(Base):
    """NEW. Fixes the count when the shelf and the notebook disagree.
    quantity is NEGATIVE when stock is lost (damaged, expired, stolen) and POSITIVE when found."""
    __tablename__ = "stock_adjustments"

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    adjusted_on: Mapped[date] = mapped_column(Date)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    reason: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(String(200))

    product: Mapped["Product"] = relationship(back_populates="adjustments")

    @validates("reason")
    def _check_reason(self, _key, value: str) -> str:
        if value not in ADJUSTMENT_REASONS:
            raise ValueError(f"reason must be one of {ADJUSTMENT_REASONS}")
        return value


class Alert(Base):
    """STEP 4. A warning the engine raised for a shop (e.g. 'Amoxicillin is running low').

    One row = one EPISODE of a problem. While the problem lasts the row stays 'open' and is never duplicated.
    When the problem goes away the row becomes 'resolved'. If it comes back later, a NEW row is opened.
      kind         what sort of problem        ('out_of_stock', 'running_low', 'sales_drop', ...)
      subject_key  what it is about            (a product id, a stock batch id, or 'shop')
      urgency      'now' (tell the owner at once) or 'weekly' (goes in the Monday summary)
      notified_at  when we last TOLD the owner. Empty = not told yet, so a failed WhatsApp send is never lost.
    """
    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_shop_status", "shop_id", "status"),
        # the database itself refuses two OPEN alerts for the same problem
        Index("uq_open_alert", "shop_id", "kind", "subject_key", unique=True,
              sqlite_where=text("status = 'open'"), postgresql_where=text("status = 'open'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"))
    kind: Mapped[str] = mapped_column(String(30))
    subject_key: Mapped[str] = mapped_column(String(40))
    urgency: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(10), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime)
