"""STEP 3 (part 2): WHY did it happen?

We compare THIS week with LAST week, product by product, and look for the cause:
  * PRICE  - we charged more or less
  * STOCK  - the shelf was empty, so nobody could buy
  * DEMAND - nothing changed on our side, so fewer customers wanted it

The money change of each product is split in two pieces that add up exactly:
    volume effect = (units now - units before) x OLD price     "we sold more or fewer"
    price effect  = total change - volume effect               "we charged more or less"
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics import between
from models import Product, Restock, Sale, StockAdjustment


def stock_history(session: Session, shop_id: int, end) -> pd.DataFrame:
    """Closing stock at the end of EVERY day, one column per product.
    Built by replaying the notebooks: deliveries and finds go up, sales and losses go down."""
    names = dict(session.execute(select(Product.id, Product.name).where(Product.shop_id == shop_id)).all())
    parts = []
    for table, date_col, sign in ((Restock, Restock.received_on, 1), (StockAdjustment, StockAdjustment.adjusted_on, 1)):
        rows = session.execute(select(table.product_id, date_col, table.quantity).where(table.shop_id == shop_id)).all()
        parts += [(pid, pd.Timestamp(d), sign * q) for pid, d, q in rows]
    rows = session.execute(select(Sale.product_id, Sale.sold_at, Sale.quantity).where(Sale.shop_id == shop_id)).all()
    parts += [(pid, pd.Timestamp(d).normalize(), -q) for pid, d, q in rows]
    if not parts:
        return pd.DataFrame()
    ev = pd.DataFrame(parts, columns=["pid", "date", "delta"]).groupby(["date", "pid"])["delta"].sum().unstack(fill_value=0)
    days = pd.date_range(ev.index.min(), max(pd.Timestamp(end).normalize(), ev.index.max()))
    stock = ev.reindex(days, fill_value=0).cumsum()
    return stock.rename(columns=names)


def stockout_days(stock: pd.DataFrame, start, end) -> pd.Series:
    """For each product: on how many days of the period did the shelf close at 0 or less?"""
    if stock is None or stock.empty:
        return pd.Series(dtype=int)
    window = stock.reindex(pd.date_range(start, end), method="ffill")
    return (window <= 0).sum()          # days before we had any records are NaN -> not counted


def _explain(r) -> str:
    """Turn the numbers of one product into one sentence."""
    if r.change >= 0:
        if r.status == "New this period":
            return "New product this period."
        up = []
        if pd.notna(r.units_change_pct) and r.units_change_pct >= 10:
            up.append(f"units up {r.units_change_pct:.0f}%")
        if pd.notna(r.price_change_pct) and abs(r.price_change_pct) >= 5:
            up.append(f"price {'up' if r.price_change_pct > 0 else 'down'} {abs(r.price_change_pct):.0f}%")
        return ", ".join(up).capitalize() + "." if up else ""
    reasons = []
    if r.stockout_days > 0:
        reasons.append(f"ran out of stock for {int(r.stockout_days)} day{'s' if r.stockout_days != 1 else ''}")
    if pd.notna(r.price_change_pct) and r.price_change_pct >= 5 and r.volume_effect < 0:
        reasons.append(f"price rose {r.price_change_pct:.0f}% and units fell {abs(r.units_change_pct):.0f}%")
    elif pd.notna(r.units_change_pct) and r.units_change_pct <= -10 and not reasons:
        reasons.append(f"fewer units sold ({abs(r.units_change_pct):.0f}% less) with no price or stock cause: demand fell")
    if r.status == "Stopped selling" and not reasons:
        reasons.append("no sales at all this period")
    return (reasons[0].capitalize() + "".join(f"; also {x}" for x in reasons[1:]) + ".") if reasons else "Small change."


def compare_periods(df: pd.DataFrame, end, days: int = 7, stock: pd.DataFrame | None = None) -> dict:
    """This period (the last `days` days up to `end`) against the period right before it."""
    end = pd.Timestamp(end).normalize()
    cur_start = end - pd.Timedelta(days=days - 1)
    prev_end = cur_start - pd.Timedelta(days=1)
    prev_start = prev_end - pd.Timedelta(days=days - 1)
    cur, prev = between(df, cur_start, end), between(df, prev_start, prev_end)

    def totals(d):
        sales_only = d[~d["is_return"]]
        return {"revenue": float(d["revenue"].sum()), "profit": float(d["profit"].sum()), "transactions": int(len(sales_only))}

    result = {"current": (cur_start, end), "previous": (prev_start, prev_end), "now": totals(cur), "before": totals(prev)}
    result["has_previous"] = len(prev) > 0
    result["change"] = result["now"]["revenue"] - result["before"]["revenue"]
    result["change_pct"] = (result["change"] / result["before"]["revenue"] * 100) if result["before"]["revenue"] else np.nan

    agg = lambda d: d.groupby("product").agg(units=("quantity", "sum"), revenue=("revenue", "sum"))
    t = agg(prev).join(agg(cur), how="outer", lsuffix="_prev", rsuffix="_cur").fillna(0.0)
    price_prev = (t["revenue_prev"] / t["units_prev"]).where(t["units_prev"] > 0)
    price_cur = (t["revenue_cur"] / t["units_cur"]).where(t["units_cur"] > 0)
    ref = price_prev.fillna(price_cur).fillna(0.0)

    out = pd.DataFrame(index=t.index)
    out["revenue_prev"], out["revenue_cur"] = t["revenue_prev"], t["revenue_cur"]
    out["change"] = t["revenue_cur"] - t["revenue_prev"]
    out["volume_effect"] = (t["units_cur"] - t["units_prev"]) * ref
    out["price_effect"] = out["change"] - out["volume_effect"]
    out["units_change_pct"] = (t["units_cur"] / t["units_prev"] - 1).where(t["units_prev"] > 0) * 100
    out["price_change_pct"] = (price_cur / price_prev - 1) * 100
    out["status"] = np.select([(t["units_prev"] > 0) & (t["units_cur"] <= 0), (t["units_prev"] <= 0) & (t["units_cur"] > 0)],
                              ["Stopped selling", "New this period"], default="Selling")
    so = stockout_days(stock, cur_start, end)
    out["stockout_days"] = out.index.map(lambda p: int(so.get(p, 0)))
    out = out.reset_index().rename(columns={"index": "product"})
    out = out.sort_values("change", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)
    out["reason"] = [_explain(r) for r in out.itertuples()]
    result["table"] = out
    return result