"""Service request operations with customer ownership enforced in SQL."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ServiceRequest


async def get_customer_service_requests(
    session: AsyncSession,
    customer_id: int,
) -> list[ServiceRequest]:
    result = await session.scalars(
        select(ServiceRequest)
        .where(ServiceRequest.customer_id == customer_id)
        .order_by(ServiceRequest.created_at.desc())
    )
    return list(result.all())


async def get_customer_service_request(
    session: AsyncSession,
    customer_id: int,
    request_id: str,
) -> ServiceRequest | None:
    return await session.scalar(
        select(ServiceRequest).where(
            ServiceRequest.request_id == request_id,
            ServiceRequest.customer_id == customer_id,
        )
    )
