# Downloader

A terminal media downloader for **macOS and Windows**. Paste a supported link, choose an output format and source quality, optionally choose extras, and download.

The interface uses Hindi/Hinglish and English. Mobile apps and a web interface are not included in this release.

## Features

- Supported website URLs, direct media URLs, selected playlist items and multiple links through yt-dlp.
- MP4, MKV, WebM, MP3, M4A, WAV and FLAC output; available captions can be exported as text.
- Resolution, source codec, FPS and HDR choices when the source supplies them. Selected video streams are preserved; no automatic video re-encoding.
- Combined video/audio source sizes when known, sorted largest first. **Unavailable means no reliable size is supplied**. Converted audio sizes are not guessed. Final container size can differ from source payload size.
- Solid green progress, downloaded/total size, speed and elapsed time. **Space pauses/resumes** the download process; Ctrl+C opens the pause/cancel controls.
- Optional thumbnail, trim, filename, captions and source-specific options. Windows uses typed start/end times for trimming; Mac also has the interactive trim control.
- Resume data, download history and local settings; validation with FFmpeg/ffprobe before reporting success.

Website support is determined by yt-dlp and the website. Public reels/stories/video/audio work only when the extractor supports the URL and access is allowed. Private content may need your own browser login. No guarantee for every site, DRM, active live streams, photos, chat backups or whole accounts.

## macOS setup

Install Python 3.10+ and the media tools. With [Homebrew](https://brew.sh/):

```sh
brew install python ffmpeg deno
```

Download this repository using **Code → Download ZIP**, extract it, and open Terminal in that folder:

```sh
python3 setup.py
./yt
```

`./dd` also starts the same tool. If the launcher is not executable after ZIP extraction, run `chmod +x yt dd` first. `yt` and `dd` here are project launchers, not modifications to your shell aliases or the system `dd` utility.

## Windows setup

Use Windows 10/11 and **Windows Terminal** for the best Unicode/colour rendering.

Install [Python 3.10+](https://www.python.org/downloads/windows/), FFmpeg (including `ffprobe`) and [Deno](https://docs.deno.com/runtime/getting_started/installation/). One option with Windows Package Manager is:

```powershell
winget install --id Python.Python.3.13 --exact
winget install --id Gyan.FFmpeg --exact
winget install --id DenoLand.Deno --exact
```

Open a **new terminal** after installation. Download/extract this repository, open its folder in the terminal, then run:

```powershell
py setup.py
.\yt.cmd
```

`.\dd.cmd` also works. Windows filenames are case-insensitive, so `.\YT.cmd` and `.\DD.cmd` work too. If the `py` launcher is unavailable, use `python setup.py`.

Check dependencies if startup reports a missing tool:

```powershell
ffmpeg -version
ffprobe -version
deno --version
```

If `winget` is unavailable, install from the official project instructions and add executable folders to PATH. FFmpeg links Windows builds at [ffmpeg.org/download.html](https://ffmpeg.org/download.html).

## Everyday use

1. Start the launcher and paste a media link. Enter can use a supported link already in your clipboard.
2. Select the output format, then an available quality.
3. Optional settings appear once; press Enter to continue.
4. Press Enter at the final download prompt. Space pauses/resumes the transfer.
5. When successful, the saved folder is printed and opened.

At the home screen: `s` settings, `h` history, `u` update instructions, `q` quit. Set your own download folder, speed limit and cookie browser in settings. Download folder defaults to your home Downloads folder. Cookies are read locally only when you enable the option.

## Update

Replace the project source files with the latest repository version, then run `python3 setup.py` (Mac) or `py setup.py` (Windows) again. This updates dependencies in this project's virtual environment. Settings and history are kept separately in `~/.downloader` (Windows: `%USERPROFILE%\.downloader`).

## Data and troubleshooting

- Each user runs their own installation. There is no hosted service or central upload of your downloads.
- Settings, history and redacted diagnostic JSON stay in the user's `.downloader` folder. Never upload that folder or cookie files to GitHub.
- Interrupted downloads keep resume data under the output folder. Server support determines whether a later transfer can resume.
- Some platforms omit FPS, HDR, duration or complete source sizes. Missing metadata is not invented.
- Font shaping depends on the terminal and installed fonts. Use a Unicode-capable monospaced font; no system font/profile is changed by setup.
- Not all codecs/HDR formats play in every player. A compatible player may be needed; the selected video stream is not silently changed to another codec.
- Windows support includes process-tree pause/resume and pipe handling. Automated checks run on Mac and Windows; see the **Actions** tab for actual results. Do not assume a particular Windows version/site works before checking those results and a local download.

Dependencies and technical details: [yt-dlp installation](https://github.com/yt-dlp/yt-dlp/wiki/Installation), [YouTube EJS/runtime support](https://github.com/yt-dlp/yt-dlp/wiki/EJS), [psutil process controls](https://psutil.readthedocs.io/stable/).

Use only for content you own or are permitted to download. This project is independent of yt-dlp and the supported websites.
