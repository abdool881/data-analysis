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
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Product, Sale

COLUMNS = ["sale_id", "product_id", "product", "customer_id", "sold_at", "date", "quantity",
           "unit_price", "unit_cost", "revenue", "cost", "profit", "is_return"]


def sales_frame(session: Session, shop_id: int) -> pd.DataFrame:
    """Every sale and return of one shop, as a table with revenue / cost / profit already worked out."""
    rows = session.execute(
        select(Sale.id, Sale.product_id, Product.name, Sale.customer_id, Sale.sold_at, Sale.quantity,
               Sale.unit_price_kobo, Sale.unit_cost_kobo)
        .join(Product, Product.id == Sale.product_id).where(Sale.shop_id == shop_id)).all()
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(rows, columns=["sale_id", "product_id", "product", "customer_id", "sold_at",
                                     "quantity", "price_kobo", "cost_kobo"])
    df["sold_at"] = pd.to_datetime(df["sold_at"])
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
    return {
        "revenue": revenue,
        "profit": profit,
        "margin_pct": profit / revenue * 100 if revenue else None,
        "units": int(df["quantity"].sum()),
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
    out["change_pct"] = out["revenue"].pct_change() * 100
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