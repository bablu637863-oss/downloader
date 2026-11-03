"""Install dependencies and user-local launch commands; never needs admin Python."""
from pathlib import Path
import argparse
import os
import shutil
import subprocess
import sys
import venv
import shlex

ROOT = Path(__file__).resolve().parent


def install_shortcuts(root=ROOT, home=None, windows=None):
    home = Path(home or Path.home())
    windows = os.name == 'nt' if windows is None else windows
    folder = home / '.downloader' / 'bin'
    folder.mkdir(parents=True, exist_ok=True)
    if windows:
        for name in ('yt', 'dd'):
            (folder/(name+'.cmd')).write_text('@echo off\ncall "'+str(root/(name+'.cmd'))+'" %*\n', encoding='utf-8')
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
            try:
                current, kind = winreg.QueryValueEx(key, 'Path')
            except FileNotFoundError:
                current, kind = '', winreg.REG_EXPAND_SZ
            paths = current.split(';')
            if str(folder).casefold() not in {p.rstrip('\\/').casefold() for p in paths}:
                winreg.SetValueEx(key, 'Path', 0, kind, current.rstrip(';')+';'+str(folder))
        import ctypes
        result = ctypes.c_size_t()
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, 'Environment', 2, 5000, ctypes.byref(result))
    else:
        for name in ('yt', 'YT', 'Yt', 'yT', 'dd', 'DD', 'Dd', 'dD'):
            script = '#!/bin/sh\nexec '+shlex.quote(str(root/('dd' if name.lower()=='dd' else 'yt')))+' "$@"\n'
            path = folder/name
            path.write_text(script, encoding='utf-8')
            path.chmod(0o755)
        # Add PATH only. Do not replace existing aliases or unrelated shell configuration.
        line = 'export PATH='+shlex.quote(str(folder))+':"$PATH"'
        marker = '# Downloader user commands'
        for filename in ('.zshrc', '.bash_profile', '.bashrc'):
            profile = home/filename
            content = profile.read_text(encoding='utf-8') if profile.exists() else ''
            if line not in content:
                with profile.open('a', encoding='utf-8') as out:
                    out.write('\n'+marker+'\n'+line+'\n')
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-shortcuts', action='store_true', help='Do not change user PATH or shell profiles')
    parser.add_argument('--check', action='store_true', help='Only check external media tools')
    args = parser.parse_args()
    if args.check:
        return subprocess.call([sys.executable, str(ROOT/'doctor.py')])
    if sys.version_info < (3, 10):
        raise SystemExit('Python 3.10 or newer is required.')
    print('\nDownloader setup\n')
    env = ROOT/'.venv'
    venv.EnvBuilder(with_pip=True).create(env)
    python = env/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    subprocess.run([str(python), '-m', 'pip', 'install', '--upgrade', '-r', str(ROOT/'requirements.txt')], check=True)
    if not args.no_shortcuts:
        print('User commands installed in:', install_shortcuts())
        print('Open a NEW terminal to use yt / dd. Existing shell aliases are not replaced.')
    print('\nChecking required tools...')
    status = subprocess.call([str(python), str(ROOT/'doctor.py')])
    if status:
        print('\nInstall the missing tools above, then run setup again. Downloading needs FFmpeg and ffprobe.')
        return status
    print('\nReady. Run yt or dd in a NEW terminal, or open Start.command / Start.cmd now.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
