"""Tests for service request ownership queries."""

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

from app.models import ServiceRequest
from app.services.service_request_service import (
    get_customer_service_request,
    get_customer_service_requests,
)


class ServiceRequestServiceTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_list_query_filters_by_customer_id(self) -> None:
        service_request = self.make_service_request()
        session = AsyncMock()
        result = Mock()
        result.all.return_value = [service_request]
        session.scalars.return_value = result

        requests = await get_customer_service_requests(session, 42)

        self.assertEqual(requests, [service_request])
        statement = session.scalars.call_args.args[0]
        self.assertIn("service_requests.customer_id", str(statement.compile()))
        self.assertEqual(statement.compile().params["customer_id_1"], 42)

    async def test_detail_query_filters_by_request_and_customer(self) -> None:
        service_request = self.make_service_request()
        session = AsyncMock()
        session.scalar.return_value = service_request

        result = await get_customer_service_request(session, 42, "SR_003")

        self.assertIs(result, service_request)
        statement = session.scalar.call_args.args[0]
        sql = str(statement.compile())
        params = statement.compile().params
        self.assertIn("service_requests.request_id", sql)
        self.assertIn("service_requests.customer_id", sql)
        self.assertEqual(params["request_id_1"], "SR_003")
        self.assertEqual(params["customer_id_1"], 42)

    async def test_detail_query_returns_none_for_non_owner(self) -> None:
        session = AsyncMock()
        session.scalar.return_value = None

        result = await get_customer_service_request(session, 99, "SR_003")

        self.assertIsNone(result)
        params = session.scalar.call_args.args[0].compile().params
        self.assertEqual(params["request_id_1"], "SR_003")
        self.assertEqual(params["customer_id_1"], 99)


if __name__ == "__main__":
    unittest.main()
