import os
import tempfile
import unittest
from unittest.mock import patch

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
            return petstock.db().execute("SELECT COALESCE(SUM(quantity),0) AS total FROM stock_batches WHERE product_id=1 AND state=? AND location_id=?", (state, location)).fetchone()['total']

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

    def test_vercel_missing_postgres_never_opens_sqlite(self):
        with patch.dict(os.environ, {'VERCEL': '1'}), patch.object(petstock, 'USING_POSTGRES', False):
            with patch.object(petstock, 'open_connection') as connect:
                with self.assertRaisesRegex(RuntimeError, 'PostgreSQL DATABASE_URL'):
                    petstock.init_database()
                connect.assert_not_called()

    def test_startup_accepts_dictionary_rows(self):
        original_connect = petstock.open_connection

        def connect():
            conn = original_connect()
            if not petstock.USING_POSTGRES:
                conn.row_factory = lambda cursor, values: dict(zip(
                    [column[0] for column in cursor.description], values))
            return conn

        with patch.object(petstock, 'open_connection', connect):
            petstock.init_database()
            petstock.init_database()
        self.assertEqual(self.stock_total('available', 1), 100)

    def test_restart_preserves_stock_and_passwords(self):
        with petstock.app.app_context():
            conn = petstock.db()
            conn.execute("UPDATE stock_batches SET quantity=73 WHERE product_id=1 AND location_id=1")
            password = conn.execute("SELECT password FROM users WHERE id=1").fetchone()['password']
        petstock.init_database()
        petstock.init_database()
        self.assertEqual(self.stock_total('available', 1), 73)
        with petstock.app.app_context():
            self.assertEqual(petstock.row('SELECT COUNT(*) AS n FROM users')['n'], 4)
            self.assertEqual(petstock.row('SELECT password FROM users WHERE id=1')['password'], password)

    def test_pages_and_csv_after_login(self):
        self.login('admin', 'admin123')
        for path in ('/', '/products', '/stock', '/stock?view=lots', '/requisitions', '/receipts', '/admin', '/reports'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        export = self.client.get('/reports/stock.csv').get_data(as_text=True)
        self.assertIn('DOG-AD-1KG', export)

    def test_partial_old_startup_preserves_existing_stock(self):
        with petstock.app.app_context():
            petstock.db().execute('DELETE FROM users')
            petstock.db().execute('UPDATE stock_batches SET quantity=73 WHERE product_id=1 AND location_id=1')
        petstock.init_database()
        self.assertEqual(self.stock_total('available', 1), 73)
        self.assertEqual(self.login('admin', 'admin123').status_code, 302)


if __name__ == "__main__":
    unittest.main()
