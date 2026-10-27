"""Tests for privacy-conscious request telemetry."""

import logging
import unittest

from fastapi.testclient import TestClient

from app.main import app, create_app


class ObservabilityTests(unittest.TestCase):
    def test_request_id_is_returned_and_logs_exclude_sensitive_inputs(self) -> None:
        secret_email = "customer@example.com"
        secret_token = "synthetic-sensitive-token"
        with self.assertLogs("app.http", level=logging.INFO) as captured:
            with TestClient(app) as client:
                response = client.get(
                    f"/health?email={secret_email}",
                    headers={"Authorization": f"Bearer {secret_token}"},
                )

        request_id = response.headers["X-Request-ID"]
        self.assertTrue(request_id)
        self.assertEqual(response.json(), {"status": "ok"})
        log_output = "\n".join(captured.output)
        self.assertIn(request_id, log_output)
        self.assertIn("/health", log_output)
        self.assertNotIn(secret_email, log_output)
        self.assertNotIn(secret_token, log_output)

    def test_interactive_docs_can_be_disabled(self) -> None:
        with TestClient(create_app(docs_enabled=False)) as client:
            response = client.get("/docs")

        self.assertEqual(response.status_code, 404)

    def test_same_origin_banking_console_is_available(self) -> None:
        with TestClient(app) as client:
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Secure Multi-Agent Banking Assistant", response.text)
        self.assertIn("crypto.randomUUID", response.text)
        self.assertIn("/chat/pending-proposal/", response.text)
        self.assertNotIn("localStorage", response.text)


if __name__ == "__main__":
    unittest.main()
