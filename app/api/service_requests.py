"""Service request endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_customer
from app.core.database import get_db
from app.models import ServiceRequest
from app.services.service_request_service import (
    get_customer_service_request,
    get_customer_service_requests,
)

router = APIRouter(prefix="/service-requests", tags=["Service Requests"])


def _service_request_response(
    service_request: ServiceRequest,
) -> dict[str, object]:
    return {
        "request_id": service_request.request_id,
        "request_type": service_request.request_type,
        "status": service_request.status,
        "created_at": service_request.created_at,
        "completed_at": service_request.completed_at,
    }


@router.get("/me")
async def get_my_service_requests(
    customer_id: Annotated[int, Depends(get_current_customer)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, list[dict[str, object]]]:
    service_requests = await get_customer_service_requests(session, customer_id)
    return {
        "service_requests": [
            _service_request_response(service_request)
            for service_request in service_requests
        ]
    }


@router.get("/{request_id}")
async def get_service_request(
    request_id: str,
    customer_id: Annotated[int, Depends(get_current_customer)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    service_request = await get_customer_service_request(
        session,
        customer_id,
        request_id,
    )
    if service_request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service request not found",
        )

    return _service_request_response(service_request)
