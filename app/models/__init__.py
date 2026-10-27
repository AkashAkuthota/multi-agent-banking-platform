"""SQLAlchemy models for the existing application tables."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for SQLAlchemy declarative models."""


from app.models.account import Account
from app.models.banking_command import (
    ServiceRequestProposal,
    ServiceRequestProposalAuditEvent,
    TransferAuditEvent,
    TransferRequest,
)
from app.models.credentials import CustomerCredential
from app.models.chat_session import ChatSession
from app.models.customer import Customer
from app.models.daily_limit import (
    DailyAccountLimit,
    DailyLimitAuditEvent,
    DailyLimitChangeRequest,
)
from app.models.service_request import ServiceRequest
from app.models.transaction import Transaction

__all__ = [
    "Account",
    "Base",
    "ChatSession",
    "Customer",
    "CustomerCredential",
    "DailyAccountLimit",
    "DailyLimitAuditEvent",
    "DailyLimitChangeRequest",
    "ServiceRequestProposal",
    "ServiceRequestProposalAuditEvent",
    "ServiceRequest",
    "Transaction",
    "TransferAuditEvent",
    "TransferRequest",
]
