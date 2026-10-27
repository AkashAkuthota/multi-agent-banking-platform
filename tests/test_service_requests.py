"""Tests for service request API behavior."""

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api.service_requests import get_my_service_requests, get_service_request
from app.models import ServiceRequest


class ServiceRequestEndpointTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_service_request() -> ServiceRequest:
        return ServiceRequest(
            id=3,
            request_id="SR_003",
            customer_id=42,
            request_type="STATEMENT",
            status="PENDING",
            created_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
            completed_at=None,
        )

    async def test_list_endpoint_calls_service_and_formats_response(self) -> None:
        service_request = self.make_service_request()
        session = AsyncMock()
        with patch(
            "app.api.service_requests.get_customer_service_requests",
            new=AsyncMock(return_value=[service_request]),
        ) as service:
            response = await get_my_service_requests(42, session)

        service.assert_awaited_once_with(session, 42)
        self.assertEqual(
            response,
            {
                "service_requests": [
                    {
                        "request_id": "SR_003",
                        "request_type": "STATEMENT",
                        "status": "PENDING",
                        "created_at": datetime(
                            2026, 1, 3, tzinfo=timezone.utc
                        ),
                        "completed_at": None,
                    }
                ]
            },
        )

    async def test_detail_endpoint_calls_service_and_formats_response(self) -> None:
        service_request = self.make_service_request()
        session = AsyncMock()
        with patch(
            "app.api.service_requests.get_customer_service_request",
            new=AsyncMock(return_value=service_request),
        ) as service:
            response = await get_service_request("SR_003", 42, session)

        service.assert_awaited_once_with(session, 42, "SR_003")
        self.assertEqual(response["request_id"], "SR_003")
        self.assertEqual(response["request_type"], "STATEMENT")
        self.assertEqual(response["status"], "PENDING")
        self.assertIsNone(response["completed_at"])

    async def test_detail_endpoint_preserves_404_for_missing_or_non_owner(self) -> None:
        session = AsyncMock()
        with patch(
            "app.api.service_requests.get_customer_service_request",
            new=AsyncMock(return_value=None),
        ) as service:
            with self.assertRaises(HTTPException) as error:
                await get_service_request("SR_003", 99, session)

        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(error.exception.detail, "Service request not found")
        service.assert_awaited_once_with(session, 99, "SR_003")

    async def test_detail_endpoint_returns_404_for_nonexistent_request(self) -> None:
        session = AsyncMock()
        with patch(
            "app.api.service_requests.get_customer_service_request",
            new=AsyncMock(return_value=None),
        ) as service:
            with self.assertRaises(HTTPException) as error:
                await get_service_request("SR_MISSING", 42, session)

        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(error.exception.detail, "Service request not found")
        service.assert_awaited_once_with(session, 42, "SR_MISSING")


if __name__ == "__main__":
    unittest.main()
