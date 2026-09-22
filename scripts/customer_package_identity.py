"""Refuse a package/image schema mismatch before any customer state import."""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from packages.marketplace.tenant_bundle import schema_fingerprint


def main():
    path = Path(sys.argv[1])
    manifest = json.loads(path.read_text())
    image = os.environ.get('GHBF_IMAGE', '')
    if image != manifest.get('image') or not re.fullmatch(r'[a-z0-9][a-z0-9./:_-]{0,175}@sha256:[a-f0-9]{64}', image):
        raise SystemExit('Destination image must match the immutable package image.')
    for name, digest in manifest.get('files', {}).items():
        target = path.parent / name
        if Path(name).name != name or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise SystemExit('Package file integrity check failed.')
    revision = ScriptDirectory.from_config(Config('alembic.ini')).get_current_head()
    if manifest.get('schema_revision') != revision or manifest.get('schema_fingerprint') != schema_fingerprint():
        raise SystemExit('Package and release image schemas differ. Request a matching package and image.')
    print('Package and image schema identities match. This is not installation acceptance.')


if __name__ == '__main__':
    main()
