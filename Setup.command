#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"
echo 'Downloader - Mac setup'
if ! command -v python3 >/dev/null; then
  echo 'Python is missing. Install from python.org or run: brew install python'
  read -r -p 'Press Enter to close...'
  exit 1
fi
missing=()
command -v ffmpeg >/dev/null || missing+=(ffmpeg)
command -v ffprobe >/dev/null || { [[ " ${missing[*]} " == *" ffmpeg "* ]] || missing+=(ffmpeg); }
command -v deno >/dev/null || missing+=(deno)
if [ ${#missing[@]} -gt 0 ]; then
  if command -v brew >/dev/null; then
    echo "Missing tools: ${missing[*]}"
    read -r -p 'Install these using Homebrew? [y/N] ' answer
    if [[ "$answer" =~ ^[Yy]$ ]]; then brew install "${missing[@]}"; fi
  else
    echo 'Install FFmpeg and Deno first. See README.md. Homebrew is not installed automatically.'
  fi
fi
chmod +x yt dd Start.command
status=0
if python3 setup.py; then
  echo 'Setup finished. Open Start.command, or open a new Terminal and type yt.'
else
  status=$?
fi
read -r -p 'Press Enter to close...'
exit "$status"
