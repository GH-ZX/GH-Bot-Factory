import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest


def archive(path, files):
    with tarfile.open(path, "w:gz") as output:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            output.addfile(member, io.BytesIO(data))


@pytest.mark.parametrize("failure", ["database", "vault", "migration", "unsafe_archive", "none"])
def test_portable_import_never_restarts_after_failure(tmp_path, failure):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "import_portable_state.sh"
    shutil.copyfile(Path(__file__).parents[1] / "scripts/import_portable_state.sh", script)
    (tmp_path / ".env").write_text("POSTGRES_DB=test_import\n")
    inner = tmp_path / "vault.tar.gz"
    archive(inner, {"secret-store/../escape" if failure == "unsafe_archive" else "secret-store/master.key": b"dummy-test-key"})
    files = {"manifest.json": json.dumps({"format": "ghbf-portable-state-v1"}).encode(),
             "postgres.dump": b"mock-dump", "secret-store.tar.gz": inner.read_bytes()}
    files["SHA256SUMS"] = "".join(f"{hashlib.sha256(value).hexdigest()}  {name}\n" for name, value in files.items()).encode()
    bundle = tmp_path / "bundle.tar.gz"
    archive(bundle, files)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    docker = bindir / "docker"
    docker.write_text('''#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
with open(os.environ["MOCK_DOCKER_LOG"], "a") as log:
    log.write(repr(args[:8]) + "\\n")
if args[:3] == ["compose", "ps", "--services"]:
    print("api\\nworker\\nbot-runtime")
fail = os.environ["MOCK_FAIL"]
if (fail == "database" and "exec" in args or
    fail == "vault" and "--no-deps" in args or
    fail == "migration" and args[-1] == "migrate"):
    sys.stdin.buffer.read()
    sys.exit(17)
if "exec" in args or "--no-deps" in args:
    sys.stdin.buffer.read()
''')
    docker.chmod(0o755)
    log = tmp_path / "docker.log"
    result = subprocess.run(["bash", str(script), "--confirm", str(bundle)], cwd=tmp_path,
                            env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
                                 "MOCK_FAIL": failure, "MOCK_DOCKER_LOG": str(log)},
                            text=True, capture_output=True, timeout=15, check=False)
    commands = log.read_text()
    if failure == "none":
        assert result.returncode == 0, result.stderr
        assert "'up', '-d', 'api', 'worker', 'bot-runtime'" in commands
    else:
        assert result.returncode != 0
        assert "'up', '-d', 'api'" not in commands
        if failure == "unsafe_archive":
            assert "'stop'" not in commands
        else:
            assert "remain stopped" in result.stderr
