"""STEP 2: The shop jobs.

models.py described the NOTEBOOKS. This file describes the JOBS people do with them:
  add_product        - set up a product (with the stock we start with)
  record_sale        - sell something
  record_restock     - a delivery arrives
  record_adjustment  - fix the count (expired, damaged, stolen...)
  current_stock      - how many are left of ONE product
  stock_levels       - how many are left of EVERYTHING

Rule: these functions never "commit" (save for good). The caller decides when to save.
That way, if something goes wrong half-way, nothing is saved half-way.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import Customer, Product, Restock, Sale, StockAdjustment


class ShopError(ValueError):
    """A mistake the owner can understand and fix. Safe to show on the screen."""


# ---------------------------------------------------------------- finding things
def get_product(session: Session, shop_id: int, name: str) -> Product:
    """Find a product by name, ignoring capital letters and extra spaces."""
    key = " ".join(name.split()).lower()
    product = session.scalar(select(Product).where(Product.shop_id == shop_id, Product.name_key == key))
    if product is None:
        raise ShopError(f"No product called '{name}'. Add it first.")
    return product


def get_or_create_customer(session: Session, shop_id: int, phone: str, name: str | None = None) -> Customer:
    """Known phone number -> same customer. New phone number -> new customer."""
    phone = "".join(ch for ch in phone if ch.isdigit())
    if not phone:
        raise ShopError("A customer needs a phone number.")
    customer = session.scalar(select(Customer).where(Customer.shop_id == shop_id, Customer.phone == phone))
    if customer is None:
        customer = Customer(shop_id=shop_id, phone=phone, name=name)
        session.add(customer)
        session.flush()
    return customer


# ---------------------------------------------------------------- counting stock
def current_stock(session: Session, product_id: int) -> int:
    """left = everything that came IN - everything that went OUT +/- corrections."""
    def total(column, table):
        return session.scalar(select(func.coalesce(func.sum(column), 0)).where(table.product_id == product_id))

    return total(Restock.quantity, Restock) - total(Sale.quantity, Sale) + total(StockAdjustment.quantity, StockAdjustment)


@dataclass
class StockLine:
    product_id: int
    name: str
    stock: int
    reorder_level: int

    @property
    def below_reorder_level(self) -> bool:
        return self.stock <= self.reorder_level


def stock_levels(session: Session, shop_id: int) -> list[StockLine]:
    """Stock of EVERY active product, using 3 quick questions to the database (not 3 per product)."""
    def sums(column, table):
        rows = session.execute(select(table.product_id, func.sum(column)).where(table.shop_id == shop_id)
                               .group_by(table.product_id)).all()
        return dict(rows)

    came_in, went_out, fixed = sums(Restock.quantity, Restock), sums(Sale.quantity, Sale), sums(StockAdjustment.quantity, StockAdjustment)
    products = session.scalars(select(Product).where(Product.shop_id == shop_id, Product.is_active).order_by(Product.name)).all()
    return [StockLine(p.id, p.name, came_in.get(p.id, 0) - went_out.get(p.id, 0) + fixed.get(p.id, 0), p.reorder_level)
            for p in products]


# ---------------------------------------------------------------- the jobs
def add_product(session: Session, shop_id: int, name: str, unit_cost: float, selling_price: float,
                opening_stock: int = 0, reorder_level: int = 0, lead_time_days: int = 7,
                expiry_date: date | None = None) -> Product:
    if unit_cost < 0 or selling_price < 0:
        raise ShopError("Prices cannot be negative.")
    if opening_stock < 0:
        raise ShopError("Opening stock cannot be negative.")
    try:
        get_product(session, shop_id, name)
    except ShopError:
        pass
    else:
        raise ShopError(f"'{name}' already exists in this shop.")

    product = Product(shop_id=shop_id, name=name, unit_cost=unit_cost, selling_price=selling_price,
                      reorder_level=reorder_level, lead_time_days=lead_time_days)
    session.add(product)
    session.flush()                      # gives the product its id
    if opening_stock:
        session.add(Restock(shop_id=shop_id, product_id=product.id, received_on=date.today(), quantity=opening_stock,
                            unit_cost=unit_cost, kind="opening", expiry_date=expiry_date))
    return product


@dataclass
class SaleResult:
    sale: Sale
    warning: str | None = None           # something worth telling the owner, but NOT a reason to refuse the sale


def record_sale(session: Session, product: Product, quantity: int, unit_price: float | None = None,
                sold_at: datetime | None = None, customer: Customer | None = None) -> SaleResult:
    if quantity == 0:
        raise ShopError("Quantity cannot be zero.")
    if customer is not None and customer.shop_id != product.shop_id:
        raise ShopError("Customer and product must belong to the same shop.")
    price = product.selling_price if unit_price is None else unit_price       # pre-fill the usual price
    if price < 0:
        raise ShopError("Price cannot be negative.")

    warning = None
    available = current_stock(session, product.id)
    if quantity > available:
        warning = (f"Only {available} of {product.name} in stock but you sold {quantity}. "
                   f"Did you forget to record a delivery?")

    sale = Sale(shop_id=product.shop_id, product_id=product.id, customer_id=customer.id if customer else None,
                sold_at=sold_at or datetime.now(), quantity=quantity, unit_price=price,
                unit_cost=product.unit_cost)                                  # remember the cost TODAY
    session.add(sale)
    session.flush()
    return SaleResult(sale, warning)


def record_restock(session: Session, product: Product, quantity: int, unit_cost: float,
                   received_on: date | None = None, expiry_date: date | None = None,
                   supplier: str | None = None) -> Restock:
    if quantity <= 0:
        raise ShopError("A delivery must have a quantity above zero.")
    if unit_cost < 0:
        raise ShopError("Cost cannot be negative.")
    restock = Restock(shop_id=product.shop_id, product_id=product.id, received_on=received_on or date.today(),
                      quantity=quantity, unit_cost=unit_cost, kind="purchase", expiry_date=expiry_date, supplier=supplier)
    product.unit_cost = unit_cost        # the newest price we paid becomes the cost for FUTURE sales
    session.add(restock)
    session.flush()
    return restock


def record_adjustment(session: Session, product: Product, quantity: int, reason: str,
                      adjusted_on: date | None = None, note: str | None = None) -> StockAdjustment:
    """quantity is NEGATIVE when stock is lost, POSITIVE when found."""
    if quantity == 0:
        raise ShopError("Adjustment cannot be zero.")
    if reason in {"damaged", "expired", "stolen"} and quantity > 0:
        raise ShopError(f"{reason.capitalize()} adjustments must reduce stock.")
    if reason == "found" and quantity < 0:
        raise ShopError("Found adjustments must increase stock.")
    try:
        adj = StockAdjustment(shop_id=product.shop_id, product_id=product.id, adjusted_on=adjusted_on or date.today(),
                              quantity=quantity, reason=reason, note=note)
    except ValueError as e:
        raise ShopError(str(e))
    session.add(adj)
    session.flush()
    return adj