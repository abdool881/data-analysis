"""Checks that each of the 8 fixes really works."""
from datetime import date, datetime, timedelta
import os, sqlite3, tempfile

from database import init_db
from models import Product, Sale, Shop, to_kobo
from services import (ShopError, add_product, current_stock, get_or_create_customer, record_adjustment,
                      record_restock, record_return, record_sale)

def refused(fn):
    try: fn()
    except ShopError as e: return f"refused ({e})"
    return "NOT REFUSED  <-- problem"

S = init_db("sqlite:///:memory:")
with S() as s:
    a, b = Shop(name="A"), Shop(name="B"); s.add_all([a, b]); s.flush()
    p = add_product(s, a.id, "Para", 180, 300, opening_stock=100)
    cust_b = get_or_create_customer(s, b.id, "0803")
    print("1 rounding   to_kobo(1.005) =", to_kobo(1.005), "| to_kobo(2.675) =", to_kobo(2.675))
    print("2 cross-shop:", refused(lambda: record_sale(s, p, 1, customer=cust_b)))
    print("3 direction :", refused(lambda: record_adjustment(s, p, 5, "expired")))
    print("              ", refused(lambda: record_adjustment(s, p, -5, "found")))
    print("4 fractions :", refused(lambda: record_sale(s, p, 0.5)))
    print("              ", refused(lambda: add_product(s, a.id, "X", 1, 2, opening_stock=1.5)))
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

path = os.path.join(tempfile.mkdtemp(), "old.db")
c = sqlite3.connect(path)
c.executescript("""CREATE TABLE shops(id INTEGER PRIMARY KEY, name TEXT, owner_phone TEXT, owner_email TEXT, entry_mode TEXT, created_at DATETIME);
CREATE TABLE products(id INTEGER PRIMARY KEY, shop_id INT, name TEXT, name_key TEXT, unit_cost_kobo INT, selling_price_kobo INT, reorder_level INT, lead_time_days INT, is_active BOOL);
CREATE TABLE sales(id INTEGER PRIMARY KEY, shop_id INT, product_id INT, customer_id INT, sold_at DATETIME, quantity INT, unit_price_kobo INT, unit_cost_kobo INT);
INSERT INTO shops(id,name) VALUES(1,'Old'); INSERT INTO products VALUES(1,1,'P','p',100,200,0,7,1); INSERT INTO sales VALUES(1,1,1,NULL,'2026-01-01 10:00:00',2,200,100);""")
c.commit(); c.close()
S2 = init_db(f"sqlite:///{path}")
with S2() as s:
    print("8 old DB upgraded: cost date =", s.query(Product).one().unit_cost_as_of, "| old sale kept, qty", s.query(Sale).one().quantity)