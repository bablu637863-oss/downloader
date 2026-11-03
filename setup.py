"""Create/update a private virtual environment. Does not change system Python."""
from pathlib import Path
import os
import subprocess
import sys
import venv


def main():
    if sys.version_info < (3, 10):
        raise SystemExit('Python 3.10 or newer is required.')
    root = Path(__file__).resolve().parent
    env = root / '.venv'
    venv.EnvBuilder(with_pip=True).create(env)
    python = env / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    subprocess.run([str(python), '-m', 'pip', 'install', '--upgrade', '-r', str(root/'requirements.txt')], check=True)
    print('\nSetup complete. Start with yt.cmd (Windows) or ./yt (Mac).')
    print('FFmpeg (including ffprobe) and Deno must also be installed; see README.md.')


if __name__ == '__main__':
    main()
