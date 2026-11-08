"""Checks service behavior, retail compatibility, and legacy database upgrades."""
from datetime import date, datetime, timedelta
from decimal import Decimal
import os, sqlite3, tempfile

from retail_ops.alerts import AlertConfig, run_alerts
from retail_ops.analytics import debt_dashboard, sales_frame, summary
from retail_ops.database import init_db
from retail_ops.models import Product, Sale, Shop, to_kobo
from retail_ops.services import (ShopError, add_product, current_stock, get_or_create_customer, record_adjustment,
                      record_credit_sale, record_debt_payment, record_restock, record_return, record_sale)

def refused(fn):
    try: fn()
    except ShopError as e: return f"refused ({e})"
    return "NOT REFUSED  <-- problem"

S = init_db("sqlite:///:memory:")
with S() as s:
    a, b = Shop(name="A"), Shop(name="B"); s.add_all([a, b]); s.flush()
    p = add_product(s, a.id, "Para", 180, 300, opening_stock=100)
    oil = add_product(s, a.id, "Cooking oil", 500, 750, opening_stock=1.5, unit_name="litre")
    cust_b = get_or_create_customer(s, b.id, "0803")
    print("1 rounding   to_kobo(1.005) =", to_kobo(1.005), "| to_kobo(2.675) =", to_kobo(2.675))
    print("2 cross-shop:", refused(lambda: record_sale(s, p, 1, customer=cust_b)))
    print("3 direction :", refused(lambda: record_adjustment(s, p, 5, "expired")))
    print("              ", refused(lambda: record_adjustment(s, p, -5, "found")))
    oil_sale = record_sale(s, oil, Decimal("0.250")).sale
    record_return(s, oil_sale, Decimal("0.125"))
    record_adjustment(s, oil, Decimal("-0.125"), "damaged")
    assert current_stock(s, oil.id) == Decimal("1.250")
    assert summary(sales_frame(s, a.id))["units"] == 0.125
    print("4 fractional stock:", current_stock(s, oil.id), oil.unit_name,
          "| rejects excess precision:", refused(lambda: record_sale(s, oil, Decimal("0.0001"))))
    t0 = datetime(2026, 11, 1, 10)
    sale = record_sale(s, p, 5, sold_at=t0).sale
    record_restock(s, p, 50, 250, received_on=date.today())
    r = record_return(s, sale, 2, returned_at=t0 + timedelta(days=5)).sale
    print(f"5 return uses ORIGINAL cost: {r.unit_cost:.0f} (product cost now {p.unit_cost:.0f}); profit {r.profit:.0f} (expect -240)")
    print("5 too many  :", refused(lambda: record_return(s, sale, 4, returned_at=t0 + timedelta(days=6))))
    print("5 neg sale  :", refused(lambda: record_sale(s, p, -1)))
    print("6 chronology:", refused(lambda: record_return(s, sale, 1, returned_at=t0 - timedelta(days=1))))
    record_restock(s, p, 10, 100, received_on=date(2026, 10, 1))
    print("7 old restock left cost at", p.unit_cost, "(expect 250)")
    print("  stock =", current_stock(s, p.id), "(expect 100-5+2+50+10 = 157)")
    s.commit()

    usd_shop = Shop(name="USD shop", currency_code="USD")
    s.add(usd_shop); s.flush()
    item = add_product(s, usd_shop.id, "Marked-down item", 10, 8)
    record_restock(s, item, 2, 10, expiry_date=date.today() - timedelta(days=1))
    config = AlertConfig(expiry_warning_days=None, overdue_min_visits=None,
                         sales_drop_pct=None, overstock_days=None)
    currency_alerts = run_alerts(s, usd_shop.id, date.today(), cfg=config).opened
    assert any("$8.00" in alert.message and "$10.00" in alert.message for alert in currency_alerts)
    assert not any(alert.kind == "expired" for alert in currency_alerts)
    print("9 currency-aware alerts:", currency_alerts[0].message)

    debtor = get_or_create_customer(s, a.id, "08035551234", "Jordan Lee")
    debt_due_date = date.today() + timedelta(days=1)
    credit = record_credit_sale(s, oil, debtor, Decimal("0.5"), debt_due_date)
    assert refused(lambda: record_debt_payment(s, credit.debt, 376)).startswith("refused")
    record_debt_payment(s, credit.debt, 100)
    record_return(s, credit.sale, Decimal("0.1"))
    dashboard = debt_dashboard(s, a.id, as_of=date.today())
    credit_row = next(row for row in dashboard["debts"] if row["debt_id"] == credit.debt.id)
    assert credit_row["amount_due"] == 200 and credit_row["status"] == "due_soon"
    debt_alerts = run_alerts(s, a.id, date.today()).opened
    debt_alert = next(alert for alert in debt_alerts if alert.kind == "debt_due")
    assert "Jordan Lee" in debt_alert.message and "08035551234" in debt_alert.message and "Cooking oil" in debt_alert.message
    run_alerts(s, a.id, debt_due_date, now=datetime.combine(debt_due_date, datetime.min.time()))
    assert "due today" in debt_alert.message
    overdue_date = debt_due_date + timedelta(days=1)
    run_alerts(s, a.id, overdue_date, now=datetime.combine(overdue_date, datetime.min.time()))
    assert "overdue" in debt_alert.message
    assert next(row for row in debt_dashboard(s, a.id, as_of=overdue_date)["debts"]
                if row["debt_id"] == credit.debt.id)["status"] == "overdue"
    record_debt_payment(s, credit.debt, 200)
    resolved = run_alerts(s, a.id, overdue_date).resolved
    assert any(alert.kind == "debt_due" for alert in resolved)
    assert next(row for row in debt_dashboard(s, a.id)["debts"] if row["debt_id"] == credit.debt.id)["status"] == "paid"
    print("10 customer debt:", debt_alert.message, "| partial payment, return credit, final payment and auto-resolution passed")

path = os.path.join(tempfile.mkdtemp(), "old.db")
c = sqlite3.connect(path)
c.executescript("""CREATE TABLE shops(id INTEGER PRIMARY KEY, name TEXT, owner_phone TEXT, owner_email TEXT, entry_mode TEXT, created_at DATETIME);
CREATE TABLE products(id INTEGER PRIMARY KEY, shop_id INT, name TEXT, name_key TEXT, unit_cost_kobo INT, selling_price_kobo INT, reorder_level INT, lead_time_days INT, is_active BOOL);
CREATE TABLE sales(id INTEGER PRIMARY KEY, shop_id INT, product_id INT, customer_id INT, sold_at DATETIME, quantity INT, unit_price_kobo INT, unit_cost_kobo INT);
INSERT INTO shops(id,name) VALUES(1,'Old'); INSERT INTO products VALUES(1,1,'P','p',100,200,0,7,1); INSERT INTO sales VALUES(1,1,1,NULL,'2026-01-01 10:00:00',2,200,100);""")
c.commit(); c.close()
S2 = init_db(f"sqlite:///{path}")
with S2() as s:
    product = s.query(Product).one()
    print("8 old DB upgraded: currency =", s.query(Shop).one().currency_code, "| unit =", product.unit_name,
          "| cost date =", product.unit_cost_as_of, "| old sale kept, qty", s.query(Sale).one().quantity)