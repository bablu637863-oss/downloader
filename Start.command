#!/bin/sh
cd -- "$(dirname -- "$0")" || exit 1
chmod +x yt dd
./yt
status=$?
if [ "$status" -ne 0 ]; then
  printf '\nRun Setup.command first if dependencies are missing.\n'
  read -r answer
fi
exit "$status"
