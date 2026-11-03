# Troubleshooting / मदद

| Problem | What to do |
| --- | --- |
| `yt` not found | Open a new terminal after setup. Use `Start.command` / `Start.cmd` directly. Rerun setup if you moved the folder. |
| Old tool starts | An old alias or earlier PATH entry may win. Setup deliberately does not delete existing aliases. Start the new package directly. |
| Python missing | Install Python 3.10+ from python.org. Reopen the terminal. On Windows try `py --version` or `python --version`. |
| FFmpeg / ffprobe / Deno missing | Install the named tool; reopen your terminal and rerun setup. Run `.venv/bin/python doctor.py` on Mac or `.venv\Scripts\python.exe doctor.py` on Windows for a check. |
| Colours or Hindi text look wrong | Use Windows Terminal or macOS Terminal with a Unicode-capable monospaced font. Font shaping belongs to the terminal. Setup does not replace your font. |
| Size / FPS unavailable | The source did not supply reliable metadata. The tool does not invent it. |
| Source size differs from saved file | Source video+audio byte counts exclude final container/metadata changes. Encoded audio size is known only after conversion. |
| Login required / 403 | Confirm the URL works in your own browser. Where supported, select your own browser under settings. Never share cookie files. Some websites remain unsupported. |
| Unsupported URL | Paste a supported media page or direct media link. Google search-result pages are not media URLs. Update through setup and check yt-dlp support. |
| Resume fails | Some servers do not support ranges or expire signed media URLs. Retrying may require a new source URL or restarting that item. |
| No audio / silent source | Confirm the original has sound and another player can play the selected codec. A silent source cannot produce original sound. |
| HDR video looks wrong | Use a compatible HDR player/display. The selected video codec and HDR are not automatically converted. |
| Space seems ineffective | Focus the running terminal window; pause is available during transfer, not while answering a menu or during later validation. |
| Antivirus/security warning | Do not disable protection. Use the public source, inspect it, and use normal OS/vendor guidance. Packages are not signed native apps. |

## Before reporting a bug

Include OS, Downloader release, what you selected, and the visible error. Share a public test link only if appropriate. Do not upload cookies, passwords, tokens, private URLs, settings/history or your `.downloader` directory.

## Updating / removing

Download the latest release into a stable folder and run setup again. User settings stay separate. To remove it, delete the project folder and the launchers in `~/.downloader/bin`. Remove just the `# Downloader user commands` PATH lines from your shell files (Mac), or that bin entry from your user PATH (Windows). Deleting `~/.downloader` also deletes your settings/history; do that only if you want to reset them. Downloaded media is not removed by deleting the application.
