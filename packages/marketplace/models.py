from __future__ import annotations

import enum
from typing import Any

from sqlalchemy import (
    Enum,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from packages.core.database import Base, TimestampMixin, UUIDMixin
from packages.tenants.models import JSON_TYPE


class InquiryStatus(str, enum.Enum):
    NEW = "NEW"
    CONTACTED = "CONTACTED"
    QUOTED = "QUOTED"
    CONVERTED = "CONVERTED"
    ARCHIVED = "ARCHIVED"


class ContactMethod(str, enum.Enum):
    TELEGRAM = "TELEGRAM"
    WHATSAPP = "WHATSAPP"
    EMAIL = "EMAIL"


class CustomerInquiry(Base, UUIDMixin, TimestampMixin):
    """Prospective customer configuration inquiry captured before tenant creation."""

    __tablename__ = "customer_inquiries"
    __table_args__ = (
        Index("ix_customer_inquiries_status_created", "status", "created_at"),
        Index("ix_customer_inquiries_contact", "contact_method", "contact_handle"),
    )

    contact_method: Mapped[ContactMethod] = mapped_column(
        Enum(ContactMethod, native_enum=False, length=20),
        nullable=False,
        default=ContactMethod.TELEGRAM,
    )
    contact_handle: Mapped[str] = mapped_column(String(120), nullable=False)
    project_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    estimated_quote: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    status: Mapped[InquiryStatus] = mapped_column(
        Enum(InquiryStatus, native_enum=False, length=20),
        nullable=False,
        default=InquiryStatus.NEW,
    )
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
