"""STEP 4: The alerts. The engine speaks up on its own.

Three jobs, kept apart on purpose:
  1. DETECT  - look at the shop and list every problem that exists RIGHT NOW   (the detect_* functions)
  2. SYNC    - compare that list with the alerts already in the notebook       (sync_alerts)
               new problem -> open an alert | same problem -> leave it alone | problem gone -> resolve it
  3. TELL    - ask which alerts the owner still has to be told about          (due_now, weekly_digest)

Why separate? Because sending a WhatsApp can fail. We only mark an alert as "told" AFTER it was really sent,
so a failed send is retried instead of lost, and a problem that lasts for days is not announced every morning.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics import between, sales_frame
from models import Alert, Customer, Product, Restock
from services import stock_levels
from why import compare_periods, stock_history


@dataclass
class AlertConfig:
    """Every rule's number lives here, so an owner can change them later in Settings."""
    window_days: int = 28            # how far back we look to learn how fast things sell
    safety_days: int = 3             # extra days of stock we like to keep as a cushion
    cover_days: int = 14             # when we order, we order enough for this many extra days
    sales_drop_pct: float = 10.0     # alert if sales fall by at least this much
    overstock_days: int = 60         # more than this many days of stock = too much
    expiry_warning_days: int = 30    # warn this far ahead of an expiry date
    overdue_min_visits: int = 3      # a "regular" customer has visited at least this often
    min_units_for_trend: int = 3     # fewer sales than this in the window = too little evidence to judge overstock
    reminder_days: int = 3           # an urgent alert nobody fixed is repeated after this many days


@dataclass
class Candidate:
    """A problem the detectors found. Not saved yet."""
    kind: str
    key: str
    urgency: str                     # "now" or "weekly"
    message: str
    action: str = ""


@dataclass
class SyncResult:
    opened: list
    resolved: list


def naira(x: float) -> str:
    return f"₦{x / 1e6:.1f}M" if abs(x) >= 1e6 else f"₦{x:,.0f}"


def _names(names, n=3) -> str:
    names = list(names)
    return ", ".join(names[:n]) + (f" and {len(names) - n} more" if len(names) > n else "")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# ------------------------------------------------------------------ how fast does each product sell?
def _daily_demand(df: pd.DataFrame, as_of: pd.Timestamp, cfg: AlertConfig) -> tuple[dict, dict]:
    """Units sold per day, per product, over the last `window_days`.
    A product that only started selling 10 days ago is divided by 10, not 28, so new products aren't undercounted."""
    if df.empty:
        return {}, {}
    first_sale = df.groupby("product_id")["date"].min()
    units = between(df, as_of - pd.Timedelta(days=cfg.window_days - 1), as_of).groupby("product_id")["quantity"].sum()
    out, sold = {}, {}
    for pid, u in units.items():
        days = min(max((as_of - first_sale[pid]).days + 1, 7), cfg.window_days)
        out[int(pid)], sold[int(pid)] = max(float(u), 0.0) / days, int(u)
    return out, sold


# ------------------------------------------------------------------ the detectors (each returns Candidates)
def detect_stock(products, lines, demand, cfg) -> list[Candidate]:
    out = []
    for pid, line in lines.items():
        p, daily = products[pid], demand.get(pid, 0.0)
        lead = p.lead_time_days
        if line.stock <= 0:
            if daily > 0:                                                    # only if people actually buy it
                extra = f" (your records show {line.stock}: a delivery may be missing)" if line.stock < 0 else ""
                out.append(Candidate("out_of_stock", str(pid), "now",
                                     f"{p.name} is out of stock{extra}. You normally sell about {daily:.1f} a day "
                                     f"(around {naira(daily * p.selling_price)} of sales a day).",
                                     f"Restock now; supplier takes about {lead} days."))
            continue
        point = max(p.reorder_level if daily == 0 else 0, math.ceil(daily * (lead + cfg.safety_days)))
        if daily > 0 and p.reorder_level:
            point = max(point, p.reorder_level)
        if daily > 0 and line.stock <= point:
            days_left = line.stock / daily
            order = max(math.ceil(daily * (lead + cfg.safety_days + cfg.cover_days)) - line.stock, 1)
            out.append(Candidate("running_low", str(pid), "now",
                                 f"{p.name} is running low: {line.stock} left, about {_plural(round(days_left), 'day')} of sales. "
                                 f"Your supplier takes about {lead} days.", f"Order about {order}."))
        elif daily == 0 and p.reorder_level and line.stock <= p.reorder_level:
            out.append(Candidate("running_low", str(pid), "now",
                                 f"{p.name} is at {line.stock}, below your reorder level of {p.reorder_level}.", "Order more."))
    return out


def detect_losses(products, df, as_of, cfg) -> list[Candidate]:
    out = []
    recent = between(df, as_of - pd.Timedelta(days=6), as_of) if not df.empty else df
    below = recent[(~recent["is_return"]) & (recent["unit_price"] < recent["unit_cost"])] if not recent.empty else recent
    for pid, p in products.items():
        if p.selling_price < p.unit_cost:
            out.append(Candidate("selling_at_loss", str(pid), "now",
                                 f"{p.name} is priced at {naira(p.selling_price)} but costs {naira(p.unit_cost)}: "
                                 f"you lose {naira(p.unit_cost - p.selling_price)} on every one.",
                                 "Raise the price or find a cheaper supplier."))
        elif not below.empty and (below["product_id"] == pid).any():
            rows = below[below["product_id"] == pid]
            out.append(Candidate("selling_at_loss", str(pid), "now",
                                 f"{p.name} was sold below cost {_plural(len(rows), 'time')} in the last 7 days "
                                 f"(lowest price {naira(rows['unit_price'].min())}, cost {naira(p.unit_cost)}).",
                                 "Check your discounts."))
    return out


def detect_sales_drop(df, as_of, stock, cfg) -> list[Candidate]:
    cmp = compare_periods(df, end=as_of, days=7, stock=stock)
    if not cmp["has_previous"] or cmp["before"]["revenue"] <= 0 or cmp["change_pct"] > -cfg.sales_drop_pct:
        return []
    t = cmp["table"]
    worst = t[t["change"] < -0.02 * cmp["before"]["revenue"]].sort_values("change").head(3)   # ignore tiny changes
    if worst.empty:
        worst = t[t["change"] < 0].sort_values("change").head(1)
    causes = "; ".join(f"{r.product} {'-' if r.change < 0 else '+'}{naira(abs(r.change))} ({r.reason.rstrip('.')})" for r in worst.itertuples())
    return [Candidate("sales_drop", "shop", "now",
                      f"Sales fell {abs(cmp['change_pct']):.0f}% ({naira(cmp['before']['revenue'])} to {naira(cmp['now']['revenue'])}) "
                      f"in the 7 days to {cmp['current'][1]:%d %b}. Biggest causes: {causes}.",
                      f"Fix the biggest cause first: {worst.iloc[0]['product']}.")]


def detect_overdue_customers(session, shop_id, df, as_of, cfg) -> list[Candidate]:
    if df.empty:
        return []
    d = df[(~df["is_return"]) & df["customer_id"].notna()]
    if d.empty:
        return []
    g = d.groupby("customer_id").agg(visits=("date", "nunique"), first=("date", "min"), last=("date", "max"), revenue=("revenue", "sum"))
    span = (g["last"] - g["first"]).dt.days
    g["gap"] = span / (g["visits"] - 1).where(g["visits"] > 1)
    g["recency"] = (as_of - g["last"]).dt.days
    g["limit"] = (2.5 * g["gap"]).clip(lower=14)                  # normal silence for THIS customer (at least 2 weeks)
    g["monthly"] = g["revenue"] / (span / 30).clip(lower=1)
    late = g[(g["visits"] >= cfg.overdue_min_visits) & (g["recency"] > g["limit"]) & (g["recency"] <= 2 * g["limit"])]
    if late.empty:
        return []
    label = {c.id: (c.name or c.phone or "customer") for c in session.scalars(select(Customer).where(Customer.shop_id == shop_id))}
    late = late.sort_values("revenue", ascending=False)
    return [Candidate("customers_overdue", "shop", "weekly",
                      f"{_plural(len(late), 'regular customer')} {'is' if len(late) == 1 else 'are'} overdue for a visit: "
                      f"{_names(label.get(int(i), 'customer') for i in late.index)}. "
                      f"{'Together they' if len(late) > 1 else 'This customer'} normally spend{'' if len(late) > 1 else 's'} about {naira(late['monthly'].sum())} a month.",
                      "Call or WhatsApp them with a small thank-you offer.")]


def detect_expiry(session, shop_id, products, lines, demand, as_of, cfg) -> tuple[list[Candidate], set]:
    """Which units on the shelf came from which delivery? We assume first-in, first-out: what is LEFT
    came from the NEWEST deliveries. (An approximation: sales are not tied to a batch.)"""
    by_product: dict[int, list] = {}
    for r in session.scalars(select(Restock).where(Restock.shop_id == shop_id)):
        by_product.setdefault(r.product_id, []).append(r)
    out, flagged = [], set()
    for pid, batches in by_product.items():
        if pid not in products or pid not in lines:
            continue
        left, p, daily = max(lines[pid].stock, 0), products[pid], demand.get(pid, 0.0)
        for b in sorted(batches, key=lambda r: (r.received_on, r.id), reverse=True):
            take = min(left, b.quantity)
            left -= take
            if take <= 0 or b.expiry_date is None:
                continue
            days_left = (b.expiry_date - as_of.date()).days
            if days_left < 0:
                flagged.add(pid)
                out.append(Candidate("expired", f"batch{b.id}", "now",
                                     f"{p.name}: {take} unit(s) from a batch that expired on {b.expiry_date:%d %b %Y} may still be on the shelf.",
                                     "Remove them and record an 'expired' stock adjustment."))
            elif days_left <= cfg.expiry_warning_days:
                expected = daily * days_left
                at_risk = take - math.floor(expected)
                if at_risk > 0:
                    flagged.add(pid)
                    out.append(Candidate("expiring_soon", f"batch{b.id}", "weekly",
                                         f"{p.name}: {take} unit(s) expire in {_plural(days_left, 'day')} ({b.expiry_date:%d %b}). "
                                         f"At your current pace you will sell about {expected:.0f}, so about {at_risk} may expire.",
                                         "Discount them, put them at the front, or ask the supplier to swap them."))
    return out, flagged


def detect_overstock(products, lines, demand, sold, skip_pids, cfg) -> list[Candidate]:
    out = []
    for pid, line in lines.items():
        daily = demand.get(pid, 0.0)
        if pid in skip_pids or daily <= 0 or line.stock <= 0 or sold.get(pid, 0) < cfg.min_units_for_trend:
            continue
        days = line.stock / daily
        if days > cfg.overstock_days:
            how = "more than a year" if days > 365 else f"about {days:.0f} days"
            out.append(Candidate("overstock", str(pid), "weekly",
                                 f"{products[pid].name}: {line.stock} in stock is {how} of sales. Money is tied up on the shelf.",
                                 "Pause buying or run a promotion."))
    return out


# ------------------------------------------------------------------ remember what we already said
def sync_alerts(session: Session, shop_id: int, candidates: list[Candidate], now: datetime) -> SyncResult:
    open_rows = {(a.kind, a.subject_key): a for a in
                 session.scalars(select(Alert).where(Alert.shop_id == shop_id, Alert.status == "open"))}
    seen, opened, resolved = set(), [], []
    for c in candidates:
        k = (c.kind, c.key)
        if k in seen:
            continue
        seen.add(k)
        row = open_rows.get(k)
        if row is None:                                    # a NEW problem
            row = Alert(shop_id=shop_id, kind=c.kind, subject_key=c.key, urgency=c.urgency, message=c.message,
                        action=c.action, status="open", created_at=now, last_seen_at=now)
            session.add(row)
            opened.append(row)
        else:                                              # the SAME problem: refresh the numbers, stay quiet
            row.message, row.action, row.last_seen_at = c.message, c.action, now
    for k, row in open_rows.items():
        if k not in seen:                                  # the problem went away
            row.status, row.resolved_at = "resolved", now
            resolved.append(row)
    session.flush()
    return SyncResult(opened, resolved)


def run_alerts(session: Session, shop_id: int, as_of, cfg: AlertConfig | None = None, now: datetime | None = None) -> SyncResult:
    """Look at the shop as it was at the END of day `as_of`, then update the alerts notebook."""
    cfg, now = cfg or AlertConfig(), now or datetime.now()
    as_of = pd.Timestamp(as_of).normalize()
    df = sales_frame(session, shop_id)
    if not df.empty:
        df = df[df["date"] <= as_of]
    products = {p.id: p for p in session.scalars(select(Product).where(Product.shop_id == shop_id, Product.is_active))}
    lines = {l.product_id: l for l in stock_levels(session, shop_id)}
    demand, sold = _daily_demand(df, as_of, cfg)

    found = detect_stock(products, lines, demand, cfg) + detect_losses(products, df, as_of, cfg)
    if not df.empty:
        found += detect_sales_drop(df, as_of, stock_history(session, shop_id, as_of), cfg)
        found += detect_overdue_customers(session, shop_id, df, as_of, cfg)
    expiry, expiry_pids = detect_expiry(session, shop_id, products, lines, demand, as_of, cfg)
    found += expiry + detect_overstock(products, lines, demand, sold, expiry_pids, cfg)   # no "overstock" on top of "expiring"
    return sync_alerts(session, shop_id, found, now)


# ------------------------------------------------------------------ who needs telling?
def due_now(session: Session, shop_id: int, now: datetime, cfg: AlertConfig | None = None) -> list[Alert]:
    """Urgent alerts the owner has not been told about yet, plus old unfixed ones due a reminder."""
    cfg = cfg or AlertConfig()
    rows = session.scalars(select(Alert).where(Alert.shop_id == shop_id, Alert.status == "open", Alert.urgency == "now")
                           .order_by(Alert.created_at, Alert.id)).all()
    return [a for a in rows if a.notified_at is None or now - a.notified_at >= timedelta(days=cfg.reminder_days)]


def weekly_digest(session: Session, shop_id: int) -> list[Alert]:
    """Everything 'weekly' that is still a problem, for the Monday summary."""
    return list(session.scalars(select(Alert).where(Alert.shop_id == shop_id, Alert.status == "open", Alert.urgency == "weekly")
                                .order_by(Alert.created_at, Alert.id)))


def mark_notified(session: Session, alerts: list[Alert], now: datetime) -> None:
    """Call this AFTER the message was really sent."""
    for a in alerts:
        a.notified_at = now
    session.flush()