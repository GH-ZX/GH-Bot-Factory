import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.core.database import (
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDMixin,
)
from packages.tenants.models import JSON_TYPE, Tenant, User


class Bot(Base, UUIDMixin, TimestampMixin, SoftDeleteMixin):
    """Represents an active or configured Telegram Bot associated with a Tenant."""

    __tablename__ = "bots"
    __table_args__ = (
        UniqueConstraint("telegram_bot_id", name="uq_bot_telegram_id"),
        Index("ix_bot_tenant_enabled", "tenant_id", "is_enabled"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    telegram_bot_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        unique=True,
        index=True,
    )
    username: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    token_secret_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSON_TYPE,
        default=lambda: {
            "currency": "USD",
            "locale": "en",
            "branding": {
                "welcome_text": "Welcome to our store!",
                "support_contact": "@support",
            },
            "enabled_modules": ["catalog", "orders", "account"],
        },
        nullable=False,
    )

    tenant: Mapped["Tenant"] = relationship("Tenant")
    user_bindings: Mapped[list["TenantTelegramUser"]] = relationship(
        "TenantTelegramUser",
        back_populates="bot",
        cascade="all, delete-orphan",
    )


class TenantTelegramUser(Base, UUIDMixin, TimestampMixin):
    """Maps a Telegram user account to a specific Tenant and Bot context.
    
    Prevents assuming Telegram user_id globally identifies a customer across all tenants.
    """

    __tablename__ = "tenant_telegram_users"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "bot_id",
            "telegram_user_id",
            name="uq_tenant_bot_telegram_user",
        ),
        Index("ix_tenant_bot_user", "tenant_id", "bot_id", "telegram_user_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    telegram_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    language_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    tenant: Mapped["Tenant"] = relationship("Tenant")
    bot: Mapped["Bot"] = relationship("Bot", back_populates="user_bindings")
    user: Mapped["User"] = relationship("User")
