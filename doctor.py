"""Local dependency check. Does not read browser cookies or contact websites."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def check():
    result = {}
    result['Python'] = {'ok': sys.version_info >= (3, 10), 'detail': sys.version.split()[0]}
    for name, args in [('yt-dlp', ['--version']), ('ffmpeg', ['-version']),
                       ('ffprobe', ['-version']), ('deno', ['--version'])]:
        path = shutil.which(name)
        detail = 'Missing'
        ok = False
        if path:
            try:
                run = subprocess.run([path]+args, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10)
                ok = run.returncode == 0
                detail = (run.stdout or run.stderr).splitlines()[0] if run.stdout or run.stderr else 'No version output'
            except (OSError, subprocess.SubprocessError):
                detail = 'Could not start'
        result[name] = {'ok': ok, 'detail': detail}
    try:
        import psutil
        result['Pause/resume'] = {'ok': True, 'detail': 'psutil '+psutil.__version__}
    except ImportError:
        result['Pause/resume'] = {'ok': False, 'detail': 'Run setup again'}
    return result


def main():
    # Include the project's scripts even when invoked directly.
    root = Path(__file__).resolve().parent
    scripts = root/'.venv'/('Scripts' if os.name == 'nt' else 'bin')
    os.environ['PATH'] = str(scripts)+os.pathsep+os.environ.get('PATH', '')
    result = check()
    if '--json' in sys.argv:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print('Downloader - dependency check\n')
        for name, entry in result.items():
            print(f"  {'OK' if entry['ok'] else 'MISSING':<8} {name:<15} {entry['detail']}")
        if not all(e['ok'] for e in result.values()):
            print('\nMac: brew install ffmpeg deno')
            print('Windows: winget install --id Gyan.FFmpeg --exact')
            print('         winget install --id DenoLand.Deno --exact')
            print('Python packages: run Setup.command / Setup.cmd again.')
    return 0 if all(e['ok'] for e in result.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
