"""Run:  python demo_step3.py
Builds two weeks of a pretend pharmacy with planted stories, then checks the engine finds them:
  * Amoxicillin: price raised 25%  -> fewer units sold
  * Artemether:  runs out of stock on 3 Nov
  * Cough Syrup: sells more (a bright spot)
  * Paracetamol: normal, but a customer returns 2 boxes
"""
from datetime import date, datetime, timedelta

import pandas as pd

from analytics import by_period, product_ranking, sales_frame, summary, weekday_pattern
from database import init_db
from models import Shop
from services import add_product, get_product, record_restock, record_return, record_sale
from why import compare_periods, stock_history

pd.set_option("display.width", 220); pd.set_option("display.max_columns", 20); pd.set_option("display.max_colwidth", 90)
Session = init_db("sqlite:///:memory:")

with Session() as s:
    shop = Shop(name="Grace Pharmacy"); s.add(shop); s.flush()
    for name, cost, price in (("Paracetamol", 180, 300), ("Amoxicillin", 1000, 1500), ("Artemether", 2000, 2800), ("Cough Syrup", 1500, 2200)):
        add_product(s, shop.id, name, cost, price)
    P = {p: get_product(s, shop.id, p) for p in ("Paracetamol", "Amoxicillin", "Artemether", "Cough Syrup")}
    for name, qty in (("Paracetamol", 500), ("Amoxicillin", 300), ("Artemether", 30), ("Cough Syrup", 300)):
        record_restock(s, P[name], qty, P[name].unit_cost, received_on=date(2026, 10, 20))

    return_target = None
    for i in range(14):                                    # 25 Oct ... 7 Nov (week 1 = last week, week 2 = this week)
        day = date(2026, 10, 25) + timedelta(days=i)
        at = datetime(day.year, day.month, day.day, 11)
        this_week = i >= 7
        sale = record_sale(s, P["Paracetamol"], 10, sold_at=at).sale
        if day == date(2026, 11, 2):
            return_target = sale
        record_sale(s, P["Amoxicillin"], 2 if this_week else 3, unit_price=1875 if this_week else 1500, sold_at=at)
        record_sale(s, P["Cough Syrup"], 5 if this_week else 4, sold_at=at)
        if i <= 9:                                         # Artemether: stock of 30 is gone after 3 Nov
            record_sale(s, P["Artemether"], 3, sold_at=at)
    record_return(s, return_target, 2, returned_at=datetime(2026, 11, 4, 15))
    s.commit()

    df = sales_frame(s, shop.id)
    print("=== PART 1: WHAT happened ===")
    print("Headline numbers:", {k: round(v, 1) if isinstance(v, float) else v for k, v in summary(df).items()})
    print("\nRevenue per week:\n", by_period(df, "W").round({"revenue": 0, "profit": 0, "change_pct": 0}).to_string(index=False))
    print("\nProducts, best to worst:\n", product_ranking(df).round(1).to_string(index=False))

    print("\n=== PART 2: WHY did it happen? (7 days to 7 Nov vs the 7 days before) ===")
    stock = stock_history(s, shop.id, end="2026-11-07")
    r = compare_periods(df, end="2026-11-07", days=7, stock=stock)
    print(f"Sales: ₦{r['before']['revenue']:,.0f} -> ₦{r['now']['revenue']:,.0f}  ({r['change']:+,.0f}, {r['change_pct']:+.1f}%)")
    print(r["table"][["product", "change", "price_effect", "volume_effect", "stockout_days", "reason"]].round(0).to_string(index=False))

    t = r["table"].set_index("product")
    assert abs(t["change"].sum() - r["change"]) < 0.01, "product changes must add up to the total change"
    assert abs((t["price_effect"] + t["volume_effect"]).sum() - r["change"]) < 0.01, "price + volume must add up"
    assert t.loc["Artemether", "stockout_days"] == 5 and "ran out of stock" in t.loc["Artemether", "reason"].lower()
    assert "price rose 25%" in t.loc["Amoxicillin", "reason"].lower()
    assert t.loc["Amoxicillin", "price_effect"] > 0 > t.loc["Amoxicillin", "volume_effect"]
    print("\nAll checks passed: the engine found the price rise and the stock-out.")