import os
import tempfile
import unittest

import app as petstock


class InventoryWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.dir.name, "test.db")
        petstock.app.config.update(TESTING=True, DATABASE=self.db_path, SECRET_KEY="test")
        petstock.init_database()
        self.client = petstock.app.test_client()
        self.client.get("/login")

    def tearDown(self):
        self.dir.cleanup()

    def csrf_headers(self):
        with self.client.session_transaction() as session:
            return {"X-CSRF-Token": session["_csrf_token"]}

    def login(self, username, password):
        response = self.client.post("/login", data={"username": username, "password": password}, headers=self.csrf_headers())
        self.client.get("/")  # creates the fresh token after session.clear() at login
        return response

    def create_request(self, quantity):
        self.login("siam", "branch123")
        response = self.client.post("/api/requisitions", json={"note": "ทดสอบ", "items": [{"product_id": 1, "quantity": quantity}]}, headers=self.csrf_headers())
        self.assertEqual(response.status_code, 201)
        self.client.post("/logout", headers=self.csrf_headers())
        self.client.get("/login")
        return response.get_json()["id"]

    def stock_total(self, state, location):
        with petstock.app.app_context():
            return petstock.db().execute("SELECT COALESCE(SUM(quantity),0) FROM stock_batches WHERE product_id=1 AND state=? AND location_id=?", (state, location)).fetchone()[0]

    def test_approve_then_receive_is_idempotent(self):
        request_id = self.create_request(20)
        self.login("admin", "admin123")
        self.assertEqual(self.client.post(f"/api/requisitions/{request_id}/approve", headers=self.csrf_headers()).status_code, 200)
        self.assertEqual(self.stock_total("available", 1), 80)
        self.assertEqual(self.stock_total("in_transfer", 1), 20)
        self.assertEqual(self.client.post(f"/api/requisitions/{request_id}/approve", headers=self.csrf_headers()).status_code, 409)
        self.assertEqual(self.stock_total("available", 1), 80)
        self.client.post("/logout", headers=self.csrf_headers()); self.client.get("/login"); self.login("siam", "branch123")
        self.assertEqual(self.client.post(f"/api/requisitions/{request_id}/receive", headers=self.csrf_headers()).status_code, 200)
        self.assertEqual(self.stock_total("available", 2), 25)
        self.assertEqual(self.client.post(f"/api/requisitions/{request_id}/receive", headers=self.csrf_headers()).status_code, 409)
        self.assertEqual(self.stock_total("available", 2), 25)

    def test_insufficient_stock_does_not_change_anything(self):
        request_id = self.create_request(101)
        self.login("admin", "admin123")
        response = self.client.post(f"/api/requisitions/{request_id}/approve", headers=self.csrf_headers())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.stock_total("available", 1), 100)
        self.assertEqual(self.stock_total("in_transfer", 1), 0)

    def test_branch_cannot_approve_or_view_another_branch_request(self):
        request_id = self.create_request(2)
        self.login("ladprao", "branch123")
        self.assertEqual(self.client.get(f"/api/requisitions/{request_id}").status_code, 403)
        self.assertEqual(self.client.post(f"/api/requisitions/{request_id}/approve", headers=self.csrf_headers()).status_code, 403)

    def test_cancel_pending_request_does_not_change_stock(self):
        request_id = self.create_request(4)
        self.login("siam", "branch123")
        response = self.client.post(f"/api/requisitions/{request_id}/cancel", headers=self.csrf_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.stock_total("available", 1), 100)
        self.assertEqual(self.stock_total("in_transfer", 1), 0)

    def test_state_changing_api_rejects_request_without_csrf_token(self):
        self.login("siam", "branch123")
        response = self.client.post("/api/requisitions", json={"items": [{"product_id": 1, "quantity": 1}]})
        self.assertEqual(response.status_code, 400)

    def test_health_check_reports_ready_database(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
