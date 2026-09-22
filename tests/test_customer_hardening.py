from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text

from apps.api.deps import get_current_principal
from apps.api.main import app
from apps.api.platform_deps import PlatformOperator, require_platform_operator
from packages.commerce.checkout import CheckoutService
from packages.commerce.models import Product, ProductVariant
from packages.core.auth import AuthenticatedPrincipal, AuthSource
from packages.core.database import get_db_session
from packages.delivery.models import DiagnosticGrant, MaintenanceIssue
from packages.marketplace.models import DeploymentHandoff
from packages.payments.models import Wallet
from packages.payments.service import LedgerService
from packages.telegram.fleet_state import BotFleetStateStore
from packages.tenants.models import Membership, Role, Tenant, User

pytestmark = pytest.mark.asyncio
IMAGE = 'example.invalid/store@sha256:' + 'a' * 64
OLD_IMAGE = 'example.invalid/store@sha256:' + 'b' * 64


@pytest_asyncio.fixture
async def care(db_session, monkeypatch):
    tenant, other, user = Tenant(name='Store', slug='hardening'), Tenant(name='Other', slug='other'), User(username='owner')
    db_session.add_all([tenant, other, user])
    await db_session.flush()
    db_session.add(Membership(tenant_id=tenant.id, user_id=user.id, role=Role.OWNER))
    await db_session.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32))'))
    await db_session.execute(text("INSERT INTO alembic_version VALUES ('c93eb541da62')"))
    await db_session.commit()
    principal = AuthenticatedPrincipal(user.id, tenant.id, AuthSource.SESSION, frozenset({Role.OWNER}))
    app.dependency_overrides[get_db_session] = lambda: db_session
    app.dependency_overrides[get_current_principal] = lambda: principal
    async def observed(*args):
        return {}
    monkeypatch.setattr(BotFleetStateStore, 'read_many', observed)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            yield client, tenant, other, user
    finally:
        app.dependency_overrides.clear()


async def test_settings_versions_scope_and_public_allowlist(care, db_session):
    client, tenant, other, _ = care
    tenant.settings = {'private_setting': 'must-stay-private'}
    await db_session.commit()
    body = {'name': 'Updated', 'expected_version': 0, 'sales_paused': True}
    response = await client.put('/api/v1/admin/store-settings', json=body)
    assert response.status_code == 200, response.text
    assert response.json()['sales_paused'] is True
    assert 'must-stay-private' not in response.text
    assert (await client.put('/api/v1/admin/store-settings', json=body)).status_code == 409
    assert (await db_session.get(Tenant, other.id)).name == 'Other'
    response = await client.put('/api/v1/admin/store-settings', json={**body, 'expected_version': 1, 'tenant_id': str(other.id)})
    assert response.status_code == 422
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('role', [Role.CUSTOMER, Role.STAFF, Role.ADMIN])
async def test_only_owner_can_record_installation(care, role):
    client, tenant, _, user = care
    app.dependency_overrides[get_current_principal] = lambda: AuthenticatedPrincipal(user.id, tenant.id, AuthSource.SESSION, frozenset({role}))
    response = await client.put('/api/v1/admin/installation', json={
        'expected_version': 0, 'image': IMAGE, 'release_version': 'v1', 'backup_status': 'NOT_RECORDED'})
    assert response.status_code == 403


async def test_installation_evidence_requires_both_backups_and_safe_report(care):
    client, *_ = care
    body = {'expected_version': 0, 'image': IMAGE, 'release_version': 'v1', 'backup_status': 'COMPLETED'}
    assert (await client.put('/api/v1/admin/installation', json=body)).status_code == 400
    body.update(backup_reference='private-backup-location', database_backup_sha256='c'*64,
                vault_backup_sha256='d'*64, backup_completed_at=datetime.now(UTC).isoformat())
    assert (await client.put('/api/v1/admin/installation', json=body)).status_code == 200
    assert (await client.put('/api/v1/admin/installation', json=body)).status_code == 409
    report = await client.get('/api/v1/admin/installation/support-report')
    assert report.status_code == 200
    assert 'private-backup-location' not in report.text
    assert report.json()['observed_schema_revisions'] == ['c93eb541da62']
    assert report.json()['acceptance'] == 'not_assessed'


async def test_attention_acknowledgement_is_not_resolution(care):
    client, *_ = care
    response = await client.get('/api/v1/admin/attention')
    assert response.status_code == 200, response.text
    alert = next(row for row in response.json()['alerts'] if row['key'] == 'backup-evidence')
    body = {key: alert[key] for key in ('key', 'fingerprint')}
    ack = await client.post('/api/v1/admin/attention/acknowledge', json=body)
    assert ack.json() == {'acknowledged': True, 'resolved': False}
    refreshed = await client.get('/api/v1/admin/attention')
    assert next(row for row in refreshed.json()['alerts'] if row['key'] == alert['key'])['acknowledged']
    assert (await client.post('/api/v1/admin/attention/acknowledge', json={**body, 'fingerprint': 'f'*64})).status_code == 409


async def test_grants_are_issue_bound_capped_revocable_and_redacted(care, db_session):
    client, tenant, other, _ = care
    private = MaintenanceIssue(tenant_id=other.id, title='Private', description='Other tenant details')
    db_session.add(private)
    await db_session.commit()
    assert (await client.post('/api/v1/admin/maintenance/grants', json={'issue_id': str(private.id)})).status_code == 404
    issue = (await client.post('/api/v1/admin/maintenance/issues', json={'title': 'Problem', 'description': 'Reproducible sample issue'})).json()
    grants = []
    for _ in range(3):
        response = await client.post('/api/v1/admin/maintenance/grants', json={'issue_id': issue['id']})
        assert response.status_code == 201, response.text
        grants.append(response.json())
    assert (await client.post('/api/v1/admin/maintenance/grants', json={'issue_id': issue['id']})).status_code == 409
    grant = grants[0]
    stored = await db_session.scalar(select(DiagnosticGrant).where(DiagnosticGrant.tenant_id == tenant.id))
    assert stored.token_digest != grant['access_token']
    headers = {'X-Support-Token': grant['access_token']}
    report = await client.get('/api/v1/maintenance-access/diagnostics', headers=headers)
    assert report.status_code == 200, report.text
    assert 'Other tenant details' not in report.text and 'access_token' not in report.text
    await client.post(f"/api/v1/admin/maintenance/grants/{grant['id']}/revoke")
    assert (await client.get('/api/v1/maintenance-access/diagnostics', headers=headers)).status_code == 401


async def test_release_update_requires_approval_backup_and_matching_installation(care):
    client, *_ = care
    root = '/api/v1/admin/maintenance'
    issue = (await client.post(root+'/issues', json={'title': 'Problem', 'description': 'Reproducible sample issue'})).json()
    await client.patch(root+'/issues/'+issue['id'], json={'expected_version': 1, 'body': 'Shared implementation defect', 'status': 'DIAGNOSING', 'scope': 'SHARED_CORE'})
    release_body = {'version_label': 'v2', 'image': IMAGE, 'release_notes': 'Fix documented problem', 'migration_notes': 'No schema change required', 'rollback_notes': 'Restore both vault and database', 'issue_ids': [issue['id']]}
    release = await client.post(root+'/releases', json=release_body)
    assert release.status_code == 201, release.text
    assert (await client.post(root+'/releases', json=release_body)).status_code == 409
    proposal = {key: release_body[key] for key in ('version_label', 'image', 'release_notes', 'migration_notes')}
    proposal.update(release_id=release.json()['id'], issue_id=issue['id'], previous_image=OLD_IMAGE)
    update = await client.post(root+'/updates', json=proposal)
    assert update.status_code == 201, update.text
    url = root+'/updates/'+update.json()['id']
    evidence = 'Owner evidence recorded in isolated test'
    assert (await client.patch(url, json={'expected_version': 1, 'status': 'INSTALLED', 'evidence': evidence})).status_code == 409
    assert (await client.patch(url, json={'expected_version': 1, 'status': 'APPROVED', 'evidence': evidence})).status_code == 200
    assert (await client.patch(url, json={'expected_version': 2, 'status': 'BACKED_UP', 'evidence': evidence})).status_code == 400
    assert (await client.patch(url, json={'expected_version': 2, 'status': 'BACKED_UP', 'backup_reference': 'db-and-vault-backup', 'evidence': evidence})).status_code == 200
    assert (await client.patch(url, json={'expected_version': 3, 'status': 'INSTALLED', 'evidence': evidence})).status_code == 400
    assert (await client.patch(url, json={'expected_version': 3, 'status': 'INSTALLED', 'evidence': evidence, 'installed_image': IMAGE, 'schema_revision': 'c93eb541da62'})).status_code == 200


async def test_pause_preserves_checkout_replay_without_extra_debit(care, db_session):
    _, tenant, _, user = care
    product = Product(tenant_id=tenant.id, title='Sample')
    db_session.add(product)
    await db_session.flush()
    variant = ProductVariant(product_id=product.id, sku='SAMPLE', title='Sample', price=Decimal(5), stock_quantity=10)
    db_session.add(variant)
    wallet = await LedgerService.get_or_create_wallet(db_session, tenant.id, user.id, 'USD')
    await LedgerService.credit(db_session, wallet, Decimal(20), description='Test funding')
    await db_session.commit()
    async def buy(key):
        return await CheckoutService().checkout(db_session, tenant.id, user.id, variant.id, 1, 'buyer', execute_sync=False, enqueue_durable=True, idempotency_key=key)
    order, _ = await buy('first-key')
    await db_session.commit()
    tenant.settings = {'sales_paused': True}
    await db_session.commit()
    assert (await buy('first-key'))[0].id == order.id
    with pytest.raises(ValueError, match='paused'):
        await buy('new-key')
    assert await db_session.scalar(select(Wallet.balance).where(Wallet.tenant_id == tenant.id)) == Decimal(15)


async def test_delivery_requires_platform_authority_and_records_pending_acceptance(care, db_session):
    client, tenant, *_ = care
    response = await client.get('/api/v1/platform/delivery/workspace/overview')
    assert response.status_code in {401, 503}
    app.dependency_overrides[require_platform_operator] = lambda: PlatformOperator(actor='TEST_OPERATOR')
    handoff = DeploymentHandoff(tenant_id=tenant.id, licensed_to='Example', license_key='isolated-handoff')
    db_session.add(handoff)
    await db_session.commit()
    response = await client.get('/api/v1/platform/delivery/workspace/overview')
    assert response.status_code == 200
    assert response.json()['projects'][0]['acceptance_recorded'] is False
    plan = {'expected_version': 0, 'image': IMAGE, 'release_version': 'v1'}
    url = '/api/v1/platform/delivery/'+str(handoff.id)
    assert (await client.put(url, json=plan)).status_code == 200
    assert (await client.put(url, json=plan)).status_code == 409
    assert (await client.post(url+'/complete', json={'expected_version': 1, 'confirm_customer_received': True})).status_code == 409


async def test_session_revocation_invalidates_old_principal(care, db_session):
    client, _, _, user = care
    assert (await client.post('/api/v1/auth/account/revoke-sessions')).status_code == 204
    await db_session.refresh(user)
    assert user.token_version == 2
    assert (await client.post('/api/v1/auth/account/revoke-sessions')).status_code == 401


async def test_validation_never_reflects_secret_inputs(care):
    client, *_ = care
    secret = 'private-value-never-reflect-in-response'
    response = await client.post('/api/v1/auth/login', json={'username': 'owner', 'password': secret*30})
    assert response.status_code == 422
    assert secret not in response.text and '"input"' not in response.text
    assert response.headers['cache-control'] == 'no-store'
