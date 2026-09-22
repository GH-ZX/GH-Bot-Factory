#!/usr/bin/env python3
"""Create destination-owned settings without displaying credentials."""
import getpass
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

os.umask(0o077)
if Path('.env').exists():
    raise SystemExit('.env exists. Edit it locally instead of overwriting destination secrets.')
url = getpass.getpass('Server-only PostgreSQL async URL (postgresql+asyncpg://…): ').strip()
if not url.startswith('postgresql+asyncpg://') or any(c in url for c in '\r\n'):
    raise SystemExit('Use a single-line postgresql+asyncpg database URL with URL-encoded credentials.')
public = input('Public HTTPS origin (example https://store.example.com): ').strip().rstrip('/')
parsed = urlsplit(public)
if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
    raise SystemExit('Enter an HTTPS origin without a path, credentials, query or fragment.')
values = {'COMPOSE_PROJECT_NAME': 'ghbf-' + secrets.token_hex(6), 'DATABASE_URL': url, 'MINIAPP_PUBLIC_URL': public+'/miniapp/', 'ADMIN_PUBLIC_URL': public+'/admin/'}
for key in ('JWT_SECRET_KEY', 'SECRET_KEY', 'SETUP_CODE', 'PLATFORM_ADMIN_TOKEN'):
    values[key] = secrets.token_urlsafe(48)
lines=[]
for line in Path('.env.example').read_text().splitlines():
    key=line.split('=',1)[0]
    value=values.get(key)
    # Compose single-quoted values prevent dollar interpolation in URL-encoded credentials.
    if value is not None:
        if "'" in value or '\n' in value or '\r' in value:
            raise SystemExit('URL-encode quotes and control characters in connection credentials.')
        line=key+"='"+value+"'"
    lines.append(line)
with Path('.env').open('x') as stream:
    stream.write('\n'.join(lines)+'\n')
print('Destination settings saved privately. Follow SETUP.md before starting the bot.')
