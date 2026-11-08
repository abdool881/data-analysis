# Retail operations engine

This project provides the data and alerting layer for retail shops. It is not tied to pharmacy products: a shop can track any catalog item with a name, cost, selling price, stock quantity, and a unit such as `piece`, `kg`, `litre`, or `metre`.

## Project layout

```text
retail_ops/          Engine, API, database models, and business logic
migrations/          Alembic migration environment and schema revisions
tests/               Automated API integration tests
scripts/             Demo and engine regression checks
```

Install the project and its dependencies from the repository root with `python -m pip install -r requirements.txt`.

Run the automated tests and example checks with:

```bash
python -m unittest discover -s tests -v
python -m scripts.check_fixes
python -m scripts.demo
```

## What it supports

- Products, sales, returns, restocks, and stock corrections
- Customer credit sales, partial payments, and returned-item credits
- A debt dashboard with customer contact details, items purchased, outstanding balances, and due-date summaries
- Fractional stock quantities up to three decimal places
- Product-specific units, so stock is counted consistently for each item
- Per-shop currency codes, with no exchange-rate conversion between shops
- Sales, profit, stock, reorder, slow-moving/overstock, loss, customer-retention, and optional expiry alerts
- Alert deduplication, notification reminders, and automatic resolution

Expiry dates are optional on restocks. Retailers that do not use expiry tracking can leave them blank or disable that alert rule. Customer-retention, sales-drop, and overstock rules can also be disabled when they do not fit a shop's business model.

## Customer debts

Create/find a customer with a name and phone number, then record a credit sale against that customer and give it a due date. Each credit sale is tied to its sale line, so the debt record retains the product, quantity, unit price, and sale date. Record partial or final payments against that debt. Returning items from a credit sale automatically applies a return credit to its balance.

```python
from datetime import date, timedelta

from retail_ops.analytics import debt_dashboard
from retail_ops.alerts import run_alerts
from retail_ops.services import get_or_create_customer, record_credit_sale, record_debt_payment

customer = get_or_create_customer(session, shop.id, "08035551234", "Jordan Lee")
credit = record_credit_sale(
    session, product, customer, quantity=2, due_date=date.today() + timedelta(days=14),
)
record_debt_payment(session, credit.debt, amount=100)

dashboard = debt_dashboard(session, shop.id)
alerts = run_alerts(session, shop.id, as_of=date.today())
```

The dashboard returns outstanding, overdue, due-today, due-soon, and customer-credit totals plus a debt list with the customer's name/phone, purchased item, amount paid, balance, and due date. The alert engine opens an urgent alert on the due date (by default, also one day before), repeats it using the existing reminder schedule, and resolves it after the debt is settled. This tracks money customers owe the shop; supplier/payables debt is not included.

## Examples

```python
from retail_ops.alerts import AlertConfig, run_alerts
from retail_ops.database import init_db
from retail_ops.models import Shop
from retail_ops.services import add_product

Session = init_db()
with Session() as session:
    shop = Shop(name="Market", currency_code="USD")
    session.add(shop)
    session.flush()

    add_product(
        session, shop.id, "Apples", unit_cost=1.25, selling_price=2.49,
        opening_stock=12.5, reorder_level=3, unit_name="kg",
    )

    # For a shop where expiry and customer-retention alerts are not relevant:
    config = AlertConfig(expiry_warning_days=None, overdue_min_visits=None)
    run_alerts(session, shop.id, as_of="2026-11-07", cfg=config)
    session.commit()
```

`currency_code` is the currency in which that shop records its prices. Set it when creating the shop; changing the code alone does not convert historical prices. The engine formats alert amounts in that currency; it does not convert or compare amounts across currencies. Monetary storage currently preserves two decimal places. Each product's quantity unit should remain consistent across its sales, restocks, and stock adjustments.

Existing SQLite and PostgreSQL databases are upgraded when `init_db()` runs. Existing shops retain the `NGN` default, and existing products retain the default `unit` unit.

## HTTP API

The engine is exposed through a versioned FastAPI service. Request and response models are documented by OpenAPI at `/docs`.

```bash
export DATABASE_URL='postgresql+psycopg://user:password@localhost:5432/retail'
export RETAIL_SIGNUP_TOKEN='set-a-long-random-onboarding-secret'
alembic upgrade head
export RETAIL_AUTO_CREATE_SCHEMA=false
uvicorn retail_ops.api:app --host 0.0.0.0 --port 8000
```

`POST /v1/shops` requires the `X-Signup-Token` onboarding secret. It returns a randomly generated shop API key once; send it as `Authorization: Bearer <key>` for shop routes. Keys are stored as hashes and can be rotated at `POST /v1/shop/api-key/rotate`. Every data endpoint scopes its records to the authenticated shop. Set `DATABASE_URL` to PostgreSQL for deployment; SQLite remains useful for local development. Connection pool limits are configurable with `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`, and `DB_POOL_RECYCLE`.

Available endpoint groups include inventory/products, customers, sales/returns, debt balances and payment history, sales/product analytics, the combined dashboard, and alert processing/listing. Analytics requests are bounded to 366 days and default to the most recent 90 days. `POST /v1/alerts/run` should be invoked on a schedule; `GET /v1/alerts/due` supplies notifications eligible to send. Delivery (WhatsApp/SMS/email), a browser UI, and user roles are not included yet.

Schema changes are versioned in `migrations/`. For a database that already contains the engine's existing tables, run the legacy `init_db()` schema upgrade once, then `alembic stamp e89314efd691` followed by `alembic upgrade head`; review the baseline against your actual schema before stamping. New databases should use `alembic upgrade head`. Production should set `RETAIL_AUTO_CREATE_SCHEMA=false` after applying migrations so application workers do not mutate schema at startup.