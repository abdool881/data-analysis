"""Run:  python demo_step2.py"""
from database import init_db
from models import Shop
from services import (ShopError, add_product, current_stock, get_or_create_customer, get_product,
                      record_adjustment, record_restock, record_sale, stock_levels)

Session = init_db("sqlite:///:memory:")

with Session() as s:
    shop = Shop(name="Grace Pharmacy")
    s.add(shop); s.flush()

    # 1. Set up two products, each with the stock we start with
    add_product(s, shop.id, "Paracetamol 500mg", unit_cost=180, selling_price=300, opening_stock=200, reorder_level=50)
    add_product(s, shop.id, "Amoxicillin 500mg", unit_cost=1000, selling_price=1500, opening_stock=50, reorder_level=20)
    para = get_product(s, shop.id, "  paracetamol 500MG ")        # capitals and spaces don't matter
    amox = get_product(s, shop.id, "Amoxicillin 500mg")
    print("1. Start:", {l.name: l.stock for l in stock_levels(s, shop.id)})

    # 2. A sale: we only say WHAT and HOW MANY. The price fills itself in.
    ada = get_or_create_customer(s, shop.id, "0803 111 2222", "Ada")
    first = record_sale(s, para, 3, customer=ada).sale
    print(f"2. Sold 3 at ₦{first.unit_price:.0f} each, cost remembered ₦{first.unit_cost:.0f}, profit ₦{first.profit:.0f}")

    # 3. A delivery arrives, and the supplier charged MORE this time
    record_restock(s, para, 100, unit_cost=200)
    print(f"3. Product cost is now ₦{para.unit_cost:.0f}, but the OLD sale still says ₦{first.unit_cost:.0f}")

    # 4. Five boxes expired
    record_adjustment(s, para, -5, "expired")

    # 5. Count what's left: 200 - 3 + 100 - 5
    print("5. Paracetamol left:", current_stock(s, para.id), "(should be 292)")

    # 6. Selling more than we have: allowed, but we get a warning
    result = record_sale(s, amox, 80)
    print("6. Warning:", result.warning)

    # 7. Mistakes the owner can understand
    for bad in (lambda: get_product(s, shop.id, "Panadol"),
                lambda: record_sale(s, para, 0),
                lambda: record_adjustment(s, para, -1, "vibes"),
                lambda: add_product(s, shop.id, "PARACETAMOL 500MG", 1, 2)):
        try:
            bad()
        except ShopError as e:
            print("7. Friendly error:", e)

    s.commit()
    print("8. Everything below its reorder level:",
          [l.name for l in stock_levels(s, shop.id) if l.below_reorder_level])