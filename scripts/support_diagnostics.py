#!/usr/bin/env python3
"""Read only the customer-authorized diagnostic summary. Never persists the access code."""
import getpass
import http.client
import json
import ssl
from urllib.parse import urlsplit


def main():
    origin = urlsplit(input('Customer HTTPS origin: ').strip())
    if origin.scheme != 'https' or not origin.hostname or origin.username or origin.password or origin.path not in {'', '/'} or origin.query or origin.fragment:
        raise SystemExit('Enter an HTTPS origin without credentials, path or query.')
    token = getpass.getpass('Temporary support code: ')
    if len(token) > 128 or len(token) < 40 or any(c in token for c in '\r\n'):
        raise SystemExit('Invalid support code.')
    connection = http.client.HTTPSConnection(origin.hostname, origin.port or 443,
                                             timeout=15, context=ssl.create_default_context())
    try:
        connection.request('GET', '/api/v1/maintenance-access/diagnostics',
                           headers={'X-Support-Token': token, 'Accept': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            raise SystemExit(f'Diagnostic request refused ({response.status}). Redirects are not followed.')
        raw = response.read(65537)
        if len(raw) > 65536:
            raise SystemExit('Diagnostic response too large.')
        data = json.loads(raw)
        allowed = {'scope', 'issue_id', 'issue_status', 'schema_revisions', 'bots', 'collected_at'}
        if not isinstance(data, dict) or set(data) != allowed or data.get('scope') != 'read_only_diagnostics':
            raise SystemExit('Unexpected diagnostic response; nothing was displayed.')
        print(json.dumps(data, indent=2))
    finally:
        connection.close()


if __name__ == '__main__':
    main()
