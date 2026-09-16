import asyncio
import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.v1.admin_members import MemberUpdateRequest, update_member
from packages.commerce.checkout import CheckoutService
from packages.commerce.models import Order, Product, ProductVariant
from packages.commerce.state_machine import OrderStatus
from packages.core.auth import AuthenticatedPrincipal, AuthSource
from packages.core.exceptions import InsufficientFundsError
from packages.factory.models import BotProvisioningJob, BotProvisioningStatus
from packages.factory.provisioning import BotProvisioningService, provisioning_fingerprint
from packages.fulfillment.models import FulfillmentJobRecord, FulfillmentJobStatus
from packages.fulfillment.worker import FulfillmentWorker
from packages.payments.models import LedgerTransaction, TransactionType, Wallet
from packages.payments.service import (
    CANONICAL_REFUND_TYPE,
    CANONICAL_SETTLEMENT_TYPE,
    LedgerService,
)
from packages.tenants.models import Membership, Role, Tenant, User

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]
CONCURRENCY_TIMEOUT_SECONDS = 15


async def _bounded_gather(*awaitables):
    return await asyncio.wait_for(
        asyncio.gather(*awaitables),
        timeout=CONCURRENCY_TIMEOUT_SECONDS,
    )


async def _tenant_user_wallet(
    factory: async_sessionmaker[AsyncSession],
    *,
    balance: Decimal,
    currency: str = "USD",
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    async with factory() as session:
        tenant = Tenant(name=f"PG Tenant {uuid.uuid4().hex[:8]}", slug=f"pg-{uuid.uuid4().hex[:12]}")
        user = User(username=f"pg_user_{uuid.uuid4().hex[:10]}")
        session.add_all([tenant, user])
        await session.flush()
        wallet = await LedgerService.get_or_create_wallet(
            session,
            tenant_id=tenant.id,
            user_id=user.id,
            currency=currency,
        )
        if balance > Decimal("0.00"):
            await LedgerService.credit(
                session,
                wallet,
                balance,
                reference_id=uuid.uuid4().hex,
                reference_type="PG_TEST_SEED",
            )
        await session.commit()
        return tenant.id, user.id, wallet.id


async def test_concurrent_debits_serialize_and_prevent_overspend(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two stale readers must not both spend the same wallet balance."""
    tenant_id, _, wallet_id = await _tenant_user_wallet(
        postgres_session_factory,
        balance=Decimal("100.00"),
    )
    ready = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()

    async def spend(index: int) -> str:
        async with postgres_session_factory() as session:
            wallet = await session.get(Wallet, wallet_id)
            assert wallet is not None
            assert wallet.balance == Decimal("100.00")
            ready[index].set()
            await release.wait()
            try:
                await LedgerService.debit(
                    session,
                    wallet,
                    Decimal("80.00"),
                    reference_id=f"spend-{index}",
                    reference_type="PG_CONCURRENCY_TEST",
                )
                await session.commit()
                return "committed"
            except InsufficientFundsError:
                await session.rollback()
                return "insufficient"

    tasks = [asyncio.create_task(spend(0)), asyncio.create_task(spend(1))]
    await _bounded_gather(*(event.wait() for event in ready))
    release.set()
    outcomes = await _bounded_gather(*tasks)

    assert sorted(outcomes) == ["committed", "insufficient"]

    async with postgres_session_factory() as session:
        wallet = await session.get(Wallet, wallet_id)
        assert wallet is not None
        assert wallet.balance == Decimal("20.00")
        debits = int(
            (
                await session.scalar(
                    select(func.count())
                    .select_from(LedgerTransaction)
                    .where(
                        LedgerTransaction.tenant_id == tenant_id,
                        LedgerTransaction.wallet_id == wallet_id,
                        LedgerTransaction.transaction_type == TransactionType.DEBIT,
                        LedgerTransaction.reference_type == "PG_CONCURRENCY_TEST",
                    )
                )
            )
            or 0
        )
        assert debits == 1
        reconstructed, valid = await LedgerService.reconstruct_and_verify_balance(
            session,
            wallet_id,
            tenant_id,
        )
        assert valid is True
        assert reconstructed == Decimal("20.00")


async def test_duplicate_checkout_is_exactly_once_under_concurrency(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id, user_id, wallet_id = await _tenant_user_wallet(
        postgres_session_factory,
        balance=Decimal("100.00"),
    )
    async with postgres_session_factory() as session:
        product = Product(tenant_id=tenant_id, title="Concurrent Product")
        session.add(product)
        await session.flush()
        variant = ProductVariant(
            product_id=product.id,
            sku=f"PG-{uuid.uuid4().hex[:8]}",
            title="Concurrent Variant",
            price=Decimal("30.00"),
            currency="USD",
        )
        session.add(variant)
        await session.commit()
        variant_id = variant.id

    key = f"pg-checkout-{uuid.uuid4().hex}"

    async def checkout_once() -> uuid.UUID:
        async with postgres_session_factory() as session:
            order, _ = await CheckoutService().checkout(
                session=session,
                tenant_id=tenant_id,
                user_id=user_id,
                variant_id=variant_id,
                quantity=1,
                recipient="@pg-user",
                execute_sync=False,
                enqueue_durable=True,
                idempotency_key=key,
            )
            return order.id

    order_ids = await _bounded_gather(checkout_once(), checkout_once())
    assert order_ids[0] == order_ids[1]

    async with postgres_session_factory() as session:
        wallet = await session.get(Wallet, wallet_id)
        assert wallet is not None
        assert wallet.balance == Decimal("70.00")
        orders = int(
            (
                await session.scalar(
                    select(func.count())
                    .select_from(Order)
                    .where(
                        Order.tenant_id == tenant_id,
                        Order.user_id == user_id,
                        Order.checkout_idempotency_key == key,
                    )
                )
            )
            or 0
        )
        debits = int(
            (
                await session.scalar(
                    select(func.count())
                    .select_from(LedgerTransaction)
                    .where(
                        LedgerTransaction.wallet_id == wallet_id,
                        LedgerTransaction.transaction_type == TransactionType.DEBIT,
                        LedgerTransaction.reference_type == "ORDER_CHECKOUT",
                    )
                )
            )
            or 0
        )
        jobs = int(
            (
                await session.scalar(
                    select(func.count())
                    .select_from(FulfillmentJobRecord)
                    .where(FulfillmentJobRecord.order_id == order_ids[0])
                )
            )
            or 0
        )
        assert orders == 1
        assert debits == 1
        assert jobs == 1


async def test_duplicate_payment_settlement_credits_once_under_concurrency(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id, _, wallet_id = await _tenant_user_wallet(
        postgres_session_factory,
        balance=Decimal("0.00"),
    )
    payment_intent_id = uuid.uuid4()
    ready = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()

    async def settle(index: int) -> uuid.UUID:
        async with postgres_session_factory() as session:
            wallet = await session.get(Wallet, wallet_id)
            assert wallet is not None
            ready[index].set()
            await release.wait()
            transaction = await LedgerService.settle_payment(
                session,
                wallet,
                Decimal("25.00"),
                payment_intent_id,
            )
            await session.commit()
            return transaction.id

    tasks = [asyncio.create_task(settle(0)), asyncio.create_task(settle(1))]
    await _bounded_gather(*(event.wait() for event in ready))
    release.set()
    transaction_ids = await _bounded_gather(*tasks)
    assert transaction_ids[0] == transaction_ids[1]

    async with postgres_session_factory() as session:
        wallet = await session.get(Wallet, wallet_id)
        assert wallet is not None
        assert wallet.balance == Decimal("25.00")
        settlements = int(
            (
                await session.scalar(
                    select(func.count())
                    .select_from(LedgerTransaction)
                    .where(
                        LedgerTransaction.tenant_id == tenant_id,
                        LedgerTransaction.wallet_id == wallet_id,
                        LedgerTransaction.transaction_type == TransactionType.CREDIT,
                        LedgerTransaction.reference_type == CANONICAL_SETTLEMENT_TYPE,
                        LedgerTransaction.reference_id == str(payment_intent_id),
                    )
                )
            )
            or 0
        )
        assert settlements == 1


async def test_duplicate_refund_credits_once_under_concurrency(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _tenant_id, _, wallet_id = await _tenant_user_wallet(
        postgres_session_factory,
        balance=Decimal("0.00"),
    )
    reference_id = f"refund-{uuid.uuid4().hex}"
    ready = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()

    async def refund(index: int) -> uuid.UUID:
        async with postgres_session_factory() as session:
            wallet = await session.get(Wallet, wallet_id)
            assert wallet is not None
            ready[index].set()
            await release.wait()
            transaction = await LedgerService.refund(
                session,
                wallet,
                Decimal("40.00"),
                reference_id=reference_id,
                reference_type=CANONICAL_REFUND_TYPE,
            )
            await session.commit()
            return transaction.id

    tasks = [asyncio.create_task(refund(0)), asyncio.create_task(refund(1))]
    await _bounded_gather(*(event.wait() for event in ready))
    release.set()
    transaction_ids = await _bounded_gather(*tasks)
    assert transaction_ids[0] == transaction_ids[1]

    async with postgres_session_factory() as session:
        wallet = await session.get(Wallet, wallet_id)
        assert wallet is not None
        assert wallet.balance == Decimal("40.00")
        refund_count = int(
            (
                await session.scalar(
                    select(func.count())
                    .select_from(LedgerTransaction)
                    .where(
                        LedgerTransaction.wallet_id == wallet_id,
                        LedgerTransaction.transaction_type == TransactionType.REFUND,
                        LedgerTransaction.reference_type == CANONICAL_REFUND_TYPE,
                        LedgerTransaction.reference_id == reference_id,
                    )
                )
            )
            or 0
        )
        assert refund_count == 1


async def test_durable_fulfillment_job_has_single_claim_winner(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id, user_id, _ = await _tenant_user_wallet(
        postgres_session_factory,
        balance=Decimal("0.00"),
    )
    async with postgres_session_factory() as session:
        order = Order(
            tenant_id=tenant_id,
            user_id=user_id,
            order_number=f"PG-{uuid.uuid4().hex[:12]}",
            status=OrderStatus.PAID,
            total_amount=Decimal("10.00"),
            currency="USD",
        )
        session.add(order)
        await session.flush()
        job = FulfillmentJobRecord(
            tenant_id=tenant_id,
            order_id=order.id,
            recipient="@pg-user",
            attempt_number=1,
            status=FulfillmentJobStatus.QUEUED,
            payload={"source": "pg-concurrency-test"},
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    async def claim() -> bool:
        async with postgres_session_factory() as session:
            return await FulfillmentWorker.claim_job(session, job_id)

    results = await _bounded_gather(claim(), claim())
    assert sorted(results) == [False, True]

    async with postgres_session_factory() as session:
        job = await session.get(FulfillmentJobRecord, job_id)
        assert job is not None
        assert job.status == FulfillmentJobStatus.RUNNING
        assert job.locked_at is not None


async def test_concurrent_wallet_creation_converges_to_one_row(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with postgres_session_factory() as session:
        tenant = Tenant(name="Wallet Race", slug=f"wallet-race-{uuid.uuid4().hex[:8]}")
        user = User(username=f"wallet_race_{uuid.uuid4().hex[:8]}")
        session.add_all([tenant, user])
        await session.commit()
        tenant_id = tenant.id
        user_id = user.id

    async def create_wallet() -> uuid.UUID:
        async with postgres_session_factory() as session:
            wallet = await LedgerService.get_or_create_wallet(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                currency="USD",
            )
            await session.commit()
            return wallet.id

    wallet_ids = await _bounded_gather(create_wallet(), create_wallet())
    assert wallet_ids[0] == wallet_ids[1]

    async with postgres_session_factory() as session:
        count = int(
            (
                await session.scalar(
                    select(func.count())
                    .select_from(Wallet)
                    .where(
                        Wallet.tenant_id == tenant_id,
                        Wallet.user_id == user_id,
                        Wallet.currency == "USD",
                    )
                )
            )
            or 0
        )
        assert count == 1


async def test_concurrent_owner_deactivation_preserves_last_active_owner(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with postgres_session_factory() as session:
        tenant = Tenant(name="Owner Race", slug=f"owner-race-{uuid.uuid4().hex[:8]}")
        owner_a = User(username=f"owner_a_{uuid.uuid4().hex[:8]}")
        owner_b = User(username=f"owner_b_{uuid.uuid4().hex[:8]}")
        session.add_all([tenant, owner_a, owner_b])
        await session.flush()
        membership_a = Membership(
            tenant_id=tenant.id,
            user_id=owner_a.id,
            role=Role.OWNER,
            permissions=[],
            is_active=True,
        )
        membership_b = Membership(
            tenant_id=tenant.id,
            user_id=owner_b.id,
            role=Role.OWNER,
            permissions=[],
            is_active=True,
        )
        session.add_all([membership_a, membership_b])
        await session.commit()
        tenant_id = tenant.id
        membership_ids = [membership_a.id, membership_b.id]
        owner_ids = [owner_a.id, owner_b.id]

    async def deactivate(index: int) -> int:
        principal = AuthenticatedPrincipal(
            user_id=owner_ids[index],
            tenant_id=tenant_id,
            source=AuthSource.TEST,
            roles=frozenset({Role.OWNER}),
        )
        async with postgres_session_factory() as session:
            try:
                await update_member(
                    membership_id=membership_ids[index],
                    req=MemberUpdateRequest(is_active=False),
                    principal=principal,
                    session=session,
                )
                await session.commit()
                return 200
            except HTTPException as exc:
                await session.rollback()
                return exc.status_code

    outcomes = await _bounded_gather(deactivate(0), deactivate(1))
    assert sorted(outcomes) == [200, 409]

    async with postgres_session_factory() as session:
        active_owners = int(
            (
                await session.scalar(
                    select(func.count())
                    .select_from(Membership)
                    .where(
                        Membership.tenant_id == tenant_id,
                        Membership.role == Role.OWNER,
                        Membership.is_active.is_(True),
                    )
                )
            )
            or 0
        )
        assert active_owners == 1


async def test_durable_bot_provisioning_job_has_single_claim_winner(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with postgres_session_factory() as session:
        tenant = Tenant(name="Bot Claim", slug=f"bot-claim-{uuid.uuid4().hex[:8]}")
        user = User(username=f"bot_claim_{uuid.uuid4().hex[:8]}")
        session.add_all([tenant, user])
        await session.flush()
        config = {"locale": "en"}
        job = BotProvisioningJob(
            tenant_id=tenant.id,
            requested_by_user_id=user.id,
            idempotency_key=f"pg-bot-{uuid.uuid4().hex}",
            request_fingerprint=provisioning_fingerprint(
                token_secret_ref="PG_BOT_TOKEN_REF",
                expected_username="pg_bot",
                requested_display_name=None,
                desired_enabled=True,
                desired_config=config,
            ),
            token_secret_ref="PG_BOT_TOKEN_REF",
            expected_username="pg_bot",
            desired_enabled=True,
            desired_config=config,
            status=BotProvisioningStatus.PENDING,
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    service = BotProvisioningService(session_factory=postgres_session_factory)

    async def claim():
        async with postgres_session_factory() as session:
            claimed = await service.claim_next_job(session)
            return claimed.id if claimed is not None else None

    winners = await _bounded_gather(claim(), claim())
    assert winners.count(job_id) == 1
    assert winners.count(None) == 1

    async with postgres_session_factory() as session:
        stored = await session.get(BotProvisioningJob, job_id)
        assert stored is not None
        assert stored.status == BotProvisioningStatus.RUNNING
        assert stored.attempt_count == 1
        assert stored.lease_expires_at is not None
