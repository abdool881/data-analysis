"""STEP 1.5: The data model, now stronger.

What changed since Step 1:
  1. Money is stored as whole KOBO (integers), so there are no tiny rounding errors.
  2. Sales remember the DATE AND TIME (so we can find busy hours later).
  3. Product names are unique ignoring capital letters ("Paracetamol" == "paracetamol").
  4. NEW table: StockAdjustment, for damaged / expired / stolen / miscounted items.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates


# ---------- money helpers ----------
def to_kobo(naira: float) -> int:
    """₦300.50 -> 30050 kobo. Whole numbers never have rounding mistakes."""
    return int((Decimal(str(naira)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def naira_view(kobo_column: str):
    """Lets us write product.selling_price = 300 (naira) while the database safely stores 30000 kobo."""
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    products: Mapped[list["Product"]] = relationship(back_populates="shop", cascade="all, delete-orphan")
    customers: Mapped[list["Customer"]] = relationship(back_populates="shop", cascade="all, delete-orphan")


class Product(Base):
    __tablename__ = "products"
    # name_key is the lowercase copy of the name; THAT is what must be unique per shop
    __table_args__ = (UniqueConstraint("shop_id", "name_key", name="uq_product_name_per_shop"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    name_key: Mapped[str] = mapped_column(String(120))                  # filled in automatically
    unit_cost_kobo: Mapped[int] = mapped_column(Integer)                # current buy price
    unit_cost_as_of: Mapped[date] = mapped_column(Date, default=date.today)
    selling_price_kobo: Mapped[int] = mapped_column(Integer)            # default sell price
    reorder_level: Mapped[int] = mapped_column(Integer, default=0)
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
    quantity: Mapped[int] = mapped_column(Integer)                               # negative = return of return_of_sale_id
    unit_price_kobo: Mapped[int] = mapped_column(Integer)                        # price actually charged
    unit_cost_kobo: Mapped[int] = mapped_column(Integer)                         # cost at the time of sale

    unit_price = naira_view("unit_price_kobo")
    unit_cost = naira_view("unit_cost_kobo")

    product: Mapped["Product"] = relationship(back_populates="sales")
    customer: Mapped["Customer | None"] = relationship()

    @property
    def sold_on(self) -> date:
        return self.sold_at.date()

    @property
    def revenue(self) -> float:
        return self.quantity * self.unit_price_kobo / 100

    @property
    def profit(self) -> float:
        return self.quantity * (self.unit_price_kobo - self.unit_cost_kobo) / 100


class Restock(Base):
    """Stock coming IN."""
    __tablename__ = "restocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    received_on: Mapped[date] = mapped_column(Date)
    quantity: Mapped[int] = mapped_column(Integer)
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
    quantity: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(String(200))

    product: Mapped["Product"] = relationship(back_populates="adjustments")

    @validates("reason")
    def _check_reason(self, _key, value: str) -> str:
        if value not in ADJUSTMENT_REASONS:
            raise ValueError(f"reason must be one of {ADJUSTMENT_REASONS}")
        return value