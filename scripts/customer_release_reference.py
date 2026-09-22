"""Require an immutable image reference before a customer-managed migration."""
import os
import re


def main():
    image = os.environ.get('GHBF_IMAGE', '')
    if not re.fullmatch(r'[a-z0-9][a-z0-9./:_-]{0,175}@sha256:[a-f0-9]{64}', image):
        raise SystemExit('GHBF_IMAGE must be an approved registry/repository@sha256 digest.')
    print('Immutable image reference configured. Approval and backup evidence remain owner responsibilities.')


if __name__ == '__main__':
    main()
