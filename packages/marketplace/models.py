from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.core.database import Base, TimestampMixin, UUIDMixin
from packages.tenants.models import JSON_TYPE, Tenant


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


class QuoteStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


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

    quotes: Mapped[list[CommercialQuote]] = relationship(
        "CommercialQuote", back_populates="inquiry", order_by="CommercialQuote.version.desc()"
    )


class CommercialQuote(Base, UUIDMixin, TimestampMixin):
    """Versioned commercial proposal created from an inquiry or custom platform deal."""

    __tablename__ = "commercial_quotes"
    __table_args__ = (
        Index("ix_commercial_quotes_number_version", "quote_number", "version", unique=True),
        Index("ix_commercial_quotes_status_created", "status", "created_at"),
        Index("ix_commercial_quotes_inquiry", "inquiry_id"),
    )

    quote_number: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    inquiry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customer_inquiries.id", ondelete="SET NULL"), nullable=True
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    customer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    customer_contact: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[QuoteStatus] = mapped_column(
        Enum(QuoteStatus, native_enum=False, length=20),
        nullable=False,
        default=QuoteStatus.DRAFT,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    total_one_time: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    total_monthly: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lines: Mapped[list[CommercialQuoteLine]] = relationship(
        "CommercialQuoteLine",
        back_populates="quote",
        cascade="all, delete-orphan",
        order_by="CommercialQuoteLine.created_at",
    )
    inquiry: Mapped[CustomerInquiry | None] = relationship("CustomerInquiry", back_populates="quotes")
    tenant: Mapped[Tenant | None] = relationship("Tenant")


class CommercialQuoteLine(Base, UUIDMixin, TimestampMixin):
    """Itemized deliverable or pricing item inside a commercial quote."""

    __tablename__ = "commercial_quote_lines"
    __table_args__ = (Index("ix_commercial_quote_lines_quote_id", "quote_id"),)

    quote_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("commercial_quotes.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False, default="general")
    item_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "one_time" or "recurring"
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    quote: Mapped[CommercialQuote] = relationship("CommercialQuote", back_populates="lines")
