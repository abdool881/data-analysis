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
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import Customer, CustomerDebt, DebtEntry, Product, Restock, Sale, StockAdjustment, to_kobo


class ShopError(ValueError):
    """A mistake the owner can understand and fix. Safe to show on the screen."""


def _quantity_value(quantity: int | float | Decimal) -> Decimal:
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float, Decimal)):
        raise ShopError("Quantity must be a number.")
    try:
        value = Decimal(str(quantity))
    except InvalidOperation as exc:
        raise ShopError("Quantity must be a finite number.") from exc
    if not value.is_finite():
        raise ShopError("Quantity must be a finite number.")
    if value.normalize().as_tuple().exponent < -3:
        raise ShopError("Quantity supports at most three decimal places.")
    return value


def _stock_value(quantity: Decimal) -> int | Decimal:
    return int(quantity) if quantity == quantity.to_integral_value() else quantity


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
def current_stock(session: Session, product_id: int) -> int | Decimal:
    """left = everything that came IN - everything that went OUT +/- corrections."""
    def total(column, table):
        return session.scalar(select(func.coalesce(func.sum(column), 0)).where(table.product_id == product_id))

    quantity = (total(Restock.quantity, Restock) - total(Sale.quantity, Sale)
                + total(StockAdjustment.quantity, StockAdjustment))
    return _stock_value(Decimal(quantity))


@dataclass
class StockLine:
    product_id: int
    name: str
    stock: int | Decimal
    reorder_level: int | Decimal

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
    return [StockLine(p.id, p.name,
                      _stock_value(Decimal(came_in.get(p.id, 0) - went_out.get(p.id, 0) + fixed.get(p.id, 0))),
                      p.reorder_level)
            for p in products]


# ---------------------------------------------------------------- the jobs
def add_product(session: Session, shop_id: int, name: str, unit_cost: float, selling_price: float,
                opening_stock: int | float = 0, reorder_level: int | float = 0, lead_time_days: int = 7,
                expiry_date: date | None = None, unit_name: str = "unit") -> Product:
    opening_stock = _quantity_value(opening_stock)
    reorder_level = _quantity_value(reorder_level)
    if unit_cost < 0 or selling_price < 0:
        raise ShopError("Prices cannot be negative.")
    if opening_stock < 0 or reorder_level < 0:
        raise ShopError("Opening stock and reorder level cannot be negative.")
    unit_name = " ".join(unit_name.split())
    if not unit_name:
        raise ShopError("Product unit name cannot be empty.")
    try:
        get_product(session, shop_id, name)
    except ShopError:
        pass
    else:
        raise ShopError(f"'{name}' already exists in this shop.")

    product = Product(shop_id=shop_id, name=name, unit_cost=unit_cost, selling_price=selling_price,
                      reorder_level=reorder_level, lead_time_days=lead_time_days, unit_name=unit_name)
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


@dataclass
class CreditSaleResult:
    sale: Sale
    debt: CustomerDebt
    warning: str | None = None


def record_sale(session: Session, product: Product, quantity: int | float | Decimal, unit_price: float | None = None,
                sold_at: datetime | None = None, customer: Customer | None = None) -> SaleResult:
    quantity = _quantity_value(quantity)
    if quantity <= 0:
        raise ShopError("Sale quantity must be positive. Use record_return() for returned items.")
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


def _debt_balance_kobo(session: Session, debt: CustomerDebt) -> int:
    settled = session.scalar(
        select(func.coalesce(func.sum(DebtEntry.amount_kobo), 0)).where(DebtEntry.debt_id == debt.id)
    )
    return debt.principal_kobo - int(settled)


def record_credit_sale(session: Session, product: Product, customer: Customer,
                       quantity: int | float | Decimal, due_date: date,
                       unit_price: float | None = None, sold_at: datetime | None = None) -> CreditSaleResult:
    """Record a customer sale and open a receivable for its full amount."""
    if customer.id is None:
        raise ShopError("Customer must be saved before recording a credit sale.")
    if customer.shop_id != product.shop_id:
        raise ShopError("Customer and product must belong to the same shop.")
    if not customer.name or not customer.name.strip() or not customer.phone:
        raise ShopError("Credit sales require a customer name and phone number.")
    quantity = _quantity_value(quantity)
    if quantity <= 0:
        raise ShopError("Sale quantity must be positive. Use record_return() for returned items.")
    sold_at = sold_at or datetime.now()
    if due_date < sold_at.date():
        raise ShopError("Debt due date cannot be before the sale date.")
    price = product.selling_price if unit_price is None else unit_price
    principal_kobo = int(
        (quantity * to_kobo(price)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    if principal_kobo <= 0:
        raise ShopError("A credit sale must have an amount greater than zero.")
    result = record_sale(session, product, quantity, unit_price=price, sold_at=sold_at, customer=customer)
    principal_kobo = int(
        (Decimal(result.sale.quantity) * result.sale.unit_price_kobo).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    debt = CustomerDebt(sale_id=result.sale.id, principal_kobo=principal_kobo, due_date=due_date)
    session.add(debt)
    session.flush()
    return CreditSaleResult(result.sale, debt, result.warning)


def record_debt_payment(session: Session, debt: CustomerDebt, amount: int | float | Decimal,
                        paid_at: datetime | None = None, note: str | None = None) -> DebtEntry:
    """Record a payment against one customer credit sale."""
    if debt.id is None:
        raise ShopError("Payment must reference a saved customer debt.")
    debt = session.get(CustomerDebt, debt.id)
    if debt is None:
        raise ShopError("Customer debt was not found.")
    amount_kobo = to_kobo(amount)
    if amount_kobo <= 0:
        raise ShopError("Payment amount must be greater than zero.")
    balance_kobo = _debt_balance_kobo(session, debt)
    if balance_kobo <= 0:
        raise ShopError("This debt has no remaining balance.")
    if amount_kobo > balance_kobo:
        raise ShopError("Payment cannot exceed the remaining debt balance.")
    entry = DebtEntry(debt_id=debt.id, amount_kobo=amount_kobo, kind="payment",
                      recorded_at=paid_at or datetime.now(), note=note)
    session.add(entry)
    session.flush()
    return entry


def record_return(session: Session, original_sale: Sale, quantity: int | float | Decimal,
                  returned_at: datetime | None = None) -> SaleResult:
    quantity = _quantity_value(quantity)
    if quantity <= 0:
        raise ShopError("Return quantity must be positive.")
    if original_sale.id is None:
        raise ShopError("A return must reference a saved sale.")

    original_sale = session.get(Sale, original_sale.id)
    if original_sale is None:
        raise ShopError("The original sale was not found.")
    if original_sale.quantity <= 0 or original_sale.return_of_sale_id is not None:
        raise ShopError("Only an original sale can be returned.")

    legacy_return = session.scalar(
        select(Sale.id).where(
            Sale.shop_id == original_sale.shop_id,
            Sale.product_id == original_sale.product_id,
            Sale.quantity < 0,
            Sale.return_of_sale_id.is_(None),
        ).limit(1)
    )
    if legacy_return is not None:
        raise ShopError("Unlinked legacy returns exist for this product; reconcile them before recording another return.")

    already_returned = session.scalar(
        select(func.coalesce(func.sum(-Sale.quantity), 0)).where(
            Sale.return_of_sale_id == original_sale.id
        )
    )
    if quantity + already_returned > original_sale.quantity:
        raise ShopError("Return quantity exceeds the quantity sold and not yet returned.")

    returned_at = returned_at or datetime.now()
    if returned_at < original_sale.sold_at:
        raise ShopError("Return time cannot be before the original sale.")

    sale_return = Sale(
        shop_id=original_sale.shop_id,
        product_id=original_sale.product_id,
        customer_id=original_sale.customer_id,
        return_of_sale_id=original_sale.id,
        sold_at=returned_at,
        quantity=-quantity,
        unit_price_kobo=original_sale.unit_price_kobo,
        unit_cost_kobo=original_sale.unit_cost_kobo,
    )
    session.add(sale_return)
    session.flush()
    debt = session.scalar(select(CustomerDebt).where(CustomerDebt.sale_id == original_sale.id))
    if debt is not None:
        return_credit_kobo = int(
            (Decimal(quantity) * original_sale.unit_price_kobo).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        if return_credit_kobo:
            session.add(DebtEntry(debt_id=debt.id, amount_kobo=return_credit_kobo, kind="return_credit",
                                  recorded_at=returned_at, note="Returned items"))
            session.flush()
    return SaleResult(sale_return)


def record_restock(session: Session, product: Product, quantity: int | float | Decimal, unit_cost: float,
                   received_on: date | None = None, expiry_date: date | None = None,
                   supplier: str | None = None) -> Restock:
    quantity = _quantity_value(quantity)
    if quantity <= 0:
        raise ShopError("A delivery must have a quantity above zero.")
    if unit_cost < 0:
        raise ShopError("Cost cannot be negative.")
    received_on = received_on or date.today()
    restock = Restock(shop_id=product.shop_id, product_id=product.id, received_on=received_on,
                      quantity=quantity, unit_cost=unit_cost, kind="purchase", expiry_date=expiry_date, supplier=supplier)
    if received_on >= product.unit_cost_as_of:
        product.unit_cost = unit_cost
        product.unit_cost_as_of = received_on
    session.add(restock)
    session.flush()
    return restock


def record_adjustment(session: Session, product: Product, quantity: int | float | Decimal, reason: str,
                      adjusted_on: date | None = None, note: str | None = None) -> StockAdjustment:
    """quantity is NEGATIVE when stock is lost, POSITIVE when found."""
    quantity = _quantity_value(quantity)
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