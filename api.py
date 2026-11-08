"""Versioned HTTP API for the retail operations engine."""
from __future__ import annotations

import hashlib
import os
import secrets
from threading import Lock
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Generator, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from alerts import AlertConfig, due_now, run_alerts, weekly_digest
from analytics import by_period, debt_dashboard, product_ranking, sales_frame, summary, weekday_pattern
from database import init_db
from models import Alert, Customer, CustomerDebt, DebtEntry, Product, Sale, Shop
from services import (
    ShopError,
    add_product,
    current_stock,
    get_or_create_customer,
    record_adjustment,
    record_credit_sale,
    record_debt_payment,
    record_return,
    record_restock,
    record_sale,
    stock_levels,
)


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShopCreate(APIModel):
    name: str = Field(min_length=1, max_length=120)
    currency_code: str = Field(default="NGN", min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Shop name cannot be empty.")
        return value


class ProductCreate(APIModel):
    name: str = Field(min_length=1, max_length=120)
    unit_cost: Decimal = Field(ge=0)
    selling_price: Decimal = Field(ge=0)
    opening_stock: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=3)
    reorder_level: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=3)
    lead_time_days: int = Field(default=7, ge=0, le=365)
    unit_name: str = Field(default="unit", min_length=1, max_length=30)

    @field_validator("name", "unit_name")
    @classmethod
    def normalize_text_fields(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("This field cannot be empty.")
        return value


class RestockCreate(APIModel):
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    unit_cost: Decimal = Field(ge=0)
    received_on: date | None = None
    expiry_date: date | None = None
    supplier: str | None = Field(default=None, max_length=120)


class AdjustmentCreate(APIModel):
    quantity: Decimal
    reason: Literal["damaged", "expired", "stolen", "miscount", "found", "other"]
    adjusted_on: date | None = None
    note: str | None = Field(default=None, max_length=200)


class CustomerCreate(APIModel):
    phone: str = Field(min_length=1, max_length=30)
    name: str | None = Field(default=None, max_length=120)


class SaleCreate(APIModel):
    product_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    unit_price: Decimal | None = Field(default=None, ge=0)
    customer_id: int | None = Field(default=None, gt=0)
    credit_due_date: date | None = None
    sold_at: datetime | None = None


class ReturnCreate(APIModel):
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    returned_at: datetime | None = None


class PaymentCreate(APIModel):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    paid_at: datetime | None = None
    note: str | None = Field(default=None, max_length=200)


bearer = HTTPBearer(auto_error=False)


def get_session(request: Request) -> Generator[Session, None, None]:
    factory_lock: Lock = request.app.state.factory_lock
    if request.app.state.session_factory is None:
        with factory_lock:
            if request.app.state.session_factory is None:
                auto_create_schema = os.getenv("RETAIL_AUTO_CREATE_SCHEMA", "true").lower() == "true"
                request.app.state.session_factory = init_db(
                    request.app.state.database_url, manage_schema=auto_create_schema
                )
    with request.app.state.session_factory() as session:
        yield session


def authenticated_shop(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[Session, Depends(get_session)],
) -> Shop:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="A shop API key is required.",
                            headers={"WWW-Authenticate": "Bearer"})
    shop = session.scalar(select(Shop).where(Shop.api_key_hash == _api_key_hash(credentials.credentials)))
    if shop is None:
        raise HTTPException(status_code=401, detail="Invalid shop API key.",
                            headers={"WWW-Authenticate": "Bearer"})
    return shop


ShopAuth = Annotated[Shop, Depends(authenticated_shop)]
DBSession = Annotated[Session, Depends(get_session)]


def _api_key_hash(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def _product_json(product: Product, stock: int | Decimal | None = None) -> dict:
    if stock is None:
        stock = 0
    return {
        "id": product.id,
        "name": product.name,
        "unit_name": product.unit_name,
        "unit_cost": product.unit_cost,
        "selling_price": product.selling_price,
        "reorder_level": product.reorder_level,
        "lead_time_days": product.lead_time_days,
        "stock": stock,
        "active": product.is_active,
    }


def _alert_json(alert: Alert) -> dict:
    return {
        "id": alert.id,
        "kind": alert.kind,
        "subject_key": alert.subject_key,
        "urgency": alert.urgency,
        "message": alert.message,
        "action": alert.action,
        "status": alert.status,
        "created_at": alert.created_at,
        "last_seen_at": alert.last_seen_at,
        "resolved_at": alert.resolved_at,
        "notified_at": alert.notified_at,
    }


def _analytics_dates(start: date | None, end: date | None) -> tuple[date, date]:
    end = end or date.today()
    start = start or end - timedelta(days=89)
    if start > end:
        raise HTTPException(status_code=422, detail="Start date must not be after end date.")
    if (end - start).days > 365:
        raise HTTPException(status_code=422, detail="Analytics date ranges are limited to 366 days.")
    return start, end


def _commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The requested change conflicts with existing shop data.",
        ) from exc


def _shop_error(session: Session, error: ShopError) -> None:
    session.rollback()
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


def create_app(
    database_url: str | None = None,
    signup_token: str | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> FastAPI:
    """Create an API instance; dependencies are injectable for isolated tests."""
    configured_database_url = database_url or os.getenv("DATABASE_URL", "sqlite:///bizengine.db")
    configured_signup_token = signup_token if signup_token is not None else os.getenv("RETAIL_SIGNUP_TOKEN")
    app = FastAPI(
        title="Retail Operations API",
        version="1.0.0",
        summary="Multi-tenant inventory, sales, analytics, customer credit, and operational alerts.",
    )
    app.state.session_factory = session_factory
    app.state.factory_lock = Lock()
    app.state.database_url = configured_database_url
    app.state.signup_token = configured_signup_token

    @app.get("/health/live", tags=["health"])
    def liveness():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def readiness(session: DBSession):
        session.execute(select(1))
        return {"status": "ready"}

    @app.post("/v1/shops", status_code=201, tags=["shops"])
    def create_shop(payload: ShopCreate, request: Request, session: DBSession,
                    x_signup_token: Annotated[str | None, Header()] = None):
        signup_token = request.app.state.signup_token
        if not signup_token:
            raise HTTPException(status_code=503, detail="Shop registration is disabled.")
        if x_signup_token is None or not secrets.compare_digest(x_signup_token, signup_token):
            raise HTTPException(status_code=403, detail="A valid signup token is required.")
        api_key = secrets.token_urlsafe(32)
        shop = Shop(name=payload.name.strip(), currency_code=payload.currency_code,
                    api_key_hash=_api_key_hash(api_key))
        session.add(shop)
        _commit(session)
        return {"shop_id": shop.id, "name": shop.name, "currency_code": shop.currency_code,
                "api_key": api_key}

    @app.get("/v1/shop", tags=["shops"])
    def get_shop(shop: ShopAuth):
        return {"id": shop.id, "name": shop.name, "currency_code": shop.currency_code}

    @app.post("/v1/shop/api-key/rotate", tags=["shops"])
    def rotate_api_key(shop: ShopAuth, session: DBSession):
        api_key = secrets.token_urlsafe(32)
        shop.api_key_hash = _api_key_hash(api_key)
        _commit(session)
        return {"api_key": api_key}

    @app.post("/v1/products", status_code=201, tags=["inventory"])
    def create_product(payload: ProductCreate, shop: ShopAuth, session: DBSession):
        try:
            product = add_product(
                session, shop.id, payload.name, float(payload.unit_cost), float(payload.selling_price),
                opening_stock=payload.opening_stock, reorder_level=payload.reorder_level,
                lead_time_days=payload.lead_time_days, unit_name=payload.unit_name,
            )
        except ShopError as exc:
            _shop_error(session, exc)
        _commit(session)
        return _product_json(product, current_stock(session, product.id))

    @app.get("/v1/products", tags=["inventory"])
    def list_products(shop: ShopAuth, session: DBSession,
                      limit: Annotated[int, Query(ge=1, le=200)] = 100,
                      offset: Annotated[int, Query(ge=0)] = 0):
        products = session.scalars(
            select(Product).where(Product.shop_id == shop.id, Product.is_active)
            .order_by(Product.id).limit(limit).offset(offset)
        ).all()
        stocks = {line.product_id: line.stock for line in stock_levels(session, shop.id)}
        return {"items": [_product_json(product, stocks.get(product.id, 0)) for product in products],
                "limit": limit, "offset": offset}

    @app.post("/v1/products/{product_id}/restocks", status_code=201, tags=["inventory"])
    def restock_product(product_id: int, payload: RestockCreate, shop: ShopAuth, session: DBSession):
        product = session.scalar(select(Product).where(Product.id == product_id, Product.shop_id == shop.id))
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found.")
        try:
            restock = record_restock(
                session, product, payload.quantity, float(payload.unit_cost),
                received_on=payload.received_on, expiry_date=payload.expiry_date, supplier=payload.supplier,
            )
        except ShopError as exc:
            _shop_error(session, exc)
        _commit(session)
        return {"id": restock.id, "product_id": product.id, "quantity": restock.quantity,
                "received_on": restock.received_on, "expiry_date": restock.expiry_date}

    @app.post("/v1/products/{product_id}/adjustments", status_code=201, tags=["inventory"])
    def adjust_stock(product_id: int, payload: AdjustmentCreate, shop: ShopAuth, session: DBSession):
        product = session.scalar(select(Product).where(Product.id == product_id, Product.shop_id == shop.id))
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found.")
        try:
            adjustment = record_adjustment(
                session, product, payload.quantity, payload.reason,
                adjusted_on=payload.adjusted_on, note=payload.note,
            )
        except ShopError as exc:
            _shop_error(session, exc)
        _commit(session)
        return {"id": adjustment.id, "product_id": product.id, "quantity": adjustment.quantity,
                "reason": adjustment.reason, "adjusted_on": adjustment.adjusted_on}

    @app.get("/v1/inventory", tags=["inventory"])
    def inventory(shop: ShopAuth, session: DBSession):
        return {"items": [
            {"product_id": line.product_id, "name": line.name, "stock": line.stock,
             "reorder_level": line.reorder_level, "below_reorder_level": line.below_reorder_level}
            for line in stock_levels(session, shop.id)
        ]}

    @app.post("/v1/customers", status_code=201, tags=["customers"])
    def create_customer(payload: CustomerCreate, shop: ShopAuth, session: DBSession):
        try:
            customer = get_or_create_customer(session, shop.id, payload.phone, payload.name)
        except ShopError as exc:
            _shop_error(session, exc)
        if payload.name and not customer.name:
            customer.name = payload.name.strip()
        _commit(session)
        return {"id": customer.id, "name": customer.name, "phone": customer.phone}

    @app.get("/v1/customers", tags=["customers"])
    def list_customers(shop: ShopAuth, session: DBSession,
                       limit: Annotated[int, Query(ge=1, le=200)] = 100,
                       offset: Annotated[int, Query(ge=0)] = 0):
        customers = session.scalars(
            select(Customer).where(Customer.shop_id == shop.id).order_by(Customer.id).limit(limit).offset(offset)
        ).all()
        return {"items": [{"id": customer.id, "name": customer.name, "phone": customer.phone}
                          for customer in customers], "limit": limit, "offset": offset}

    @app.post("/v1/sales", status_code=201, tags=["sales"])
    def create_sale(payload: SaleCreate, shop: ShopAuth, session: DBSession):
        product = session.scalar(select(Product).where(Product.id == payload.product_id,
                                                        Product.shop_id == shop.id, Product.is_active))
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found.")
        customer = None
        if payload.customer_id is not None:
            customer = session.scalar(select(Customer).where(Customer.id == payload.customer_id,
                                                              Customer.shop_id == shop.id))
            if customer is None:
                raise HTTPException(status_code=404, detail="Customer not found.")
        if payload.credit_due_date is not None and customer is None:
            raise HTTPException(status_code=422, detail="Credit sales require a customer.")
        try:
            if payload.credit_due_date is not None:
                if customer is None:
                    raise HTTPException(status_code=422, detail="Credit sales require a customer.")
                result = record_credit_sale(
                    session, product, customer, payload.quantity, payload.credit_due_date,
                    unit_price=float(payload.unit_price) if payload.unit_price is not None else None,
                    sold_at=payload.sold_at,
                )
                debt = result.debt
            else:
                result = record_sale(
                    session, product, payload.quantity,
                    unit_price=float(payload.unit_price) if payload.unit_price is not None else None,
                    sold_at=payload.sold_at, customer=customer,
                )
                debt = None
        except ShopError as exc:
            _shop_error(session, exc)
        _commit(session)
        return {
            "sale_id": result.sale.id,
            "product_id": product.id,
            "product": product.name,
            "customer_id": customer.id if customer else None,
            "quantity": result.sale.quantity,
            "unit_price": result.sale.unit_price,
            "sold_at": result.sale.sold_at,
            "warning": result.warning,
            "debt_id": debt.id if debt else None,
            "debt_amount": debt.principal_kobo / 100 if debt else None,
            "debt_due_date": debt.due_date if debt else None,
        }

    @app.get("/v1/sales", tags=["sales"])
    def list_sales(shop: ShopAuth, session: DBSession,
                   start: date | None = None, end: date | None = None,
                   limit: Annotated[int, Query(ge=1, le=200)] = 100,
                   offset: Annotated[int, Query(ge=0)] = 0):
        query = select(Sale, Product, Customer).join(Product, Product.id == Sale.product_id).outerjoin(
            Customer, Customer.id == Sale.customer_id
        ).where(Sale.shop_id == shop.id)
        if start is not None:
            query = query.where(Sale.sold_at >= datetime.combine(start, datetime.min.time()))
        if end is not None:
            query = query.where(Sale.sold_at < datetime.combine(date.fromordinal(end.toordinal() + 1),
                                                                 datetime.min.time()))
        rows = session.execute(query.order_by(Sale.sold_at.desc(), Sale.id.desc()).limit(limit).offset(offset)).all()
        return {"items": [
            {"sale_id": sale.id, "product_id": product.id, "product": product.name,
             "customer_id": customer.id if customer else None, "customer_name": customer.name if customer else None,
             "quantity": sale.quantity, "unit_price": sale.unit_price, "revenue": sale.revenue,
             "profit": sale.profit, "sold_at": sale.sold_at}
            for sale, product, customer in rows
        ], "limit": limit, "offset": offset}

    @app.post("/v1/sales/{sale_id}/returns", status_code=201, tags=["sales"])
    def return_sale(sale_id: int, payload: ReturnCreate, shop: ShopAuth, session: DBSession):
        sale = session.scalar(select(Sale).where(Sale.id == sale_id, Sale.shop_id == shop.id))
        if sale is None:
            raise HTTPException(status_code=404, detail="Sale not found.")
        try:
            result = record_return(session, sale, payload.quantity, returned_at=payload.returned_at)
        except ShopError as exc:
            _shop_error(session, exc)
        _commit(session)
        return {"return_sale_id": result.sale.id, "original_sale_id": sale.id,
                "quantity": result.sale.quantity, "revenue": result.sale.revenue}

    @app.get("/v1/analytics/sales", tags=["analytics"])
    def sales_analytics(shop: ShopAuth, session: DBSession,
                        freq: Literal["D", "W", "M"] = "D",
                        start: date | None = None, end: date | None = None):
        start, end = _analytics_dates(start, end)
        frame = sales_frame(session, shop.id, start=start, end=end)
        records = by_period(frame, freq, start=start, end=end).to_dict(orient="records")
        return {"currency_code": shop.currency_code, "start": start, "end": end,
                "summary": summary(frame), "periods": records}

    @app.get("/v1/analytics/products", tags=["analytics"])
    def product_analytics(shop: ShopAuth, session: DBSession,
                          start: date | None = None, end: date | None = None):
        start, end = _analytics_dates(start, end)
        frame = sales_frame(session, shop.id, start=start, end=end)
        ranking = product_ranking(frame)
        return {"currency_code": shop.currency_code, "start": start, "end": end,
                "products": ranking.to_dict(orient="records") if not ranking.empty else []}

    @app.get("/v1/analytics/weekdays", tags=["analytics"])
    def weekday_analytics(shop: ShopAuth, session: DBSession,
                          start: date | None = None, end: date | None = None):
        start, end = _analytics_dates(start, end)
        frame = sales_frame(session, shop.id, start=start, end=end)
        patterns = weekday_pattern(frame).to_dict(orient="records") if not frame.empty else []
        return {"currency_code": shop.currency_code, "start": start, "end": end, "weekdays": patterns}

    @app.get("/v1/analytics/debts", tags=["analytics"])
    def debt_analytics(shop: ShopAuth, session: DBSession,
                       as_of: date | None = None,
                       due_soon_days: Annotated[int, Query(ge=0, le=365)] = 7):
        return debt_dashboard(session, shop.id, as_of=as_of, due_soon_days=due_soon_days)

    @app.get("/v1/debts", tags=["debts"])
    def list_debts(shop: ShopAuth, session: DBSession,
                   state: Literal["all", "open", "overdue", "due_today", "due_soon", "paid", "customer_credit"] = "all",
                   as_of: date | None = None,
                   limit: Annotated[int, Query(ge=1, le=200)] = 100,
                   offset: Annotated[int, Query(ge=0)] = 0):
        debts = debt_dashboard(session, shop.id, as_of=as_of)["debts"]
        filtered = debts if state == "all" else [debt for debt in debts if debt["status"] == state]
        return {"items": filtered[offset:offset + limit], "total": len(filtered),
                "limit": limit, "offset": offset}

    @app.post("/v1/debts/{debt_id}/payments", status_code=201, tags=["debts"])
    def pay_debt(debt_id: int, payload: PaymentCreate, shop: ShopAuth, session: DBSession):
        debt = session.scalar(
            select(CustomerDebt).join(Sale, Sale.id == CustomerDebt.sale_id)
            .where(CustomerDebt.id == debt_id, Sale.shop_id == shop.id)
        )
        if debt is None:
            raise HTTPException(status_code=404, detail="Customer debt not found.")
        try:
            entry = record_debt_payment(session, debt, payload.amount,
                                        paid_at=payload.paid_at, note=payload.note)
        except ShopError as exc:
            _shop_error(session, exc)
        _commit(session)
        return {"payment_id": entry.id, "debt_id": debt.id, "amount": entry.amount_kobo / 100,
                "paid_at": entry.recorded_at, "note": entry.note}

    @app.get("/v1/debts/{debt_id}/payments", tags=["debts"])
    def debt_payments(debt_id: int, shop: ShopAuth, session: DBSession):
        debt = session.scalar(
            select(CustomerDebt).join(Sale, Sale.id == CustomerDebt.sale_id)
            .where(CustomerDebt.id == debt_id, Sale.shop_id == shop.id)
        )
        if debt is None:
            raise HTTPException(status_code=404, detail="Customer debt not found.")
        entries = session.scalars(
            select(DebtEntry).where(DebtEntry.debt_id == debt.id).order_by(DebtEntry.recorded_at, DebtEntry.id)
        ).all()
        return {"debt_id": debt.id, "items": [
            {"id": entry.id, "kind": entry.kind, "amount": entry.amount_kobo / 100,
             "recorded_at": entry.recorded_at, "note": entry.note}
            for entry in entries
        ]}

    @app.get("/v1/dashboard", tags=["analytics"])
    def dashboard(shop: ShopAuth, session: DBSession,
                  as_of: date | None = None):
        as_of = as_of or date.today()
        sales_start = as_of - timedelta(days=29)
        levels = stock_levels(session, shop.id)
        return {
            "shop": {"id": shop.id, "name": shop.name, "currency_code": shop.currency_code},
            "sales": {"start": sales_start, "end": as_of,
                      **summary(sales_frame(session, shop.id, start=sales_start, end=as_of))},
            "inventory": {"product_count": len(levels),
                          "low_stock_count": sum(1 for line in levels if line.stock <= line.reorder_level)},
            "debts": debt_dashboard(session, shop.id, as_of=as_of),
        }

    @app.post("/v1/alerts/run", tags=["alerts"])
    def process_alerts(shop: ShopAuth, session: DBSession, as_of: date | None = None):
        result = run_alerts(session, shop.id, as_of or date.today())
        _commit(session)
        return {"opened": [_alert_json(alert) for alert in result.opened],
                "resolved": [_alert_json(alert) for alert in result.resolved]}

    @app.get("/v1/alerts", tags=["alerts"])
    def list_alerts(shop: ShopAuth, session: DBSession,
                    state: Literal["open", "resolved"] = "open",
                    limit: Annotated[int, Query(ge=1, le=200)] = 100,
                    offset: Annotated[int, Query(ge=0)] = 0):
        alerts = session.scalars(
            select(Alert).where(Alert.shop_id == shop.id, Alert.status == state)
            .order_by(Alert.created_at.desc(), Alert.id.desc()).limit(limit).offset(offset)
        ).all()
        return {"items": [_alert_json(alert) for alert in alerts], "limit": limit, "offset": offset}

    @app.get("/v1/alerts/due", tags=["alerts"])
    def alerts_due(shop: ShopAuth, session: DBSession):
        now = datetime.now()
        return {"items": [_alert_json(alert) for alert in due_now(session, shop.id, now)], "as_of": now}

    @app.get("/v1/alerts/weekly", tags=["alerts"])
    def weekly_alerts(shop: ShopAuth, session: DBSession):
        return {"items": [_alert_json(alert) for alert in weekly_digest(session, shop.id)]}

    @app.post("/v1/alerts/{alert_id}/acknowledge", tags=["alerts"])
    def acknowledge_alert(alert_id: int, shop: ShopAuth, session: DBSession):
        alert = session.scalar(select(Alert).where(Alert.id == alert_id, Alert.shop_id == shop.id,
                                                   Alert.status == "open"))
        if alert is None:
            raise HTTPException(status_code=404, detail="Open alert not found.")
        alert.notified_at = datetime.now()
        _commit(session)
        return _alert_json(alert)

    return app


app = create_app()
