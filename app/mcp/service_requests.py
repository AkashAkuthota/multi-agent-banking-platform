"""Service Requests MCP tools."""

from typing import TYPE_CHECKING

from app.core.database import async_session
from app.mcp.context import get_authenticated_customer_id
from app.services.service_request_service import get_customer_service_requests

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


async def get_my_service_requests() -> list[dict[str, str | None]]:
    """Return service requests belonging to the authenticated customer."""
    customer_id = get_authenticated_customer_id()
    async with async_session() as session:
        service_requests = await get_customer_service_requests(session, customer_id)

    return [
        {
            "request_id": service_request.request_id,
            "request_type": service_request.request_type,
            "status": service_request.status,
            "created_at": service_request.created_at.isoformat(),
            "completed_at": (
                service_request.completed_at.isoformat()
                if service_request.completed_at is not None
                else None
            ),
        }
        for service_request in service_requests
    ]


def register_service_request_tools(server: "FastMCP") -> None:
    server.tool(
        name="get_my_service_requests",
        description="Get service requests belonging to the authenticated customer.",
    )(get_my_service_requests)
