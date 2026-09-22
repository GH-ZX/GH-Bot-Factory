"""Audit installed third-party versions; first-party source is covered by repository gates."""
import re
import subprocess
import sys
from importlib.metadata import distributions
from pathlib import Path
from tempfile import TemporaryDirectory


def main():
    requirements = set()
    for distribution in distributions():
        name = distribution.metadata['Name']
        if name.lower().replace('_', '-') == 'gh-bot-factory':
            continue
        version = distribution.version
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or not re.fullmatch(r'[A-Za-z0-9_.+!-]+', version):
            raise SystemExit('Cannot represent an installed distribution as an exact audit requirement.')
        requirements.add(f'{name}=={version}')
    if not requirements:
        raise SystemExit('No third-party dependencies found to audit.')
    with TemporaryDirectory(prefix='ghbf-dependency-audit-') as temporary:
        path = Path(temporary) / 'requirements.txt'
        path.write_text('\n'.join(sorted(requirements))+'\n')
        result = subprocess.run([sys.executable, '-m', 'pip_audit', '--strict', '--no-deps',
                                 '--disable-pip', '-r', str(path)], check=False)
        return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
