#!/usr/bin/env python3
"""Check the local image reference without executing or pulling the image."""
import re
from pathlib import Path

values = [line.split('=', 1)[1].strip().strip("'\"")
          for line in Path('.env').read_text().splitlines()
          if line.startswith('GHBF_IMAGE=')]
if len(values) != 1 or not re.fullmatch(r'[a-z0-9][a-z0-9./:_-]{0,175}@sha256:[a-f0-9]{64}', values[0]):
    raise SystemExit('Set exactly one digest-pinned GHBF_IMAGE in .env before continuing.')
print('Local release reference is digest-pinned. Follow the customer approval and backup workflow.')
