"""Build explicit source bundles; never include virtualenv, cookies or user data."""
from pathlib import Path
import hashlib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
COMMON = ['downloader.py', 'desktop_support.py', 'setup.py', 'doctor.py', 'requirements.txt',
          'README.md', 'LICENSE', 'VERSION', 'CHANGELOG.md', 'docs/SETUP-HINDI.md',
          'docs/TROUBLESHOOTING.md', 'docs/terminal-preview.svg']


def build():
    version = (ROOT/'VERSION').read_text().strip()
    out = ROOT/'dist'
    out.mkdir(exist_ok=True)
    sums = []
    for platform, extras in [('macOS', ['Setup.command','Start.command','yt','dd']),
                             ('Windows', ['Setup.cmd','Start.cmd','yt.cmd','dd.cmd'])]:
        path = out/f'Downloader-{version}-{platform}.zip'
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as bundle:
            for name in COMMON+extras:
                bundle.write(ROOT/name, 'Downloader/'+name)
        sums.append(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name)
    (out/'SHA256SUMS.txt').write_text('\n'.join(sums)+'\n')


if __name__ == '__main__':
    build()
