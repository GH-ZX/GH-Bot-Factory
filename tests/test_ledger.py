from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.exceptions import (
    InsufficientFundsError,
    TenantAccessViolationError,
)
from packages.payments.models import TransactionType
from packages.payments.service import LedgerService
from packages.tenants.models import Tenant, User


@pytest.mark.asyncio
async def test_wallet_credit_debit_and_audit(db_session: AsyncSession):
    # Setup Tenant and User
    tenant = Tenant(name="Finance Tenant", slug="fin-tenant")
    user = User(username="wallet_user")
    db_session.add_all([tenant, user])
    await db_session.flush()

    # 1. Create Wallet
    wallet = await LedgerService.get_or_create_wallet(
        session=db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        currency="USD",
    )
    assert wallet.balance == Decimal("0.00")

    # 2. Credit 100.00
    tx_credit = await LedgerService.credit(
        session=db_session,
        wallet=wallet,
        amount=Decimal("100.00"),
        reference_id="dep-001",
        reference_type="TOPUP",
        description="Initial deposit",
    )
    assert wallet.balance == Decimal("100.00")
    assert tx_credit.transaction_type == TransactionType.CREDIT
    assert tx_credit.balance_before == Decimal("0.00")
    assert tx_credit.balance_after == Decimal("100.00")

    # 3. Debit 35.50
    tx_debit = await LedgerService.debit(
        session=db_session,
        wallet=wallet,
        amount=Decimal("35.50"),
        reference_id="ord-001",
        reference_type="ORDER",
        description="Purchase order #001",
    )
    assert wallet.balance == Decimal("64.50")
    assert tx_debit.transaction_type == TransactionType.DEBIT
    assert tx_debit.balance_before == Decimal("100.00")
    assert tx_debit.balance_after == Decimal("64.50")

    # 4. Refund 10.00
    tx_refund = await LedgerService.refund(
        session=db_session,
        wallet=wallet,
        amount=Decimal("10.00"),
        reference_id="ord-001",
        reference_type="ORDER_REFUND",
        description="Partial return",
    )
    assert wallet.balance == Decimal("74.50")
    assert tx_refund.transaction_type == TransactionType.REFUND

    # 5. Reconstruct and Audit balance
    reconstructed, is_valid = await LedgerService.reconstruct_and_verify_balance(
        session=db_session,
        wallet_id=wallet.id,
        tenant_id=tenant.id,
    )
    assert is_valid is True
    assert reconstructed == Decimal("74.50")


@pytest.mark.asyncio
async def test_wallet_overdraw_protection(db_session: AsyncSession):
    tenant = Tenant(name="Overdraw Tenant", slug="overdraw-tenant")
    user = User(username="poor_user")
    db_session.add_all([tenant, user])
    await db_session.flush()

    wallet = await LedgerService.get_or_create_wallet(
        session=db_session,
        tenant_id=tenant.id,
        user_id=user.id,
    )

    # Attempt to debit without funds
    with pytest.raises(InsufficientFundsError):
        await LedgerService.debit(
            session=db_session,
            wallet=wallet,
            amount=Decimal("10.00"),
        )

    assert wallet.balance == Decimal("0.00")


@pytest.mark.asyncio
async def test_ledger_tenant_isolation_violation(db_session: AsyncSession):
    tenant_a = Tenant(name="Tenant Alpha", slug="t-alpha")
    tenant_b = Tenant(name="Tenant Beta", slug="t-beta")
    user = User(username="user1")
    db_session.add_all([tenant_a, tenant_b, user])
    await db_session.flush()

    wallet = await LedgerService.get_or_create_wallet(
        session=db_session,
        tenant_id=tenant_a.id,
        user_id=user.id,
    )

    # Attempting to verify Tenant A's wallet using Tenant B's identity
    with pytest.raises(TenantAccessViolationError):
        await LedgerService.reconstruct_and_verify_balance(
            session=db_session,
            wallet_id=wallet.id,
            tenant_id=tenant_b.id,
        )
