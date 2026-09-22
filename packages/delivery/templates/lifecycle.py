#!/usr/bin/env python3
"""Private local operation receipts; never claim runtime or customer acceptance."""
import json
import os
import sys
from datetime import UTC, datetime

from preflight import ROOT, configuration, package

os.umask(0o077)
STATE = ROOT / '.installation.json'
LOCK = ROOT / '.operation-lock'


def write(value):
    tmp = ROOT / '.installation.json.tmp'
    with tmp.open('x') as stream:
        json.dump(value, stream, indent=2)
    tmp.replace(STATE)


def main():
    action = sys.argv[1]
    if action == 'begin':
        mode = sys.argv[2]
        if mode not in {'install', 'update'}:
            raise SystemExit('Unknown lifecycle operation.')
        manifest, env = package(), configuration()
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
        if mode == 'install' and state:
            raise SystemExit('Installation already attempted. Follow recovery instructions; do not re-import.')
        if mode == 'update' and state.get('phase') not in {'installed', 'migrated'}:
            raise SystemExit('A completed local installation receipt is required before updating.')
        if mode == 'install' and env['GHBF_IMAGE'] != manifest['image']:
            raise SystemExit('Initial installation requires the original package image.')
        if mode == 'update':
            evidence_path = ROOT / 'update-approval.json'
            evidence = json.loads(evidence_path.read_text()) if evidence_path.exists() else {}
            if (evidence.get('approved_image') != env['GHBF_IMAGE']
                    or evidence.get('previous_image') != state.get('image')
                    or not evidence.get('owner_approved') is True
                    or not evidence.get('writes_stopped') is True):
                raise SystemExit('Record matching customer approval, previous image and stopped writes in update-approval.json.')
            for key in ('database_backup_sha256', 'vault_backup_sha256'):
                value = evidence.get(key, '')
                if len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                    raise SystemExit('Record database AND vault backup SHA-256 checksums before updating.')
            if not evidence.get('backup_reference') or not evidence.get('rollback_notes'):
                raise SystemExit('Record backup location and rollback instructions before updating.')
        try:
            LOCK.mkdir(mode=0o700)
        except FileExistsError:
            raise SystemExit('An operation is active or failed. Follow UPDATE-RESTORE.md before recovery.')
        locked_state = json.loads(STATE.read_text()) if STATE.exists() else {}
        if locked_state != state:
            raise SystemExit('Local installation changed while acquiring the operation lock. Review the operation before recovery.')
        state = {'phase': 'installing' if mode == 'install' else 'updating',
                 'image': env['GHBF_IMAGE'], 'previous_image': state.get('image'),
                 'started_at': datetime.now(UTC).isoformat(),
                 'acceptance': 'not_performed', 'tenant_id': manifest['tenant_id']}
        write(state)
    elif action == 'finish':
        state = json.loads(STATE.read_text())
        phase = {'installing': 'installed', 'updating': 'migrated'}.get(state.get('phase'))
        if not phase or not LOCK.is_dir():
            raise SystemExit('No active lifecycle operation.')
        state.update(phase=phase, completed_at=datetime.now(UTC).isoformat())
        write(state)
        LOCK.rmdir()
    else:
        raise SystemExit('Use begin install, begin update, or finish.')


if __name__ == '__main__':
    main()
