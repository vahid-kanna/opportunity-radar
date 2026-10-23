import unittest
import json
import db
import scanner
from server import app

class TestOpportunityRadar(unittest.TestCase):
    def setUp(self):
        db.init_db()
        self.client = app.test_client()

    def test_health(self):
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get("status"), "healthy")

    def test_index_renders(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Opportunity Radar", res.data)
        self.assertIn(b"GLOBAL REMOTE USD", res.data)

    def test_api_opportunities(self):
        res = self.client.get("/api/opportunities")
        self.assertEqual(res.status_code, 200)
        items = res.get_json()
        self.assertIsInstance(items, list)
        self.assertGreater(len(items), 0)

    def test_favicon(self):
        res = self.client.get("/favicon.ico")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content_type, "image/svg+xml")

if __name__ == "__main__":
    unittest.main()
