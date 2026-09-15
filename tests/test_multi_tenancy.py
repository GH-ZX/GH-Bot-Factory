import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.commerce.models import Category, Product
from packages.tenants.models import Membership, Role, Tenant, User


@pytest.mark.asyncio
async def test_tenant_creation_and_membership(db_session: AsyncSession):
    # 1. Create Tenant
    tenant = Tenant(name="Acme Store", slug="acme-store", settings={"currency": "USD"})
    db_session.add(tenant)
    await db_session.flush()

    # 2. Create User
    user = User(
        username="owner_acme",
        telegram_id=12345678,
        email="owner@acme.com",
    )
    db_session.add(user)
    await db_session.flush()

    # 3. Create Membership with Role OWNER
    membership = Membership(
        tenant_id=tenant.id,
        user_id=user.id,
        role=Role.OWNER,
        permissions=["*"],
    )
    db_session.add(membership)
    await db_session.flush()

    # 4. Verify associations
    stmt = select(Membership).where(Membership.tenant_id == tenant.id)
    res = await db_session.execute(stmt)
    memberships = res.scalars().all()
    assert len(memberships) == 1
    assert memberships[0].role == Role.OWNER
    assert memberships[0].user_id == user.id


@pytest.mark.asyncio
async def test_tenant_data_scoping_isolation(db_session: AsyncSession):
    # Tenant A
    tenant_a = Tenant(name="Tenant Alpha", slug="tenant-alpha")
    # Tenant B
    tenant_b = Tenant(name="Tenant Beta", slug="tenant-beta")
    db_session.add_all([tenant_a, tenant_b])
    await db_session.flush()

    # Category for Tenant A
    cat_a = Category(tenant_id=tenant_a.id, name="Electronics", slug="electronics")
    # Category for Tenant B with identical slug (should succeed due to composite uniqueness)
    cat_b = Category(tenant_id=tenant_b.id, name="Electronics", slug="electronics")
    db_session.add_all([cat_a, cat_b])
    await db_session.flush()

    # Products
    prod_a = Product(tenant_id=tenant_a.id, category_id=cat_a.id, title="Alpha Phone")
    prod_b = Product(tenant_id=tenant_b.id, category_id=cat_b.id, title="Beta Laptop")
    db_session.add_all([prod_a, prod_b])
    await db_session.flush()

    # Querying tenant A products should strictly never return tenant B items
    stmt_a = select(Product).where(Product.tenant_id == tenant_a.id)
    res_a = await db_session.execute(stmt_a)
    products_a = res_a.scalars().all()

    assert len(products_a) == 1
    assert products_a[0].title == "Alpha Phone"
    assert products_a[0].tenant_id == tenant_a.id
