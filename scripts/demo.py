"""Run with ``python -m scripts.demo`` to exercise sample alerts."""
from datetime import date, datetime, timedelta

from retail_ops.alerts import due_now, mark_notified, run_alerts, weekly_digest
from retail_ops.database import init_db
from retail_ops.models import Shop
from retail_ops.services import add_product, get_or_create_customer, get_product, record_restock, record_return, record_sale

Session = init_db("sqlite:///:memory:")
AS_OF = date(2026, 11, 7)                          # the day we are checking
T0 = datetime(2026, 11, 8, 6, 0)                   # "now": Sunday morning


def show(title, alerts):
    print(f"\n{title}")
    for a in alerts:
        print(f"  [{a.kind}] {a.message}" + (f"\n      -> {a.action}" if a.action else ""))


with Session() as s:
    shop = Shop(name="Grace Pharmacy"); s.add(shop); s.flush()
    catalog = (("Paracetamol", 180, 300, 300, None), ("Amoxicillin", 1000, 1500, 50, None), ("Artemether", 2000, 2800, 30, None),
               ("Cough Syrup", 1500, 2200, 300, None), ("Zinc", 700, 600, 50, None), ("Face Masks", 600, 1000, 500, None),
               ("Vitamin C", 1700, 2500, 100, date(2026, 11, 20)), ("Cetirizine", 350, 600, 10, date(2026, 10, 30)))
    P = {}
    for name, cost, price, qty, expiry in catalog:
        add_product(s, shop.id, name, cost, price)
        P[name] = get_product(s, shop.id, name)
        record_restock(s, P[name], qty, cost, received_on=date(2026, 9, 1), expiry_date=expiry)

    at = lambda d, h=11: datetime(d.year, d.month, d.day, h)
    ada, bola = get_or_create_customer(s, shop.id, "08031110001", "Ada"), get_or_create_customer(s, shop.id, "08031110002", "Bola")
    for d in (date(2026, 9, 19), date(2026, 9, 26), date(2026, 10, 3), date(2026, 10, 10)):           # Ada: weekly, then vanished
        record_sale(s, P["Paracetamol"], 2, sold_at=at(d), customer=ada)
    for d in (date(2026, 10, 3), date(2026, 10, 10), date(2026, 10, 17), date(2026, 10, 24), date(2026, 10, 31), date(2026, 11, 7)):
        record_sale(s, P["Paracetamol"], 2, sold_at=at(d), customer=bola)                              # Bola: still coming

    target = None
    for i in range(14):                                                    # 25 Oct ... 7 Nov
        d = date(2026, 10, 25) + timedelta(days=i)
        this_week = i >= 7
        sale = record_sale(s, P["Paracetamol"], 10, sold_at=at(d)).sale
        target = sale if d == date(2026, 11, 2) else target
        record_sale(s, P["Amoxicillin"], 2 if this_week else 3, unit_price=1875 if this_week else 1500, sold_at=at(d))
        record_sale(s, P["Cough Syrup"], 5 if this_week else 4, sold_at=at(d))
        record_sale(s, P["Face Masks"], 1, sold_at=at(d))
        record_sale(s, P["Vitamin C"], 1, sold_at=at(d))
        if i <= 9:
            record_sale(s, P["Artemether"], 3, sold_at=at(d))              # stock of 30 is finished on 3 Nov
    record_sale(s, P["Zinc"], 1, sold_at=at(date(2026, 11, 5)))
    record_return(s, target, 2, returned_at=at(date(2026, 11, 4), 15))
    s.commit()

    # ---- Day 1: first check
    r1 = run_alerts(s, shop.id, AS_OF, now=T0)
    kinds = {(a.kind, a.message.split(":")[0].split(" ")[0]) for a in r1.opened}
    show("DAY 1: urgent, to send now", [a for a in r1.opened if a.urgency == "now"])
    show("DAY 1: weekly summary", [a for a in r1.opened if a.urgency == "weekly"])
    for expected in (("out_of_stock", "Artemether"), ("running_low", "Amoxicillin"), ("selling_at_loss", "Zinc"),
                     ("expired", "Cetirizine"), ("expiring_soon", "Vitamin"), ("overstock", "Face")):
        assert expected in kinds, f"missing alert: {expected}"
    assert ("sales_drop", "Sales") in kinds and ("customers_overdue", "1") in kinds
    assert not any(k in kinds for k in (("running_low", "Paracetamol"), ("overstock", "Vitamin"), ("overstock", "Cough"), ("out_of_stock", "Cough"), ("overstock", "Zinc")))
    print("\n  OK: 8 problems found, healthy products stayed quiet, no 'overstock' on top of 'expiring'.")

    # ---- Tell the owner (pretend the WhatsApp was sent)
    todo = due_now(s, shop.id, T0)
    mark_notified(s, todo, T0)
    print(f"\n  Sent {len(todo)} urgent alerts by WhatsApp and marked them as told.")

    # ---- Day 2: nothing changed -> total silence
    r2 = run_alerts(s, shop.id, AS_OF, now=T0 + timedelta(days=1))
    assert r2.opened == [] and r2.resolved == [] and due_now(s, shop.id, T0 + timedelta(days=1)) == []
    print("  OK: next day, same problems -> no new alerts, nothing sent again.")

    # ---- Day 3: the owner restocks Artemether -> that alert closes by itself
    record_restock(s, P["Artemether"], 40, 2000, received_on=AS_OF)
    r3 = run_alerts(s, shop.id, AS_OF, now=T0 + timedelta(days=2))
    assert [a.kind for a in r3.resolved] == ["out_of_stock"] and "Artemether" in r3.resolved[0].message
    print("  OK: owner restocked Artemether -> its alert was resolved automatically.")

    # ---- Day 5: still-unfixed urgent alerts get ONE reminder
    reminders = due_now(s, shop.id, T0 + timedelta(days=4))
    assert reminders and all(a.urgency == "now" for a in reminders)
    show("DAY 5: reminders for urgent problems still not fixed", reminders)
    print(f"\n  Weekly summary on Monday would list {len(weekly_digest(s, shop.id))} items.")