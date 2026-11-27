"""Run a checksum-pinned Gitleaks release without vendoring its binary."""

import hashlib
import io
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile


VERSION = '8.30.1'
ASSETS = {
    'Windows': ('windows_x64.zip', 'd29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e'),
    'Linux': ('linux_x64.tar.gz', '551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb'),
}


def main():
    if platform.machine().lower() not in {'amd64', 'x86_64'} or platform.system() not in ASSETS:
        raise SystemExit('This helper supports Linux and Windows x64; install Gitleaks separately on other platforms.')
    suffix, checksum = ASSETS[platform.system()]
    url = f'https://github.com/gitleaks/gitleaks/releases/download/v{VERSION}/gitleaks_{VERSION}_{suffix}'
    with urllib.request.urlopen(url, timeout=60) as response:
        archive = response.read()
    if hashlib.sha256(archive).hexdigest() != checksum:
        raise SystemExit('Gitleaks archive checksum mismatch')
    with tempfile.TemporaryDirectory(prefix='atria-secret-check-') as directory:
        binary = Path(directory) / ('gitleaks.exe' if suffix.endswith('.zip') else 'gitleaks')
        if suffix.endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(archive)) as package:
                binary.write_bytes(package.read('gitleaks.exe'))
        else:
            with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as package:
                member = package.extractfile('gitleaks')
                if member is None:
                    raise SystemExit('Gitleaks binary missing from release archive')
                binary.write_bytes(member.read())
            binary.chmod(0o700)
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([str(binary), 'dir', str(root), '--redact=100', '--no-banner'], check=False)
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
