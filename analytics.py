"""STEP 3 (part 1): WHAT happened?

The database is good at storing things. pandas is good at asking questions about lots of numbers.
So we do this in two moves:
  1. sales_frame()  - copy the Sales notebook into one big table (a "DataFrame")
  2. small functions that ask that table questions: by_period(), product_ranking() ...

Money rule: in the database money is kobo. Here we turn it into naira for the owner.
Returns: a return is a sale with a NEGATIVE quantity, so it automatically reduces revenue and profit.
We count a return on the day it was returned (not the day of the original sale).
"""
from __future__ import annotations

import pandas as pd
import numpy as np
from datetime import date, datetime, time, timedelta
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from models import Customer, CustomerDebt, DebtEntry, Product, Sale, Shop

COLUMNS = ["sale_id", "product_id", "product", "customer_id", "sold_at", "date", "quantity",
           "unit_price", "unit_cost", "revenue", "cost", "profit", "is_return"]


def sales_frame(session: Session, shop_id: int, start: date | None = None,
                end: date | None = None) -> pd.DataFrame:
    """Every sale and return of one shop, as a table with revenue / cost / profit already worked out."""
    statement = (
        select(Sale.id, Sale.product_id, Product.name, Sale.customer_id, Sale.sold_at, Sale.quantity,
               Sale.unit_price_kobo, Sale.unit_cost_kobo)
        .join(Product, Product.id == Sale.product_id).where(Sale.shop_id == shop_id)
    )
    if start is not None:
        statement = statement.where(Sale.sold_at >= datetime.combine(start, time.min))
    if end is not None:
        statement = statement.where(Sale.sold_at < datetime.combine(end + timedelta(days=1), time.min))
    rows = session.execute(
        statement.order_by(Sale.sold_at)
    ).all()
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(rows, columns=["sale_id", "product_id", "product", "customer_id", "sold_at",
                                     "quantity", "price_kobo", "cost_kobo"])
    df["sold_at"] = pd.to_datetime(df["sold_at"])
    df["quantity"] = df["quantity"].astype(float)
    df["date"] = df["sold_at"].dt.normalize()
    df["unit_price"] = df["price_kobo"] / 100
    df["unit_cost"] = df["cost_kobo"] / 100
    df["revenue"] = df["quantity"] * df["unit_price"]            # a return makes this negative
    df["cost"] = df["quantity"] * df["unit_cost"]
    df["profit"] = df["revenue"] - df["cost"]
    df["is_return"] = df["quantity"] < 0
    return df[COLUMNS].sort_values("sold_at").reset_index(drop=True)


def between(df: pd.DataFrame, start, end) -> pd.DataFrame:
    """Only the rows from the start day to the end day, including both days."""
    return df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))]


def summary(df: pd.DataFrame) -> dict:
    """The headline numbers."""
    revenue, profit = float(df["revenue"].sum()), float(df["profit"].sum())
    sales_only = df[~df["is_return"]]
    units = float(df["quantity"].sum())
    return {
        "revenue": revenue,
        "profit": profit,
        "margin_pct": profit / revenue * 100 if revenue else None,
        "units": int(units) if units.is_integer() else units,
        "transactions": int(len(sales_only)),
        "avg_sale": float(sales_only["revenue"].mean()) if len(sales_only) else 0.0,
        "returns_value": float(-df.loc[df["is_return"], "revenue"].sum()),
    }


_RULES = {"D": "D", "W": "W-SUN", "M": "MS"}      # day, week (ends Sunday), month


def by_period(df: pd.DataFrame, freq: str = "D", start=None, end=None) -> pd.DataFrame:
    """Revenue, profit and units per day / week / month. Days with no sales show as 0, not missing."""
    if df.empty:
        return pd.DataFrame(columns=["period", "revenue", "profit", "units", "transactions"])
    d = df.copy()
    d["tx"] = (~d["is_return"]).astype(int)
    out = d.set_index("date").resample(_RULES[freq]).agg(
        revenue=("revenue", "sum"), profit=("profit", "sum"), units=("quantity", "sum"), transactions=("tx", "sum"))
    if freq == "D" and (start is not None or end is not None):
        out = out.reindex(pd.date_range(start or out.index.min(), end or out.index.max()), fill_value=0)
    if freq == "W":                       # pandas labels a week by its LAST day; show the FIRST day (Monday) instead
        out.index = out.index - pd.Timedelta(days=6)
    change_pct = out["revenue"].pct_change(fill_method=None) * 100
    out["change_pct"] = change_pct.astype(object).where(np.isfinite(change_pct), None)
    return out.rename_axis("period").reset_index()


def product_ranking(df: pd.DataFrame) -> pd.DataFrame:
    """Best to worst. 'top_80' marks the few products that bring in 80% of the money."""
    if df.empty:
        return pd.DataFrame()
    g = df.groupby("product").agg(revenue=("revenue", "sum"), units=("quantity", "sum"), profit=("profit", "sum"))
    g["margin_pct"] = (g["profit"] / g["revenue"] * 100).where(g["revenue"] != 0)
    g = g.sort_values("revenue", ascending=False)
    total = g["revenue"].sum()
    g["share_pct"] = g["revenue"] / total * 100 if total else 0.0
    g["cumulative_pct"] = g["share_pct"].cumsum()
    g["top_80"] = (g["cumulative_pct"] - g["share_pct"]) < 80      # still inside the first 80%
    return g.reset_index()


def weekday_pattern(df: pd.DataFrame) -> pd.DataFrame:
    """Average revenue for each day of the week (counting days with no sales as 0)."""
    daily = by_period(df, "D", df["date"].min(), df["date"].max()).set_index("period")["revenue"]
    out = daily.groupby(daily.index.day_name()).mean()
    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return out.reindex([d for d in order if d in out.index]).rename("avg_revenue").rename_axis("weekday").reset_index()


def debt_dashboard(session: Session, shop_id: int, as_of: date | None = None,
                   due_soon_days: int = 7) -> dict:
    """Return receivables, customer/item details, and due-date analytics for a shop."""
    as_of = as_of or date.today()
    due_soon_through = as_of + timedelta(days=due_soon_days)
    rows = session.execute(
        select(CustomerDebt, Sale, Customer, Product)
        .join(Sale, Sale.id == CustomerDebt.sale_id)
        .join(Customer, Customer.id == Sale.customer_id)
        .join(Product, Product.id == Sale.product_id)
        .where(Sale.shop_id == shop_id)
    ).all()
    entries = {
        debt_id: (int(paid or 0), int(returns or 0))
        for debt_id, paid, returns in session.execute(
            select(
                DebtEntry.debt_id,
                func.sum(case((DebtEntry.kind == "payment", DebtEntry.amount_kobo), else_=0)),
                func.sum(case((DebtEntry.kind == "return_credit", DebtEntry.amount_kobo), else_=0)),
            ).join(CustomerDebt, CustomerDebt.id == DebtEntry.debt_id)
            .join(Sale, Sale.id == CustomerDebt.sale_id)
            .where(Sale.shop_id == shop_id)
            .group_by(DebtEntry.debt_id)
        ).all()
    }

    debts = []
    customer_totals: dict[int, dict] = {}
    total_outstanding = overdue_amount = due_today_amount = due_soon_amount = customer_credit_total = 0
    overdue_count = due_today_count = due_soon_count = open_count = 0
    owing_customers: set[int] = set()

    for debt, sale, customer, product in rows:
        paid_kobo, return_kobo = entries.get(debt.id, (0, 0))
        balance_kobo = debt.principal_kobo - paid_kobo - return_kobo
        amount_due_kobo, credit_kobo = max(balance_kobo, 0), max(-balance_kobo, 0)
        if amount_due_kobo:
            open_count += 1
            total_outstanding += amount_due_kobo
            owing_customers.add(customer.id)
            summary = customer_totals.setdefault(customer.id, {
                "customer_id": customer.id, "name": customer.name, "phone": customer.phone, "amount_due": 0.0,
            })
            summary["amount_due"] += amount_due_kobo / 100
        if credit_kobo:
            customer_credit_total += credit_kobo

        if balance_kobo <= 0:
            status = "customer_credit" if credit_kobo else "paid"
        elif debt.due_date < as_of:
            status = "overdue"
            overdue_count += 1
            overdue_amount += amount_due_kobo
        elif debt.due_date == as_of:
            status = "due_today"
            due_today_count += 1
            due_today_amount += amount_due_kobo
        elif debt.due_date <= due_soon_through:
            status = "due_soon"
            due_soon_count += 1
            due_soon_amount += amount_due_kobo
        else:
            status = "open"

        debts.append({
            "debt_id": debt.id,
            "sale_id": sale.id,
            "customer_id": customer.id,
            "customer_name": customer.name,
            "customer_phone": customer.phone,
            "product": product.name,
            "quantity": float(sale.quantity),
            "unit": product.unit_name,
            "unit_price": sale.unit_price_kobo / 100,
            "sold_at": sale.sold_at,
            "due_date": debt.due_date,
            "original_amount": debt.principal_kobo / 100,
            "paid_amount": paid_kobo / 100,
            "returned_amount": return_kobo / 100,
            "amount_due": amount_due_kobo / 100,
            "customer_credit": credit_kobo / 100,
            "status": status,
        })

    debts.sort(key=lambda item: (item["due_date"], item["debt_id"]))
    top_customers = sorted(customer_totals.values(), key=lambda item: item["amount_due"], reverse=True)[:10]
    shop = session.get(Shop, shop_id)
    return {
        "shop_id": shop_id,
        "currency_code": shop.currency_code if shop else "NGN",
        "as_of": as_of,
        "total_outstanding": total_outstanding / 100,
        "overdue_amount": overdue_amount / 100,
        "due_today_amount": due_today_amount / 100,
        "due_soon_amount": due_soon_amount / 100,
        "customer_credit_total": customer_credit_total / 100,
        "open_debt_count": open_count,
        "overdue_count": overdue_count,
        "due_today_count": due_today_count,
        "due_soon_count": due_soon_count,
        "customers_owing_count": len(owing_customers),
        "top_customers": top_customers,
        "debts": debts,
    }