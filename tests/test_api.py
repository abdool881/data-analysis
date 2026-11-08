from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from retail_ops.api import create_app
from retail_ops.database import init_db


class RetailAPITests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "retail.db"
        self.session_factory = init_db(f"sqlite:///{database_path}")
        self.client = TestClient(create_app(signup_token="test-signup", session_factory=self.session_factory))

    def tearDown(self):
        self.client.close()
        self.temp_dir.cleanup()

    def create_shop(self, name: str):
        response = self.client.post(
            "/v1/shops",
            headers={"X-Signup-Token": "test-signup"},
            json={"name": name, "currency_code": "USD"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        shop = response.json()
        return shop, {"Authorization": f"Bearer {shop['api_key']}"}

    def test_customer_credit_inventory_alerts_and_tenant_isolation(self):
        first_shop, first_auth = self.create_shop("First shop")
        _, second_auth = self.create_shop("Second shop")

        product = self.client.post(
            "/v1/products",
            headers=first_auth,
            json={
                "name": "Coffee beans",
                "unit_cost": "8.00",
                "selling_price": "12.50",
                "opening_stock": "4.5",
                "reorder_level": "1",
                "unit_name": "kg",
            },
        )
        self.assertEqual(product.status_code, 201, product.text)
        product_id = product.json()["id"]
        self.assertEqual(product.json()["stock"], 4.5)

        customer = self.client.post(
            "/v1/customers",
            headers=first_auth,
            json={"name": "Taylor Shop", "phone": "+1 (555) 010-1234"},
        )
        self.assertEqual(customer.status_code, 201, customer.text)
        customer_id = customer.json()["id"]
        self.assertEqual(customer.json()["phone"], "15550101234")

        due_date = date.today()
        sale = self.client.post(
            "/v1/sales",
            headers=first_auth,
            json={
                "product_id": product_id,
                "customer_id": customer_id,
                "quantity": "1.25",
                "credit_due_date": due_date.isoformat(),
            },
        )
        self.assertEqual(sale.status_code, 201, sale.text)
        sale_data = sale.json()
        self.assertEqual(sale_data["debt_amount"], 15.63)

        dashboard = self.client.get("/v1/analytics/debts", headers=first_auth).json()
        self.assertEqual(dashboard["currency_code"], "USD")
        self.assertEqual(dashboard["total_outstanding"], 15.63)
        self.assertEqual(dashboard["due_today_count"], 1)
        debt_row = dashboard["debts"][0]
        self.assertEqual(debt_row["product"], "Coffee beans")
        self.assertEqual(debt_row["customer_phone"], "15550101234")

        alerts = self.client.post("/v1/alerts/run", headers=first_auth)
        self.assertEqual(alerts.status_code, 200, alerts.text)
        debt_alert = next(alert for alert in alerts.json()["opened"] if alert["kind"] == "debt_due")
        self.assertIn("Taylor Shop", debt_alert["message"])
        self.assertIn("15550101234", debt_alert["message"])
        self.assertIn("$15.63", debt_alert["message"])

        payment = self.client.post(
            f"/v1/debts/{sale_data['debt_id']}/payments",
            headers=first_auth,
            json={"amount": "5.00", "note": "First installment"},
        )
        self.assertEqual(payment.status_code, 201, payment.text)
        history = self.client.get(f"/v1/debts/{sale_data['debt_id']}/payments", headers=first_auth)
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["items"][0]["amount"], 5.0)
        self.assertEqual(
            self.client.get("/v1/analytics/debts", headers=first_auth).json()["total_outstanding"],
            10.63,
        )

        foreign_product = self.client.post(
            f"/v1/products/{product_id}/restocks",
            headers=second_auth,
            json={"quantity": "1", "unit_cost": "8.00"},
        )
        self.assertEqual(foreign_product.status_code, 404)
        foreign_payment = self.client.post(
            f"/v1/debts/{sale_data['debt_id']}/payments",
            headers=second_auth,
            json={"amount": "1.00"},
        )
        self.assertEqual(foreign_payment.status_code, 404)

    def test_signup_is_gated_and_api_key_rotation_revokes_old_key(self):
        no_token = self.client.post("/v1/shops", json={"name": "Blocked"})
        self.assertEqual(no_token.status_code, 403)

        shop, auth = self.create_shop("Rotatable")
        rotate = self.client.post("/v1/shop/api-key/rotate", headers=auth)
        self.assertEqual(rotate.status_code, 200, rotate.text)
        self.assertEqual(self.client.get("/v1/shop", headers=auth).status_code, 401)
        new_auth = {"Authorization": f"Bearer {rotate.json()['api_key']}"}
        self.assertEqual(self.client.get("/v1/shop", headers=new_auth).json()["id"], shop["shop_id"])

    def test_cash_sale_and_return_are_exposed(self):
        _, auth = self.create_shop("Sales")
        product = self.client.post(
            "/v1/products",
            headers=auth,
            json={"name": "Notebook", "unit_cost": 2, "selling_price": 5, "opening_stock": 3},
        ).json()
        sale = self.client.post(
            "/v1/sales",
            headers=auth,
            json={"product_id": product["id"], "quantity": 2},
        )
        self.assertEqual(sale.status_code, 201, sale.text)
        self.assertIsNone(sale.json()["debt_id"])

        returned = self.client.post(
            f"/v1/sales/{sale.json()['sale_id']}/returns",
            headers=auth,
            json={"quantity": 1},
        )
        self.assertEqual(returned.status_code, 201, returned.text)
        inventory = self.client.get("/v1/inventory", headers=auth).json()["items"][0]
        self.assertEqual(inventory["stock"], 2)

        adjustment = self.client.post(
            f"/v1/products/{product['id']}/adjustments",
            headers=auth,
            json={"quantity": "-0.5", "reason": "damaged"},
        )
        self.assertEqual(adjustment.status_code, 201, adjustment.text)
        inventory_data = self.client.get("/v1/inventory", headers=auth).json()
        inventory = inventory_data["items"][0]
        self.assertEqual(inventory["stock"], 1.5)
        self.assertEqual(inventory_data["summary"]["product_count"], 1)
        self.assertEqual(inventory_data["summary"]["products_in_stock"], 1)
        self.assertEqual(inventory_data["summary"]["stock_cost_value"], 3)
        self.assertEqual(inventory_data["summary"]["potential_sales_value"], 7.5)
        self.assertEqual(inventory_data["summary"]["potential_gross_profit"], 4.5)
        self.assertEqual(inventory["potential_gross_profit"], 4.5)

        sales_analytics = self.client.get("/v1/analytics/sales", headers=auth).json()
        self.assertEqual(sales_analytics["summary"]["revenue"], 5)
        self.assertTrue(sales_analytics["periods"])
        self.assertIsNone(sales_analytics["periods"][0]["change_pct"])
        products = self.client.get("/v1/analytics/products", headers=auth).json()
        self.assertEqual(products["products"][0]["product"], "Notebook")
        weekdays = self.client.get("/v1/analytics/weekdays", headers=auth).json()
        self.assertTrue(weekdays["weekdays"])
        invalid_window = self.client.get(
            "/v1/analytics/sales",
            headers=auth,
            params={"start": "2024-01-01", "end": "2025-01-02"},
        )
        self.assertEqual(invalid_window.status_code, 422)


if __name__ == "__main__":
    unittest.main()
