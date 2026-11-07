"""Run:  python demo_step1_5.py"""
from datetime import date, datetime

from sqlalchemy.exc import IntegrityError

from database import init_db
from models import Product, Sale, Shop, StockAdjustment

Session = init_db("sqlite:///:memory:")

with Session() as s:
    shop = Shop(name="Grace Pharmacy")
    para = Product(shop=shop, name="  Paracetamol   500mg ", unit_cost=180.50, selling_price=300)
    s.add_all([shop, para])
    s.flush()

    # 1. Money is stored as whole kobo, but we still talk in naira
    print("Stored in database (kobo):", para.unit_cost_kobo, "| We read it as naira:", para.unit_cost)

    # 2. Names are tidied up, and capital letters don't create duplicates
    print("Name saved as:", repr(para.name))
    s.add(Product(shop_id=shop.id, name="PARACETAMOL 500MG", unit_cost=1, selling_price=2))
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        print("Same name with different capitals was rejected")

    # 3. A sale now remembers date AND time
    sale = Sale(shop_id=shop.id, product=para, sold_at=datetime(2026, 11, 5, 17, 45),
                quantity=3, unit_price=300, unit_cost=180.50)
    s.add(sale)

    # 4. NEW: stock adjustment, e.g. 5 boxes expired
    s.add(StockAdjustment(shop_id=shop.id, product=para, adjusted_on=date(2026, 11, 6),
                          quantity=-5, reason="expired", note="Batch ended Oct"))
    s.commit()
    print("Sold at:", sale.sold_at, "| Profit:", sale.profit)
    print("Adjustments:", [(a.quantity, a.reason) for a in para.adjustments])

    # A made-up reason is refused
    try:
        StockAdjustment(shop_id=shop.id, product=para, adjusted_on=date.today(), quantity=-1, reason="vibes")
    except ValueError as e:
        print("Bad reason refused:", e)