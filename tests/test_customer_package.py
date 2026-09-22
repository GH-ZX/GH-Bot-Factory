import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile

import pytest

from packages.delivery.package import build_package

IMAGE = 'example.invalid/store@sha256:' + 'a' * 64
NEW_IMAGE = 'example.invalid/store@sha256:' + 'b' * 64


@pytest.fixture
def package(tmp_path):
    contents = build_package(ciphertext=b'encrypted-test-fixture', tenant_name='Example <store>',
                             tenant_id='00000000-0000-0000-0000-000000000001', image=IMAGE, language='en')
    with zipfile.ZipFile(io.BytesIO(contents)) as archive:
        archive.extractall(tmp_path)
    values = dict(line.split('=', 1) for line in (tmp_path/'.env.example').read_text().splitlines() if '=' in line)
    values.update(COMPOSE_PROJECT_NAME='ghbf-test-package', DATABASE_URL='postgresql+asyncpg://test:test@localhost/isolated_test',
                  ADMIN_PUBLIC_URL='https://example.invalid/admin/', MINIAPP_PUBLIC_URL='https://example.invalid/miniapp/')
    for key in ('JWT_SECRET_KEY', 'SECRET_KEY', 'SETUP_CODE', 'PLATFORM_ADMIN_TOKEN'):
        values[key] = 'isolated-test-credential-' + 'x'*32
    (tmp_path/'.env').write_text('\n'.join(f'{key}={value}' for key, value in values.items())+'\n')
    (tmp_path/'.env').chmod(0o600)
    return tmp_path, values


def run(package, *args, overrides=None):
    root, values = package
    env = {key: value for key, value in os.environ.items() if key not in values and not key.startswith('COMPOSE_')}
    env.update(overrides or {})
    return subprocess.run([sys.executable, *args], cwd=root, env=env, capture_output=True, text=True, check=False)


def test_package_manifest_and_fail_closed_configuration(package):
    root, _ = package
    manifest = json.loads((root/'manifest.json').read_text())
    for name, digest in manifest['files'].items():
        assert hashlib.sha256((root/name).read_bytes()).hexdigest() == digest
    assert '&lt;store&gt;' in (root/'REPORT.html').read_text()
    assert run(package, 'preflight.py').returncode == 0
    assert run(package, 'preflight.py', overrides={'GHBF_IMAGE': NEW_IMAGE}).returncode != 0
    (root/'.env').chmod(0o644)
    assert run(package, 'preflight.py').returncode != 0
    (root/'.env').chmod(0o600)
    (root/'install.sh').write_text('tampered script')
    assert run(package, 'preflight.py').returncode != 0


def test_lifecycle_blocks_repeat_and_unapproved_updates(package):
    root, _ = package
    assert run(package, 'lifecycle.py', 'begin', 'install').returncode == 0
    assert run(package, 'lifecycle.py', 'begin', 'install').returncode != 0
    assert run(package, 'lifecycle.py', 'finish').returncode == 0
    assert run(package, 'lifecycle.py', 'begin', 'install').returncode != 0
    (root/'.env').write_text((root/'.env').read_text().replace(IMAGE, NEW_IMAGE))
    assert run(package, 'lifecycle.py', 'begin', 'update').returncode != 0
    approval = {'owner_approved': True, 'writes_stopped': True, 'approved_image': NEW_IMAGE,
                'previous_image': IMAGE, 'database_backup_sha256': 'c'*64, 'vault_backup_sha256': 'd'*64,
                'backup_reference': 'isolated-backup', 'rollback_notes': 'Restore matched database and vault'}
    (root/'update-approval.json').write_text(json.dumps(approval))
    assert run(package, 'lifecycle.py', 'begin', 'update').returncode == 0
    assert (root/'.operation-lock').is_dir()
    assert run(package, 'lifecycle.py', 'begin', 'update').returncode != 0
    assert run(package, 'lifecycle.py', 'finish').returncode == 0
    state = json.loads((root/'.installation.json').read_text())
    assert state['image'] == NEW_IMAGE and state['previous_image'] == IMAGE
    assert state['acceptance'] == 'not_performed'
