#!/usr/bin/env python3
import hashlib, json, math, os, re, select, shutil, signal, socket, subprocess, sys, time, tempfile
from contextlib import contextmanager
from desktop_support import (WINDOWS, file_locks as fcntl, configure_console, open_folder,
                             clipboard_text, pause_tree, stop_tree, WindowsPipe, process_options)
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, urljoin
from datetime import datetime

STATE_DIR = os.path.expanduser(os.environ.get("DOWNLOADER_STATE_DIR", "~/.downloader"))
os.makedirs(STATE_DIR, exist_ok=True)
CFG_PATH = os.path.join(STATE_DIR, "settings.json")
HIST_PATH = os.path.join(STATE_DIR, "history.json")
NAMES = {4320: "8K", 2160: "4K", 1440: "2K", 1080: "Full HD", 720: "HD"}
FORMATS = {"1": ("mp4", False), "2": ("mkv", False), "3": ("webm", False),
           "4": ("mp3", True), "5": ("m4a", True), "6": ("wav", True),
           "7": ("flac", True), "8": ("transcript", False)}
EMBED_THUMB = ("mp4", "mkv", "mp3", "m4a")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, type(default)):
            raise ValueError("wrong JSON structure")
        if isinstance(default, list):
            data = [r for r in data if isinstance(r, dict)]
        return data
    except (OSError, ValueError, TypeError):
        return default.copy() if isinstance(default, (dict, list)) else default


def save_json(path, data):
    tmp = None
    try:
        folder = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(prefix=".ytdl-save-", dir=folder)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except (OSError, ValueError, TypeError) as e:
        print(f"Warning:   जानकारी सेव नहीं हो सकी: {os.path.basename(path)} ({type(e).__name__})")
        return False
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


CFG = {"out": "~/Downloads", "limit": "", "notify": True, "cookies": "",
       "convert": False}
_saved_cfg = load_json(CFG_PATH, {})
for _key, _value in _saved_cfg.items():
    if _key in CFG and type(_value) is type(CFG[_key]):
        CFG[_key] = _value
    elif _key in ("aria_off", "aria_asked") and isinstance(_value, bool):
        CFG[_key] = _value
    elif _key == "last" and isinstance(_value, dict):
        CFG[_key] = _value
if not CFG["out"].strip():
    CFG["out"] = "~/Downloads"
if CFG["cookies"] not in ("", "chrome", "safari", "firefox", "brave"):
    CFG["cookies"] = ""
if CFG["limit"] and not re.fullmatch(r"(?:[0-9]+(?:\.[0-9]+)?)[KMG]?", CFG["limit"]):
    CFG["limit"] = ""
if CFG["limit"]:
    try:
        if not math.isfinite(float(CFG["limit"].rstrip("KMG"))) or float(CFG["limit"].rstrip("KMG")) <= 0:
            CFG["limit"] = ""
    except ValueError:
        CFG["limit"] = ""
if os.path.expanduser(str(CFG.get("out", ""))).rstrip("/") == os.path.expanduser("~/Downloads/YT-Downloads"):
    CFG["out"] = "~/Downloads"
    save_json(CFG_PATH, CFG)
OUT_OVERRIDE = None


def outdir():
    p = os.path.expanduser(OUT_OVERRIDE or CFG["out"])
    os.makedirs(p, exist_ok=True)
    return p


def need(tool):
    if not shutil.which(tool):
        print(f"Error: {tool} is missing. See README.md / setup instructions.")
        sys.exit(1)


def mb(b):
    if not isinstance(b, (int, float)) or not math.isfinite(b) or b < 0:
        return "Unknown"
    if b < 1000:
        return f"{b:.0f} B"
    if b < 1000000:
        return f"{b / 1000:.1f} KB"
    if b >= 1000000000:
        return f"{b / 1000000000:.2f} GB"
    return f"{b / 1000000:.1f} MB"




def hms(sec):
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_time(s):
    if not s.strip():
        return None
    if not re.fullmatch(r"\d+(?:\.\d+)?(?::\d+(?:\.\d+)?){0,2}", s.strip()):
        raise ValueError("invalid time")
    parts = s.strip().split(":")
    sec = 0.0
    for p in parts:
        sec = sec * 60 + float(p)
    if not math.isfinite(sec) or sec < 0:
        raise ValueError("invalid time")
    return sec


def ask(prompt, valid, default=None, back=False):
    while True:
        a = input(prompt).strip().lower()
        if back and a == "b":
            raise Back
        if a == "" and default is not None:
            return default
        if a in valid:
            return a
        print("Warning:   गलत चुनाव, फिर से लिखें।")


def ask_num(prompt, n, default="1", back=False):
    return int(ask(prompt, [str(i) for i in range(1, n + 1)], default, back))


def fsize(f, dur):
    value = f.get("filesize")
    return value if isinstance(value, (int, float)) and math.isfinite(value) and value > 0 else 0



def vlabel(f):
    c = f.get("vcodec") or ""
    if c.startswith("avc"):
        return "H.264"
    if c.startswith(("vp9", "vp09")):
        return "VP9"
    if c.startswith("av01"):
        return "AV1"
    return c.split(".")[0] or "?"


def esc(s):
    return s.replace("%", "%%")


def clean_name(s, limit=160):
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "", str(s)).strip().strip(".")
    return s.encode("utf-8")[:limit].decode("utf-8", "ignore").strip()


def history_add(rec):
    try:
        with open(HIST_PATH + ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            h = load_json(HIST_PATH, [])
            h.append(rec)
            save_json(HIST_PATH, h[-500:])
    except OSError:
        print("Warning:   History सेव नहीं हो सकी।")


def history_find(vid):
    if not vid:
        return None
    for r in reversed(load_json(HIST_PATH, [])):
        if r.get("id") == vid:
            return r
    return None


def notify(msg):
    if not CFG.get("notify") or sys.platform != "darwin":
        return
    msg = re.sub(r'["\\]', "", msg)
    try:
        subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
        subprocess.Popen(["osascript", "-e",
                          f'display notification "{msg}" with title "Media Downloader"'])
    except Exception:
        pass


def base_args():
    a = ["yt-dlp"]
    if CFG.get("cookies"):
        a += ["--cookies-from-browser", CFG["cookies"]]
    for runtime in ("deno", "node", "bun"):
        if shutil.which(runtime):
            a += ["--js-runtimes", runtime]
    return a


BOT_WORDS = ("not a bot", "sign in to confirm", "cookies-from-browser")



class FolderBusy(Exception):
    pass


@contextmanager
def folder_lock(folder):
    folder = os.path.abspath(os.path.expanduser(folder))
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, ".ytdl.lock"), "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise FolderBusy(folder)
        yield


def capture(cmd, timeout=120):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, **process_options())
    try:
        stdout, stderr = p.communicate(timeout=timeout)
        return subprocess.CompletedProcess(cmd, p.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        kill_group(p)
        return subprocess.CompletedProcess(cmd, 124, "", "Operation timed out")
    except BaseException:
        kill_group(p)
        raise
    finally:
        p.stdout.close()
        p.stderr.close()



def redact(value):
    # Do not persist URLs, signed queries, headers, cookie locations or tokens.
    if isinstance(value, str):
        value = re.sub(r"https?://[^\s\"']+", "[URL]", value)
        return re.sub(r"(?i)(authorization|cookie|token|password|secret)(\s*[:=]\s*|\s+)[^\s,;]+",
                      r"\1=[REDACTED]", value)
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if re.search(r"cookie|token|password|secret|headers", k, re.I)
                    else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def diagnostic(spec, rc, tail, stage="download"):
    path = os.path.join(os.path.dirname(CFG_PATH), "last_run.json")
    save_json(path, redact({"time": datetime.now().isoformat(timespec="seconds"),
                           "stage": stage, "returncode": rc,
                           "format": spec.get("ext"), "selection": spec.get("selection"),
                           "tail": list(tail)[-80:]}))


def media_details(path):
    try:
        r = capture(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                     "-of", "json", path], timeout=30)
        if r.returncode:
            return {}
        data = json.loads(r.stdout)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def validate_media(path, is_audio, expected=0, expect_audio=False):
    d = media_details(path)
    streams = d.get("streams", [])
    video = [v for v in streams if v.get("codec_type") == "video"
             and not v.get("disposition", {}).get("attached_pic")]
    audio = [v for v in streams if v.get("codec_type") == "audio"]
    try:
        duration = float(d.get("format", {}).get("duration", 0))
    except (ValueError, TypeError):
        duration = 0
    if not math.isfinite(duration) or duration <= 0 or not (audio if is_audio else video):
        return False, "media stream / duration नहीं मिला"
    if expect_audio and not audio:
        return False, "source में audio है लेकिन output में audio track नहीं मिला"
    if expected and abs(duration - expected) > max(4, expected * .04):
        return False, f"duration mismatch: {duration:.1f}s / expected {expected:.1f}s"
    return True, "streams और duration जाँचे गए"



def decode_check(path):
    r = capture(["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", path,
                 "-map", "0:v?", "-map", "0:a?", "-sn", "-dn", "-f", "null", "-"],
                timeout=max(600, probe_dur(path) * 8))
    return r.returncode == 0, r.stderr.strip()[-500:]


def verify_extras(valid, item, tmp):
    warnings = []
    streams = [stream for path in valid for stream in media_details(path).get("streams", [])]
    files = list_files(tmp)
    if item.get("subs"):
        requested = [code for code in item["subs"].split(",") if code and not code.startswith("-")]
        if item.get("embed"):
            if sum(v.get("codec_type") == "subtitle" for v in streams) < len(requested):
                warnings.append("माँगे गए सभी subtitles embedded output में नहीं मिले")
        else:
            for code in requested:
                if not any(re.search(r"\." + re.escape(code) + r"\.srt$", path) for path in files):
                    warnings.append("subtitle file नहीं मिली: " + code)
    thumb = item.get("thumb")
    if thumb in ("file", "both") and not any(path.lower().endswith((".jpg", ".png", ".webp")) for path in files):
        warnings.append("माँगी गई thumbnail file नहीं मिली")
    if thumb in ("embed", "both") and not any(v.get("disposition", {}).get("attached_pic") or
                                              v.get("codec_type") == "attachment" for v in streams):
        warnings.append("embedded thumbnail cover नहीं मिला")
    return warnings

def actual_quality(path):
    d = media_details(path)
    video = next((v for v in d.get("streams", []) if v.get("codec_type") == "video"
                  and not v.get("disposition", {}).get("attached_pic")), {})
    if not video:
        return "audio"
    return (f"{video.get('width', '?')}×{video.get('height', '?')} "
            f"{video.get('codec_name', '?')} fps={video.get('avg_frame_rate', '?')} "
            f"transfer={video.get('color_transfer', 'unknown')}")


def range_indices(text, n):
    if not re.fullmatch(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*", text):
        raise ValueError("invalid range")
    out = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        lo, hi = int(a), int(b or a)
        if not 1 <= lo <= hi <= n:
            raise ValueError("range out of bounds")
        out.extend(range(lo, hi + 1))
    return list(dict.fromkeys(out))



def extract_urls(raw):
    result = []
    for match in re.findall(r"https?://[^\s<>\"']+", raw, re.I):
        for value in re.split(r",(?=https?://)", match, flags=re.I):
            value = value.rstrip(",")
            while value.endswith(")") and value.count(")") > value.count("("):
                value = value[:-1]
            try:
                parsed = urlsplit(value)
                if parsed.scheme.lower() in ("http", "https") and parsed.hostname:
                    result.append(value)
            except ValueError:
                continue
    return list(dict.fromkeys(result))


def audio_only(info):
    fmts = info.get("formats") or [info]
    has_video = any(f.get("vcodec") not in (None, "none") for f in fmts)
    has_audio = any(f.get("acodec") not in (None, "none") for f in fmts)
    known_audio = info.get("ext") in ("mp3", "m4a", "wav", "flac", "opus", "ogg", "aac")
    explicitly_no_video = all(f.get("vcodec") == "none" for f in fmts)
    return not has_video and (known_audio or (has_audio and explicitly_no_video))


def normalize_info(info):
    info = dict(info)
    if not (info.get("formats") or []) and info.get("url") and not info.get("entries"):
        media = {k: info[k] for k in ("url", "ext", "vcodec", "acodec", "height", "width", "fps",
                 "filesize", "filesize_approx", "protocol", "http_headers", "format_note", "dynamic_range") if k in info}
        media["format_id"] = str(info.get("format_id") or "direct")
        info["formats"] = [media]
    normalized = []
    for f in info.get("formats") or []:
        f = dict(f)
        if f.get("ext") in ("mp3", "m4a", "wav", "flac", "opus", "ogg", "aac"):
            f["vcodec"] = "none"
            f["acodec"] = f.get("acodec") or "unknown"
        elif f.get("ext") in ("mp4", "mkv", "webm", "mov", "avi", "flv", "m4v", "ts", "unknown_video") and f.get("vcodec") is None:
            # Unknown is a Best-source option, never a fabricated resolution.
            f["vcodec"] = "unknown"
        normalized.append(f)
    if normalized:
        info["formats"] = normalized
    return info


def source_is_youtube(info):
    return "youtube" in str(info.get("extractor_key") or info.get("extractor") or "").lower()


def source_error(err):
    low = err.lower()
    if "drm" in low:
        return "यह content DRM-protected है; इस tool से download नहीं होगा।"
    if any(word in low for word in ("unsupported url", "no video formats", "no video found", "no media")):
        return "इस page पर supported media नहीं मिला। Actual video page या direct media link दें।"
    if any(word in low for word in ("login", "sign in", "private", "authentication", "cookies", "401")):
        return "Login/private content है। अपने authorized account के लिए s → 4 से browser cookies चुनें।"
    if any(word in low for word in ("403", "forbidden", "geo", "not available in your")):
        return "Website ने access रोका या content आपके region/account में उपलब्ध नहीं है।"
    return "Source पढ़ा नहीं गया। Link, internet और yt-dlp update जाँचें।"

def entry_url(e, parent_url="", extractor=""):
    u = e.get("url") or e.get("webpage_url") or ""
    if u.startswith(("https://", "http://")):
        return u
    if u.startswith(("/", "./", "../")) and parent_url:
        return urljoin(parent_url, u)
    if "youtube" in str(e.get("ie_key") or extractor).lower():
        return "https://www.youtube.com/watch?v=" + str(e.get("id") or u)
    raise ValueError("इस playlist item का usable media/page URL नहीं मिला")


def choose_batch(info, spec):
    choice = spec.get("selection") or spec.get("last") or {}
    if spec["is_audio"]:
        return dict(spec)
    opts = video_options(info, spec["ext"], info.get("duration") or 0, with_hdr=True)
    if choice.get("h"):
        opts = [o for o in opts if not o.get("any") and 0 < o["h"] <= choice["h"]
                and o.get("known_fps") and o["fps"] <= (choice.get("fps") or 30)
                and o.get("known_range") and o["hdr"] == bool(choice.get("hdr"))]
    else:
        # Best means no resolution promise; show actual output after probing.
        opts = [o for o in opts if not o.get("hdr")]
    if not opts:
        raise ValueError("चुनी quality/FPS/range के अंदर verified format नहीं मिला")
    codec = choice.get("codec")
    preferred = [o for o in opts if o["codec"] == codec]
    o = (preferred or opts)[0]
    out = dict(spec)
    out.update(fargs=["-f", o["fid"]], hdr=o["hdr"], est=o["size"], est_exact=bool(o.get("exact")),
               aria_allowed=aria_safe(info, {"fargs": ["-f", o["fid"]]}))
    print(f"   चुना: {o['label']} {o['codec']} {'HDR' if o['hdr'] else 'SDR/unknown'}")
    return out

def remote_size(f):
    url = f.get("url") or ""
    protocol = f.get("protocol") or urlsplit(url).scheme
    if urlsplit(url).scheme not in ("http", "https") or protocol not in ("http", "https") or f.get("fragments") or re.search(r"\.(m3u8|mpd)(?:[?#]|$)", url):
        return 0
    headers = {k: v for k, v in (f.get("http_headers") or {}).items()
               if k.lower() not in ("range", "accept-encoding")}
    headers["Accept-Encoding"] = "identity"
    headers.setdefault("User-Agent", "Mozilla/5.0")
    for method in ("HEAD", "GET"):
        try:
            h = dict(headers)
            if method == "GET":
                h["Range"] = "bytes=0-0"
            with urlopen(Request(url, headers=h, method=method), timeout=2.5) as response:
                content_type = response.headers.get("Content-Type", "").lower()
                if any(t in content_type for t in ("text/", "json", "mpegurl", "dash+xml")):
                    return 0
                encoding = response.headers.get("Content-Encoding", "identity")
                if encoding not in ("", "identity"):
                    return 0
                content_range = response.headers.get("Content-Range", "")
                match = re.fullmatch(r"bytes\s+\d+-\d+/(\d+)", content_range.strip())
                if match:
                    return int(match[1])
                if response.status == 200:
                    length = int(response.headers.get("Content-Length", "0"))
                    if length > 0:
                        return length
        except (OSError, ValueError):
            pass
    return 0

def enrich_info(info):
    if info.get("entries"):
        return info
    fmts = info.get("formats") or []
    duration = info.get("duration")
    if not duration:
        for f in fmts:
            if isinstance(f.get("duration"), (int, float)) and f["duration"] > 0:
                info["duration"] = f["duration"]
                break
    candidates = []
    for f in fmts:
        if f.get("filesize") or f.get("fragments"):
            continue
        if (f.get("protocol") or urlsplit(f.get("url") or "").scheme) not in ("http", "https"):
            continue
        merged = dict(f)
        merged["http_headers"] = dict(info.get("http_headers") or {}, **(f.get("http_headers") or {}))
        candidates.append((f, merged))
    # Probe the actual menu choices first, not every low-bitrate duplicate.
    ordered = sorted(candidates, key=lambda pair: (real_height(pair[0]) or 0,
                     pair[0].get("tbr") or 0), reverse=True)
    priority, remainder, groups = [], [], set()
    chosen_audio = choose_audio(info, "mp4")
    audio_id = chosen_audio.get("format_id") if chosen_audio else None
    for pair in ordered:
        f = pair[0]
        if f.get("vcodec") not in (None, "none"):
            group = (real_height(f), f.get("fps"), vlabel(f), is_hdr(f))
            if group not in groups:
                groups.add(group)
                priority.append(pair)
            else:
                remainder.append(pair)
        elif f.get("format_id") == audio_id:
            priority.insert(0, pair)
        else:
            remainder.append(pair)
    probes = (priority + remainder)[:8]
    if probes:
        with ThreadPoolExecutor(max_workers=4) as pool:
            sizes = list(pool.map(remote_size, [f for _, f in probes]))
        for (f, _), size in zip(probes, sizes):
            if size > 0:
                f["filesize"] = size
    return info

def size_text(size, exact=False):
    if not exact or not isinstance(size, (int, float)) or not math.isfinite(size) or size <= 0:
        return "Unavailable"
    return mb(size)



def _fetch_once(url, playlist, browser=None):
    flags = ["--flat-playlist", "--yes-playlist"] if playlist else ["--no-playlist"]
    args = base_args()
    if browser:
        args += ["--cookies-from-browser", browser]
    try:
        r = capture(args + ["--socket-timeout", "20", "--extractor-retries", "2", "-J"] + flags + [url],
                    timeout=180 if playlist else 120)
    except OSError as e:
        return None, str(e)
    if r.returncode != 0 or not r.stdout.strip():
        lines = r.stderr.strip().splitlines()
        return None, lines[-1] if lines else "जानकारी नहीं मिली"
    try:
        info = json.loads(r.stdout)
        if not isinstance(info, dict):
            return None, "गलत metadata"
        info = enrich_info(normalize_info(info))
        fmts = info.get("formats") or []
        if info.get("has_drm") or (fmts and all(f.get("has_drm") for f in fmts)):
            return None, "DRM-protected media"
        return info, ""
    except ValueError:
        return None, "जानकारी पढ़ी नहीं जा सकी"



class MixkitPage(HTMLParser):
    """Read only public download choices for the primary Mixkit stock clip."""
    def __init__(self):
        super().__init__()
        self.options, self.labels, self.text = [], {}, []
        self.label = None
        self.heading = 0
        self.heading_seen = False
        self.title = []
        self.poster = None
        self.download_url = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("data-download--modal-url-value"):
            self.download_url = a["data-download--modal-url-value"]
        if tag == "input" and str(a.get("id", "")).startswith("download-size-"):
            self.options.append(a)
        if tag == "label":
            self.label = a.get("for")
            if self.label:
                self.labels.setdefault(self.label, [])
        if tag == "h1" and not self.heading_seen:
            self.heading = 1
            self.heading_seen = True
        if tag == "video" and "video-player__viewer" in a.get("class", ""):
            self.poster = a.get("poster")

    def handle_endtag(self, tag):
        if tag == "label":
            self.label = None
        if tag == "h1":
            self.heading = max(0, self.heading - 1)

    def handle_data(self, data):
        self.text.append(data)
        if self.label:
            self.labels[self.label].append(data)
        if self.heading:
            self.title.append(data)


def mixkit_info_from_html(page, url):
    parser = MixkitPage()
    parser.feed(page)
    formats = []
    for option in parser.options:
        target = urljoin(url, option.get("value") or "")
        if urlsplit(target).hostname not in ("mixkit.co", "www.mixkit.co"):
            continue
        if not urlsplit(target).path.startswith("/free-stock-video/download/"):
            continue
        label = " ".join(parser.labels.get(option.get("id"), []))
        dims = re.search(r"(\d{3,5})\s*[x×]\s*(\d{3,5})", label)
        tier = re.search(r"(?:[?&])type=(\d+)p(?:&|$)", target)
        height = int(dims[2]) if dims else int(tier[1]) if tier else None
        width = int(dims[1]) if dims else None
        try:
            size = float(option.get("data-size") or 0) * 1_000_000
        except ValueError:
            size = 0
        formats.append({"format_id": "mixkit-" + (tier[1] if tier else str(len(formats))),
                        "url": target, "ext": "mp4", "vcodec": "unknown", "acodec": None,
                        "audio_unknown": True, "height": height, "width": width,
                        "format_note": (tier[1] + "p " if tier else "") + option.get("data-label", ""),
                        "filesize_approx": size or None, "protocol": "https",
                        "http_headers": {"Referer": url, "User-Agent": "Mozilla/5.0"}})
    if not formats:
        return None
    text = " ".join(parser.text)
    duration = re.search(r"Duration\s+(\d+:\d+(?::\d+)?)", text)
    fps = re.search(r"Frame Rate\s+(\d+(?:\.\d+)?)\s*fps", text)
    # These values are published for this clip; no unavailable quality is invented.
    if fps:
        for f in formats:
            f["fps"] = float(fps[1])
    match = re.search(r"-(\d+)/?$", urlsplit(url).path)
    return {"id": match[1] if match else hashlib.sha256(url.encode()).hexdigest()[:12],
            "title": " ".join(parser.title).strip() or "Mixkit clip",
            "webpage_url": url, "extractor": "mixkit-public", "extractor_key": "MixkitPublic",
            "formats": formats, "duration": parse_time(duration[1]) if duration else None,
            "thumbnail": urljoin(url, parser.poster) if parser.poster else None,
            "http_headers": {"Referer": url, "User-Agent": "Mozilla/5.0"}}


def fetch_mixkit(url):
    if urlsplit(url).hostname not in ("mixkit.co", "www.mixkit.co") or not re.fullmatch(
            r"/free-stock-video/[^/]+-\d+/?", urlsplit(url).path):
        return None
    def read_page(target, maximum):
        with urlopen(Request(target, headers={"User-Agent": "Mozilla/5.0", "Referer": url}), timeout=30) as response:
            page = response.read(maximum + 1)
            if len(page) > maximum:
                raise ValueError("Mixkit response too large")
            return page.decode("utf-8", "replace")
    info = mixkit_info_from_html(read_page(url, 5_000_000), url)
    if not info:
        return None
    resolved = []
    for f in info["formats"]:
        try:
            modal = MixkitPage()
            modal.feed(read_page(f["url"], 1_000_000))
            media = urlsplit(modal.download_url or "")
            if media.scheme == "https" and media.hostname == "assets.mixkit.co" and media.path.endswith(".mp4"):
                f["url"] = modal.download_url
                resolved.append(f)
        except (OSError, ValueError):
            print("Warning:  Mixkit का एक download option नहीं पढ़ा जा सका।")
    info["formats"] = resolved
    return info if resolved else None


def fetch(url, playlist=False):
    try:
        special = fetch_mixkit(url)
        if special:
            return special, ""
    except (OSError, ValueError):
        print("Warning:  Mixkit के public options नहीं पढ़े गए; generic detection से कोशिश...")
    info, err = _fetch_once(url, playlist)
    if info or not any(w in err.lower() for w in BOT_WORDS) or CFG.get("cookies"):
        return info, err
    # YouTube ने "bot नहीं हो" की जाँच माँगी: आपके browser की login से अपने-आप कोशिश
    print("\n YouTube ने पहचान माँगी है। आपके browser की login से कोशिश कर रहा हूँ...")
    print("   (Mac पासवर्ड / Keychain की अनुमति माँगे तो 'Allow' दबाएं)")
    for br in ("chrome", "safari", "firefox", "brave"):
        info, e2 = _fetch_once(url, playlist, br)
        if info:
            CFG["cookies"] = br
            save_json(CFG_PATH, CFG)
            print(f"    {br.title()} की login से चल गया (आगे अपने-आप यही इस्तेमाल होगा)")
            return info, ""
    return None, ("YouTube में Chrome/Safari से login करके फिर कोशिश करें। "
                  "(Safari के लिए Terminal को System Settings → Privacy → Full Disk Access देना पड़ सकता है)")


TIERS = (144, 240, 360, 480, 720, 1080, 1440, 2160, 4320)


def real_height(f):
    """YouTube जैसा quality नंबर (144p, 360p, 720p, 1080p, 2160p...).
    चौड़े (cinema) और खड़े (Shorts) वीडियो में असली पिक्सेल अजीब होते हैं
    (जैसे 3840x2026), पर YouTube उन्हें 2160p ही कहता है।"""
    m = re.match(r"\s*(\d{3,4})p", f.get("format_note") or "")
    if m:
        return int(m[1])
    h, w = f.get("height") or 0, f.get("width") or 0
    if not (h and w):
        return h
    val = max(min(h, w), max(h, w) * 9 / 16)
    best = min(TIERS, key=lambda t: abs(t - val))
    return best if abs(best - val) <= best * 0.05 else int(round(val))


def usable(f):
    note = (f.get("format_note") or "").lower()
    return not (f.get("has_drm") or "premium" in note or "drm" in note
                or "storyboard" in note or f.get("ext") == "mhtml")



def choose_audio(info, ext):
    candidates = [f for f in info.get("formats") or [] if usable(f)
                  and f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none")]
    if not candidates:
        return None
    language = str(info.get("language") or "").split("-")[0].lower()
    preferred_ext = "m4a" if ext in ("mp4", "m4a") else "webm" if ext == "webm" else None
    def rank(f):
        pref = f.get("language_preference") or 0
        note = str(f.get("format_note") or "").lower()
        original = pref >= 10 or "original" in note
        lang = str(f.get("language") or "").split("-")[0].lower()
        return (original, bool(language and lang == language), pref,
                bool(preferred_ext and f.get("ext") == preferred_ext),
                "drc" not in note and "drc" not in str(f.get("format_id")),
                f.get("abr") or f.get("tbr") or 0)
    return max(candidates, key=rank)


def audio_description(f):
    if not f:
        return "source की embedded audio / metadata उपलब्ध नहीं"
    lang = f.get("language") or "language unknown"
    original = (f.get("language_preference") or 0) >= 10 or "original" in str(f.get("format_note") or "").lower()
    name = globals().get("LANGNAMES", {}).get(str(lang).split("-")[0], str(lang))
    return name + (" (original)" if original else "")


def audio_signal(path):
    if not has_audio(path):
        return "missing"
    r = capture(["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-v", "info", "-i", path,
                 "-map", "0:a:0", "-vn", "-af", "volumedetect", "-f", "null", "-"],
                timeout=max(120, probe_dur(path) * 2))
    match = re.search(r"max_volume:\s*(-?inf|-?\d+(?:\.\d+)?) dB", r.stderr)
    if r.returncode or not match:
        return "unknown"
    return "silent" if float(match[1]) <= -90 else "audible"

def video_options(info, ext, dur, with_hdr=True):
    fmts = [f for f in (info.get("formats") or []) if usable(f)]
    vids = [f for f in fmts if f.get("vcodec") not in (None, "none")
            and f.get("height") and f.get("acodec") in (None, "none")]
    if not vids:
        vids = [f for f in fmts if f.get("vcodec") not in (None, "none") and f.get("height")]
    direct = [f for f in vids if "m3u8" not in (f.get("protocol") or "")]
    if direct:
        # HLS (m3u8) सिर्फ़ तब जोड़ो जब वह सीधे formats से ज़्यादा ऊँची quality दे
        top = max(real_height(f) or 0 for f in direct)
        hls = [f for f in vids if f not in direct and (real_height(f) or 0) > top]
        vids = direct + hls
    else:
        vids = vids
    auds = [f for f in fmts if f.get("vcodec") in (None, "none")
            and f.get("acodec") not in (None, "none")]
    aud = choose_audio(info, ext)
    asize = fsize(aud, dur) if aud else 0
    best = {}
    for f in vids:
        c = vlabel(f)
        if ext == "webm" and c not in ("VP9", "AV1", "vp8"):
            continue
        hdr = is_hdr(f)
        if hdr and not with_hdr:
            continue
        fps = float(f.get("fps") or 30)
        key = (real_height(f), fps, c, hdr)
        if key not in best or (f.get("tbr") or 0) > (best[key].get("tbr") or 0):
            best[key] = f
    rank = {"H.264": 0, "VP9": 1, "AV1": 2}
    hi = {k[0] for k in best if k[1] > 30}
    out = []
    for key in sorted(best, key=lambda k: (-k[0], -k[1], not k[3], rank.get(k[2], 9))):
        h, _, c, hdr = key
        f = best[key]
        fps = float(f.get("fps") or 30)
        label = f"{h}p" + (f"{fps:g}" if (fps > 30 or h in hi) else "")
        ac = f.get("acodec")
        if ac not in (None, "none") or f.get("audio_unknown"):
            fid = f["format_id"]                                   # पहले से आवाज़ वाला
        elif aud:
            fid = f["format_id"] + "+" + aud["format_id"]
        else:                                                      # आवाज़ अलग होगी तो जोड़ दो
            fid = f"{f['format_id']}+ba/{f['format_id']}"
        paired = ac in (None, "none") and aud is not None and not f.get("audio_unknown")
        exact = bool(f.get("filesize")) and (not paired or bool(aud.get("filesize")))
        video_bytes = fsize(f, dur)
        total_bytes = video_bytes + (asize if paired else 0) if video_bytes and (not paired or asize) else 0
        out.append({"h": h, "fps": fps, "label": label, "codec": c, "hdr": hdr,
                    "size": total_bytes, "fid": fid, "exact": exact,
                    "audio": audio_description(aud) if paired else "source की embedded audio / metadata उपलब्ध नहीं",
                    "known_fps": bool(f.get("fps")),
                    "known_range": bool(f.get("dynamic_range")) or c == "H.264"})
    if not out and not vids and (info.get("vcodec") not in (None, "none") or
                    any(f.get("vcodec") not in (None, "none") for f in fmts)):
        # Instagram / Facebook / X / TikTok जैसी site जो "कितने पिक्सेल" बताती ही नहीं:
        # एक सीधा "Best" विकल्प, ताकि सूची खाली न रहे
        sz = info.get("filesize") or 0
        if not sz:
            sz = max([fsize(f, dur) for f in fmts] or [0])
        out.append({"h": 0, "fps": 0, "label": "Best", "codec": "-", "hdr": False,
                    "size": sz, "fid": "bv*+ba/b", "any": True, "exact": bool(info.get("filesize"))})
    return out



def audio_options(ext):
    if ext == "wav":
        return [("WAV (बिना कंप्रेशन)", "0", 1411)]
    if ext == "flac":
        return [("Best (पूरी quality)", "0", 900)]
    if ext == "mp3":
        return [("Best (320 kbps)", "320K", 320), ("High 256 kbps", "256K", 256),
                ("Good 192 kbps", "192K", 192), ("Medium 128 kbps", "128K", 128),
                ("Low 64 kbps", "64K", 64)]
    return [("Best", "0", 256), ("High 256 kbps", "256K", 256), ("Good 192 kbps", "192K", 192),
            ("Medium 128 kbps", "128K", 128), ("Low 64 kbps", "64K", 64)]


def choose_format(info=None):
    info = info or {}
    only_audio = audio_only(info)
    allowed = [k for k, (_, is_audio) in FORMATS.items() if is_audio or not only_audio]
    captions = bool(info.get("subtitles") or info.get("automatic_captions"))
    if not captions:
        allowed = [k for k in allowed if k != "8"]
    print("\n" + heading("Output Format") + "\n")
    source_exts = sorted({str(f.get("ext")) for f in (info.get("formats") or [info]) if f.get("ext") and usable(f)})
    labels = {"1":"MP4 — video", "2":"MKV — video", "3":"WEBM — compatible video codecs",
              "4":"MP3 — audio conversion", "5":"M4A — audio", "6":"WAV — uncompressed output",
              "7":"FLAC — lossless output", "8":"TRANSCRIPT — उपलब्ध captions से"}
    for k in allowed:
        print(f"  {k}) {labels[k]}")
    if only_audio:
        print("  ℹ यह audio source है; इससे नया video नहीं बनाया जाएगा।")
    default = "5" if only_audio and info.get("ext") == "m4a" else "4" if only_audio else "1"
    return FORMATS[ask(f"नंबर चुनें [{default}] (b = वापस): ", allowed, default, back=True)]


def size_colour(text, size, largest):
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR") or not largest or not size:
        return text
    # 'largest' is the rank group here, so every nontrivial table gets three bands.
    colour = {1: "38;5;151", 2: "38;5;180", 3: "38;5;153"}[largest]
    return col(colour, text)



def verify_menu_sizes(info, opts):
    # Recheck the actual selected streams, including extractor-reported sizes.
    ids = {fid for o in opts for fid in o["fid"].split("+") if "/" not in fid}
    candidates = []
    for f in info.get("formats") or []:
        if f.get("format_id") not in ids or f.get("_size_checked") or not f.get("url"):
            continue
        if (f.get("protocol") or urlsplit(f["url"]).scheme) not in ("http", "https") or f.get("fragments"):
            continue
        merged = dict(f)
        merged["http_headers"] = dict(info.get("http_headers") or {}, **(f.get("http_headers") or {}))
        candidates.append((f, merged))
    probes = candidates[:40]
    if probes:
        with ThreadPoolExecutor(max_workers=8) as pool:
            sizes = list(pool.map(remote_size, [m for _, m in probes]))
        for (f, _), size in zip(probes, sizes):
            f["_size_checked"] = True
            if size > 0:
                f["filesize"] = size
                f["_size_verified"] = True


def choose_quality(info, ext, is_audio, dur, count, single):
    if is_audio:
        opts = sorted(audio_options(ext), key=lambda o: o[2], reverse=True)
        default = 1
        print("\n" + heading("Audio quality") + "\n")
        for i, o in enumerate(opts, 1):
            estimate = o[2] * 125 * dur * count
            band = min(3, (i - 1) * 3 // len(opts) + 1)
            size = size_colour(size_text(estimate).rjust(14), estimate, band)
            print(f"  {i:>3})   {o[0]:<28}   {size}")
        n = ask_num(f"\nनंबर [{default}], b = वापस: ", len(opts), str(default), back=True)
        return audio_q(ext, opts[n - 1], dur, count)
    opts = video_options(info, ext, dur, with_hdr=single)
    if not opts:
        return None
    verify_menu_sizes(info, opts)
    opts = video_options(info, ext, dur, with_hdr=single)
    opts.sort(key=lambda o: (not bool(o["size"]), -o["size"], -o["h"], -o["fps"]))
    candidates = [(i, o) for i, o in enumerate(opts, 1) if not o["hdr"]]
    default = max(candidates, key=lambda pair: (pair[1]["h"], pair[1]["fps"], pair[1]["size"]))[0] if candidates else 1
    print("\n" + heading("Quality") + "\n")
    print(f"  {'No.':>4}   {'Quality':<9}   {'Type':<9}   {'Codec':<8}   {'FPS':>7}   {'HDR':<7}   {'Size':>14}")
    print("  " + "-" * 85)
    known = [o for o in opts if o["size"]]
    for i, o in enumerate(opts, 1):
        quality = f"{o['h']}p" if o['h'] else "Best"
        fps = f"{o['fps']:g}" if o.get("known_fps") else "Unknown"
        hdr = "HDR" if o["hdr"] else ""
        band = 3 - min(2, known.index(o) * 3 // len(known)) if o["size"] else 1
        size = size_colour(size_text(o["size"], o.get("exact")).rjust(14), o["size"], band)
        print(f"  {i:>3})   {quality:<9}   {NAMES.get(o['h'], ''):<9}   {o['codec']:<8}   {fps:>7}   {hdr:<7}   {size}")
    
    if count > 1:
        print("  Size: पहले item के source streams।")
    n = ask_num(f"\nनंबर [{default}], b = वापस: ", len(opts), str(default), back=True)
    return make_q(opts[n - 1], ext, count, single)




def show_info(info, dur):
    print(f"\nℹ  {info.get('title')}")
    d = info.get("upload_date") or "?"
    if len(d) == 8:
        d = f"{d[6:]}-{d[4:6]}-{d[:4]}"
    print(f"   Channel : {info.get('uploader') or '?'}")
    print(f"   Views   : {info.get('view_count') or '?'}   |   तारीख: {d}")
    print(f"   Duration: {hms(dur)}")
    print("\n    वीडियो (source size):")
    for o in video_options(info, "mkv", dur):
        print(f"     {o['label']:<9} {o['codec']:<6} {'HDR' if o['hdr'] else '':<4} {size_text(o['size'], o.get('exact'))}")
    print("\n    Audio output: MP3, M4A, WAV, FLAC")
    manual = sorted(info.get("subtitles") or {})
    print("\n    Subtitles:", ", ".join(manual[:15]) if manual else "कोई तैयार नहीं"
          + (" (auto-generated मिल सकते हैं)" if info.get("automatic_captions") else ""))
    print(f"    Chapters: {len(info.get('chapters') or [])}")


def junk_strict(f):
    return bool(re.search(r"\.part(?:$|[.-])", f)) or f.endswith((".ytdl", ".temp", ".aria2"))


def junk_fail(f):
    return junk_strict(f) or bool(re.search(r"\.f[0-9a-zA-Z_-]+\.[a-z0-9]+$", f))


def probe(path):
    d = media_details(path)
    v = next((v for v in d.get("streams", []) if v.get("codec_type") == "video"
              and not v.get("disposition", {}).get("attached_pic")), {})
    return v.get("codec_name"), int(v.get("height") or 0)


def probe_dur(path):
    try:
        v = float(media_details(path).get("format", {}).get("duration", 0))
        return v if math.isfinite(v) and v > 0 else 0
    except (TypeError, ValueError):
        return 0


def has_audio(path):
    return any(v.get("codec_type") == "audio" for v in media_details(path).get("streams", []))


def silent_videos(root):
    # Informational only; silence never triggers destructive redownload.
    return any(not has_audio(p) for p in list_files(root)
               if p.lower().endswith((".mp4", ".mkv", ".webm")) and not junk_strict(os.path.basename(p)))


def ensure_playable(path):
    """Lossless MP4 remux only; never silently change the selected codec/HDR."""
    codec, _ = probe(path)
    tmp = os.path.splitext(path)[0] + ".fix.mp4"
    cmd = ["ffmpeg", "-y", "-nostdin", "-loglevel", "error", "-i", path,
           "-map", "0", "-c", "copy", "-movflags", "+faststart"]
    if codec == "hevc":
        cmd += ["-tag:v", "hvc1"]
    audio_streams = [v for v in media_details(path).get("streams", []) if v.get("codec_type") == "audio"]
    if any(v.get("codec_name") not in ("aac", "alac", "mp3") for v in audio_streams):
        print("    MP4 audio को compatible AAC में बदल रहे हैं; video quality वही रहेगी।")
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    try:
        r = capture(cmd + [tmp], timeout=600)
        ok, _ = validate_media(tmp, False, probe_dur(path), has_audio(path))
        if r.returncode == 0 and ok:
            os.replace(tmp, path)
            return True
        print("   Warning:  MP4 preparation नहीं हुई; downloaded file सुरक्षित रखी है।")
        return False
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)



def encode_m4a(path, quality):
    tmp = os.path.splitext(path)[0] + ".encode.m4a"
    try:
        r = capture(["ffmpeg", "-y", "-nostdin", "-v", "error", "-i", path,
                     "-map", "0", "-c", "copy", "-c:a", "aac", "-b:a", quality, tmp],
                    timeout=max(600, probe_dur(path) * 5))
        ok, _ = validate_media(tmp, True, probe_dur(path))
        if r.returncode == 0 and ok:
            os.replace(tmp, path)
            return True
        return False
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

def convert_h264(path):
    d = media_details(path)
    videos = [v for v in d.get("streams", []) if v.get("codec_type") == "video"
              and not v.get("disposition", {}).get("attached_pic")]
    if not videos or videos[0].get("codec_name") == "h264":
        return True
    v = videos[0]
    # Guard actual media, not merely the selected menu option. Unknown transfer
    # characteristics are preserved instead of risking an HDR->SDR conversion.
    if v.get("color_transfer") not in ("bt709", "smpte170m", "gamma22", "gamma28"):
        print("   Warning:  HDR/unknown colour range: conversion छोड़ी, original सुरक्षित है।")
        return False
    tmp = os.path.splitext(path)[0] + ".conv.mp4"
    total = probe_dur(path)
    print("    H.264 conversion (software encoder; original सुरक्षित है)...")
    try:
        cmd = ["ffmpeg", "-y", "-nostdin", "-v", "error", "-i", path, "-map", "0", "-c", "copy",
               "-c:v:0", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt:v:0", "yuv420p",
               "-movflags", "+faststart", tmp]
        r = capture(cmd, timeout=max(600, total * 10))
        ok, _ = validate_media(tmp, False, total, has_audio(path))
        if r.returncode == 0 and ok:
            os.replace(tmp, path)
            return True
        print("   Warning:  Conversion नहीं हुई; original codec वाली file रखी है।")
        return False
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ===== प्रोग्रेस बार =====
def bar(pct, width=24):
    filled = max(0, min(width, int(width * pct / 100)))
    return "█" * filled + " " * (width - filled)




def draw_block(st, lines):
    import unicodedata
    columns = max(10, shutil.get_terminal_size((100, 30)).columns - 1)
    fitted = []
    for line in lines:
        clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line)
        chars, width = [], 0
        for c in clean:
            advance = 0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
            if width + advance > columns:
                break
            chars.append(c)
            width += advance
        visible = "".join(chars)
        if sys.stdout.isatty() and not os.environ.get("NO_COLOR"):
            visible = re.sub("█+", lambda m: "\033[32m" + m[0] + "\033[0m", visible)
        fitted.append(visible)
    previous = st.get("blk", 0)
    count = max(previous, len(fitted))
    fitted += [""] * (count - len(fitted))
    out = f"\033[{previous}A" if previous else ""
    out += "".join("\r\033[2K" + line + "\n" for line in fitted)
    sys.stdout.write(out)
    sys.stdout.flush()
    st["blk"] = count



def fmt_block(label, pct, dl, total, speed, eta_s, elapsed, approx=False):
    if total and not approx:
        size = f"{mb(dl)} / {mb(total)}"
    else:
        size = f"{mb(dl)} / Unavailable"
    if speed is None:
        sp = "Measuring..."
    elif speed >= 1000000:
        sp = f"{speed / 1000000:.1f} MB/s"
    else:
        sp = f"{speed / 1000:.0f} KB/s"
    left = hms(eta_s) if eta_s is not None else "..."
    return [f"Download  [{bar(pct)}] {min(pct, 100):5.1f}%",
            f"   Downloaded : {size}",
            f"   Speed      : {sp}",
            f"   Time left  : {left}        Elapsed : {hms(elapsed)}"]


def moving_speed(samples, now, cum):
    samples.append((now, cum))
    while len(samples) > 2 and now - samples[0][0] > 6:
        samples.pop(0)
    if len(samples) > 1 and samples[-1][0] > samples[0][0]:
        v = (samples[-1][1] - samples[0][1]) / (samples[-1][0] - samples[0][0])
        return v if v > 0 else None
    return None


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def kill_group(p):
    if stop_tree(p):
        return
    try:
        pause_tree(p.pid, resume=True)
        os.killpg(p.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait(timeout=5)


def pause_menu(p):
    try:
        pause_tree(p.pid)
    except OSError:
        return False
    print("\n\n⏸  रुका हुआ है (Pause)")
    try:
        a = input("   ▶  Enter = जारी रखें (Resume)     c = रद्द करें: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        a = "c"
    if a == "c":
        return False
    try:
        pause_tree(p.pid, resume=True)
    except OSError:
        pass
    print("   ▶  जारी है...\n")
    return True


ARIA_RE = re.compile(r"\[#\w+\s+(\d+)B/(\d+)B(?:\(\d+%\))?(?:\s+CN:\d+)?(?:\s+SD:\d+)?(?:\s+DL:(\d+)B)?")
RC_STALL = 997
START_SECS = 90      # इतनी देर में पहला byte न आए तो दूसरे तरीके से दोबारा
ARIA_START_SECS = 30  # aria2 के लिए और जल्दी
STALL_SECS = 40      # इतने सेकंड में एक भी byte न बढ़े तो कनेक्शन अटका माना जाएगा


def run_with_bar(cmd, parts=None, seg=None, est_total=0, t0=None, start_secs=None, processing_secs=600, media_duration=None, total_exact=False):
    if not WINDOWS:
        import termios, tty
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         **process_options())
    fd = p.stdout.fileno()
    windows_pipe = WindowsPipe(fd) if WINDOWS else None
    segment = seg or media_duration
    st = {"part": 1, "drawn": False, "done": 0.0, "samples": [], "active": False,
          "last_cum": -1.0, "last_move": time.monotonic(), "left": 0,
          "started": False}
    t_start = t0 or time.monotonic()
    t_proc = time.monotonic()

    def waiting():
        draw_block(st, [f"⏳ कनेक्ट हो रहा है...   ({int(time.monotonic() - t_proc)} सेकंड)"])

    waiting()
    tail = []

    def prog(status, dl, tot, est, spd, eta):
        st["started"] = True
        if True:
            total = tot or est
            # कुल size एक बार तय होकर टिका रहे (बार-बार बदलना बंद)
            if dl is not None and total:
                key = ("lock", st["part"])
                if tot:
                    st[key] = tot
                elif key not in st:
                    ok_est = est_total and (not parts or parts <= 1) and \
                        abs(est_total - total) <= 0.3 * max(total, est_total)
                    st[key] = est_total if ok_est else total
                if dl > st[key]:
                    st[key] = dl
                total = st[key]
            label = f" भाग {st['part']}" + (f"/{parts}" if parts else "")
            if dl is not None:
                now = time.monotonic()
                cum = st["done"] + dl
                if status == "finished":
                    speed = st.get("last_speed")
                else:
                    speed = moving_speed(st["samples"], now, cum)
                    if st.get("aria_fresh") and spd:
                        speed = spd
                    elif not speed and spd and spd > 0:
                        speed = spd
                    st["last_speed"] = speed
                grand = None
                if est_total and est_total > cum:
                    if not parts or parts <= 1:
                        # हमारा अंदाज़ा तभी, जब वह असली total से मेल खाता हो
                        if not total or abs(est_total - total) <= 0.3 * total:
                            grand = est_total
                    else:
                        grand = est_total
                remain = (grand - cum) if grand else ((total - dl) if total else None)
                eta_s = remain / speed if (speed and remain is not None) else (
                    eta if (eta and eta > 0) else None)
                pct = dl / total * 100 if total else 0
                show_dl, show_tot, approx_ = dl, total, not tot or total != tot
                multi = bool(parts and parts > 1)
                if multi:
                    # वीडियो + आवाज़ = एक ही बार, एक ही कुल size
                    cur = tot or est or 0
                    base = st["done"] + cur
                    lastp = st["part"] >= parts
                    if lastp and tot:
                        g = st["done"] + tot
                    else:
                        g = (est_total if (est_total and 0.5 * est_total <= base <= 1.3 * est_total)
                             else base)
                        g = max(g, st.get("grand", 0))
                    if cum > g:
                        g = cum * 1.03
                    st["grand"] = g
                    show_dl, show_tot, approx_ = cum, g, not (lastp and tot)
                    pct = cum / g * 100 if g else 0
                    label = " Download (वीडियो + आवाज़)"
                    eta_s = ((g - cum) / speed) if speed else eta_s
                if total_exact and est_total and not seg and cum <= est_total:
                    show_dl, show_tot, approx_ = cum, est_total, False
                    pct = cum / est_total * 100
                    eta_s = (est_total - cum) / speed if speed else eta_s
                st["fin"] = (label, max(cum, st.get("fin", ("", 0))[1]))
                if cum > st["last_cum"]:
                    st["last_cum"], st["last_move"] = cum, now
                st["active"] = status != "finished"
                st["left"] = (total - dl) if total else 0
                # Keep slow connections while bytes arrive; retry only on stall/error.
                draw_block(st, fmt_block(label, pct, show_dl, show_tot, speed, eta_s,
                                         now - t_start, approx=approx_))
            st["drawn"] = True
            if status == "finished":
                st["done"] += (tot or dl or 0)
                st["part"] += 1
                st["active"] = False


    def handle(line):
        if line.startswith("PROG|"):
            f = line.split("|")
            if len(f) < 7:
                return
            st["aria_fresh"] = False
            prog(f[1], _f(f[2]), _f(f[3]), _f(f[4]), _f(f[5]), _f(f[6]))
        elif "[#" in line and "CN:" in line:
            m = ARIA_RE.search(line)
            if m:
                st["aria_fresh"] = True
                prog("downloading", float(m[1]), float(m[2]) or None, None,
                     float(m[3]) if m[3] else None, None)
        elif segment and "time=" in line and "speed=" in line:
            m = re.search(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", line)
            if m:
                st["started"] = True
                st["active"] = True
                now = time.monotonic()
                sec = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
                if sec > st.get("trim_sec", -1):
                    st["last_move"] = now
                    st["trim_sec"] = sec
                sp = re.search(r"speed=\s*([\d.]+)x", line)
                eta_s = (segment - sec) / float(sp[1]) if sp and float(sp[1]) > 0 else None
                ms = re.search(r"size=\s*(\d+(?:\.\d+)?)\s*([kKmMgG])i?B", line)
                sizeb, speed = 0.0, None
                if ms:
                    sizeb = float(ms[1]) * {"k": 1024, "m": 1048576, "g": 1 << 30}[ms[2].lower()]
                    speed = moving_speed(st["samples"], now, sizeb)
                frac = min(1.0, sec / segment) if segment else 0
                total = est_total or 0
                if frac >= 0.05 and sizeb > 0:
                    # अब तक की रफ़्तार से पूरे हिस्से का असली size (धीरे-धीरे पक्का होता है)
                    proj = sizeb / frac
                    old = st.get("proj")
                    st["proj"] = proj if not old else old * 0.9 + proj * 0.1
                    total = st["proj"]
                elif st.get("proj"):
                    total = st["proj"]
                total = max(total, sizeb) or None
                st["last_speed"] = speed
                label = " Trim वाला हिस्सा" if seg else " FFmpeg download"
                st["fin"] = (label, max(sizeb, st.get("fin", ("", 0))[1]))
                draw_block(st, fmt_block(label, sec / segment * 100, sizeb, total,
                                         speed, eta_s, now - t_start, approx=True))
                st["drawn"] = True
        elif line.strip():
            tail.append(line.strip())
            del tail[:-80]

    buf = b""
    why = None
    key_fd = None
    terminal_state = None
    space_paused = False
    pause_started = 0
    try:
        if not WINDOWS and sys.stdin.isatty():
            key_fd = sys.stdin.fileno()
            terminal_state = termios.tcgetattr(key_fd)
            tty.setcbreak(key_fd)
        while True:
            try:
                if WINDOWS:
                    data, key = windows_pipe.poll()
                    if key == " ":
                        if not space_paused:
                            pause_tree(p.pid)
                            pause_started = time.monotonic()
                            space_paused = True
                            draw_block(st, ["Paused - Space to resume"])
                        else:
                            pause_tree(p.pid, resume=True)
                            t_proc += time.monotonic() - pause_started
                            st["last_move"] = time.monotonic()
                            st["samples"].clear()
                            space_paused = False
                else:
                    ready = select.select([fd] + ([key_fd] if key_fd is not None else []), [], [], .2)[0]
                    if key_fd is not None and key_fd in ready:
                        key = os.read(key_fd, 1)
                        if not key:
                            key_fd = None
                        elif key == b" ":
                            if not space_paused:
                                pause_tree(p.pid)
                                pause_started = time.monotonic()
                                space_paused = True
                                draw_block(st, ["Paused · Space दबाकर जारी करें"])
                            else:
                                pause_tree(p.pid, resume=True)
                                t_proc += time.monotonic() - pause_started
                                st["last_move"] = time.monotonic()
                                st["samples"].clear()
                                space_paused = False
                    data = os.read(fd, 4096) if fd in ready else None
            except KeyboardInterrupt:
                paused_at = time.monotonic()
                if pause_menu(p):
                    space_paused = False
                    t_proc += time.monotonic() - paused_at
                    st["samples"].clear()
                    st["blk"] = 0
                    st["last_move"] = time.monotonic()

                    continue
                raise
            now = time.monotonic()
            if data == b"":
                break
            if data:
                buf += data
                pieces = re.split(rb"[\r\n]", buf)
                buf = pieces.pop()
                for piece in pieces:
                    handle(piece.decode("utf-8", "replace"))
            if space_paused:
                if p.poll() is not None:
                    break
                continue
            if not st["started"]:
                if now - t_proc > (start_secs or START_SECS):
                    why = RC_STALL
                else:
                    waiting()
            if st["active"] and now - st["last_move"] > STALL_SECS:
                why = RC_STALL
            if st["started"] and not st["active"] and now - st["last_move"] > processing_secs:
                tail.append("ERROR: post-processing timed out")
                why = RC_STALL
            if why:
                kill_group(p)
                st["blk"] = 0
                print()
                break
        if buf and not why:
            handle(buf.decode("utf-8", "replace"))
        p.wait()
    except BaseException:
        kill_group(p)
        print()
        raise
    finally:
        if terminal_state is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, terminal_state)
        p.stdout.close()
    if (not why and p.returncode == 0 and st.get("fin") and st["fin"][1] > 0
            and st.get("blk", 0) > 0):
        # आख़िर में बार 100% और असली size के साथ पूरा दिखे
        lab, sz = st["fin"]
        el = time.monotonic() - t_start
        lines = fmt_block(lab, 100.0,
                          sz, sz, st.get("last_speed"), 0, el)
        lines = [ln.replace("Speed      :", "Last speed :") for ln in lines]
        draw_block(st, lines)
    if why:
        return why, tail
    return p.returncode, tail


TPL = ("download:PROG|%(progress.status)s|%(progress.downloaded_bytes)s|"
       "%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|"
       "%(progress.speed)s|%(progress.eta)s")


def have_aria():
    return bool(shutil.which("aria2c"))


def save_formats(info, spec):
    keys = ("format_id", "protocol", "ext", "width", "height", "fps", "tbr", "abr",
            "vcodec", "acodec", "language", "language_preference", "audio_channels", "filesize", "filesize_approx", "format_note")
    data = {"extractor": info.get("extractor_key"), "chosen": spec.get("fargs"),
            "formats": [{k: f.get(k) for k in keys} for f in (info.get("formats") or [])]}
    save_json(os.path.join(os.path.dirname(CFG_PATH), "last_formats.json"), redact(data))


def strip_range(info):
    """कुछ साइटें हर format में Range header डालती हैं जिससे आधी फ़ाइल पर अटक जाता है; हटा दो।"""
    try:
        info = json.loads(json.dumps(info))
        for f in [info] + list(info.get("formats") or []):
            h = f.get("http_headers")
            if isinstance(h, dict):
                for k in [k for k in h if k.lower() == "range"]:
                    del h[k]
    except Exception:
        pass
    return info


def aria_safe(info, q):
    """aria2 सिर्फ़ तब, जब चुना हुआ format एक सीधी https फ़ाइल हो (जैसे YouTube)।
    टुकड़ों वाले (HLS/DASH fragments) social-media वीडियो में वह गड़बड़ करता है।"""
    try:
        fa = q.get("fargs") or []
        if len(fa) != 2 or fa[0] != "-f":
            return False
        byid = {f.get("format_id"): f for f in (info.get("formats") or [])}
        for fid in fa[1].split("+"):
            f = byid.get(fid)
            if not f or (f.get("protocol") or "") not in ("https", "http") or f.get("fragments"):
                return False
        return True
    except Exception:
        return False


def clean_partials(root):
    """अधूरी फ़ाइलें हटाओ (तरीका बदलते समय, ताकि पुराने टुकड़े नए तरीके को न बिगाड़ें)।"""
    for p in list_files(root):
        n = os.path.basename(p)
        if re.search(r"\.(part|aria2|ytdl|temp)$|\.part-Frag\d+|\.part\.", n):
            try:
                os.remove(p)
            except OSError:
                pass


def have_js():
    for runtime in ("deno", "node", "bun"):
        if shutil.which(runtime):
            try:
                if capture([runtime, "--version"], timeout=5).returncode == 0:
                    return True
            except OSError:
                pass
    return False


def offer_speedup(force=False):
    need_aria = not have_aria()
    need_js = not have_js()
    if not (need_aria or need_js):
        if force:
            print(" तेज़ download पहले से चालू है (aria2 मौजूद है)।")
            time.sleep(1.5)
        return
    if not shutil.which("brew"):
        return
    if not force and CFG.get("aria_asked"):
        return
    pk = (["aria2"] if need_aria else []) + (["deno"] if need_js else [])
    print("\n तेज़ Download के लिए एक बार का सेटअप")
    if need_aria:
        print("   • aria2: एक साथ कई connection से download (speed कई गुना तक)")
    if need_js:
        print("   • deno: YouTube की speed-रोक (throttling) हटाने के लिए")
    try:
        a = input("   Enter = अभी install करें (1-2 मिनट)     n = अभी नहीं:  ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        a = "n"
    CFG["aria_asked"] = True
    save_json(CFG_PATH, CFG)
    if a == "n":
        return
    print(f"\n⏳ चल रहा है: brew install {' '.join(pk)}\n")
    try:
        subprocess.run(["brew", "install"] + pk)
    except KeyboardInterrupt:
        print("\n रोक दिया गया।")
        return
    print("\n हो गया।" if (have_aria() and have_js()) else "\nWarning:   पूरा install नहीं हुआ, बाद में सेटिंग (s) → 6 से दोबारा कोशिश करें।")
    time.sleep(1.5)


NET_WORDS = ("timed out", "timeout", "connection", "unable to download", "temporary failure",
             "unreachable", "name or service", "ssl", "http error 403", "http error 5",
             "incomplete read", "read error", "remote end closed", "got error", "network",
             "resolve", "reset by peer", "broken pipe", "eof")


def net_error(tail):
    t = " ".join(tail).lower()
    return any(w in t for w in NET_WORDS)


def online(url="https://www.youtube.com"):
    host = urlsplit(url).hostname
    if not host:
        return False
    try:
        socket.create_connection((host, urlsplit(url).port or 443), timeout=4).close()
        return True
    except OSError:
        return False


def wait_online(url="https://www.youtube.com", limit=120):
    t0, shown = time.monotonic(), False
    while not online(url):
        if time.monotonic() - t0 > limit:
            return False
        if not shown:
            print(" Source server तक connection नहीं है; इंतज़ार... (Control+C = रोकें)")
            shown = True
        time.sleep(3)
    return True


def save_debug(url, spec, cmd, tail, rescue):
    # Command/URL deliberately omitted: may contain signed URLs and credentials.
    diagnostic(spec, rescue, tail, "recovery")


def build_cmd(url, spec, outroot, aria=True, use_info=True, rescue=0):
    out = esc(outroot)
    cmd = base_args() + ["-q", "--progress", "--no-continue" if rescue else "--continue", "-N", "16",
                         "--retries", "10", "--fragment-retries", "10",
                         "--extractor-retries", "5", "--socket-timeout", "20"]
    if spec.get("items"):
        cmd += ["--yes-playlist", "--playlist-items", spec["items"]]
    else:
        cmd.append("--no-playlist")
    if CFG.get("limit"):
        cmd += ["-r", CFG["limit"]]
    cmd += spec["fargs"]
    if spec["is_audio"]:
        cmd += ["-x", "--audio-format", spec["ext"], "--audio-quality", spec["aq"]]
        if spec["ext"] in ("mp3", "m4a", "flac"):
            cmd.append("--embed-metadata")      # गाने का नाम / कलाकार फ़ाइल में
    else:
        cmd += ["--merge-output-format", spec["ext"], "--remux-video", spec["ext"]]
    if spec.get("playlist_name"):
        name = esc(spec["playlist_name"]) + "/" + esc(spec["item_name"])
    elif spec.get("items"):
        name = "%(playlist_title).60B/%(playlist_index)02d - %(title).180B"
    elif spec.get("name"):
        name = esc(spec["name"])
    else:
        name = "%(title).200B"
    cmd += ["-o", os.path.join(out, name + ".%(ext)s")]
    if spec.get("subs"):
        cmd += ["--write-subs", "--write-auto-subs", "--sub-langs", spec["subs"],
                "--convert-subs", "srt"]
        if spec.get("embed"):
            cmd.append("--embed-subs")
    th = spec.get("thumb")
    if th in ("file", "both"):
        cmd += ["--write-thumbnail", "--convert-thumbnails", "jpg"]
    if th in ("embed", "both"):
        cmd.append("--embed-thumbnail")
    if spec.get("chapters"):
        cmd += ["--split-chapters", "-o", "chapter:" + os.path.join(
            out, "%(title).60B", "%(section_number)02d - %(section_title).60B.%(ext)s")]
    if spec.get("sponsor"):
        cmd += ["--sponsorblock-remove", "sponsor,selfpromo"]
    if spec.get("trim"):
        s, e = spec["trim"]
        cmd += ["--download-sections", f"*{s:g}-{e:g}",
                "--downloader-args", "ffmpeg:-loglevel error -hide_banner -stats"]
        if spec.get("precise"):
            cmd.append("--force-keyframes-at-cuts")
    if (aria and spec.get("aria_allowed") and have_aria() and not CFG.get("aria_off")
            and not spec.get("trim")):
        cmd += ["--downloader", "aria2c", "--downloader-args",
                "aria2c:-x16 -s16 -k1M --min-split-size=1M --file-allocation=none "
                "--human-readable=false --console-log-level=warn --summary-interval=0 "
                "--async-dns=false --disable-ipv6=true "
                "--timeout=20 --connect-timeout=8 --max-tries=5 --retry-wait=2"]
    if rescue >= 2 and not spec.get("trim"):
        cmd += ["--downloader", "ffmpeg", "--downloader-args",
                "ffmpeg:-loglevel error -hide_banner -stats"]
    cmd += ["--newline", "--progress-template", TPL]
    info_file = spec.get("info_file")
    if use_info and info_file and os.path.exists(info_file):
        cmd += ["--load-info-json", info_file]     # दोबारा जानकारी निकालने का इंतज़ार नहीं
    else:
        cmd.append(url)
    return cmd


def _execute(urls, spec, meta):
    t_all = time.monotonic()
    saved, outcomes = [], []
    print("\nDownload शुरू... (Space: pause/resume · Control+C: विकल्प)\n")
    for index, url in enumerate(urls, 1):
        item = dict(spec)
        item.pop("items", None)
        info = spec.get("source_info") if len(urls) == 1 and not spec.get("batch") else None
        title, vid, tmp = meta.get("title"), meta.get("id"), None
        warnings, tail, moved, rc = [], [], [], 1
        try:
            if not info:
                print(f"\n── Video {index}/{len(urls)}: जानकारी जाँच रहे हैं ──")
                info, err = fetch(url)
                if not info:
                    raise ValueError(err or "metadata नहीं मिला")
            if info.get("is_live"):
                raise ValueError("Live video अभी supported नहीं है")
            title, vid = info.get("title") or title, info.get("id") or vid
            if item.get("sponsor") and not source_is_youtube(info):
                item["sponsor"] = False
                warnings.append("इस source पर SponsorBlock लागू नहीं है; skip किया")
            if not item["is_audio"] and audio_only(info):
                raise ValueError("यह audio-only source है; audio format चुनें")
            if spec.get("batch"):
                item = choose_batch(info, item)
            if item["is_audio"]:
                selected_audio = choose_audio(info, item["ext"])
                if selected_audio:
                    item["fargs"] = ["-f", selected_audio["format_id"]]
                    print("    Audio: " + audio_description(selected_audio))
            if item.get("playlist_name"):
                number = spec["playlist_indices"][index-1]
                item["item_name"] = f"{number:02d} - {clean_name(title, 150) or vid or 'video'}"
            identity = {k: item.get(k) for k in ("ext", "is_audio", "fargs", "aq", "name",
                        "subs", "embed", "thumb", "chapters", "sponsor", "trim", "precise",
                        "convert", "playlist_name", "item_name")}
            job = hashlib.sha256(json.dumps([url, identity], sort_keys=True).encode()).hexdigest()[:20]
            tmp = os.path.join(outdir(), ".ytdl_tmp", job)
            os.makedirs(tmp, exist_ok=True)
            info_path = os.path.join(tmp, ".source.json")
            if not save_json(info_path, strip_range(info)):
                raise OSError("metadata cache save failed")
            item["info_file"] = info_path
            item["aria_allowed"] = aria_safe(info, item)
            fa = item["fargs"]
            parts = fa[1].count("+") + 1 if len(fa) == 2 and fa[0] == "-f" else None
            seg = item["trim"][1] - item["trim"][0] if item.get("trim") else None
            aria_ok, rescue = True, 0
            for attempt in range(6):
                cmd = build_cmd(url, item, tmp, aria=aria_ok and rescue < 2,
                                use_info=attempt == 0, rescue=rescue)
                downloader = cmd[cmd.index("--downloader") + 1] if "--downloader" in cmd else None
                used_aria = downloader == "aria2c"
                rc, tail = run_with_bar(cmd, parts, seg,
                                       item.get("est", 0) / (len(urls) if not spec.get("batch") else 1),
                                       start_secs=ARIA_START_SECS if used_aria else START_SECS,
                                       processing_secs=max(600, (info.get("duration") or 0) * 5),
                                       media_duration=info.get("duration"), total_exact=bool(item.get("est_exact")))
                diagnostic(item, rc, tail)
                if rc == 0:
                    for ln in tail:
                        if ln.lstrip().startswith("WARNING:"):
                            print("   Warning: ", redact(ln)[:180])
                    break
                for ln in tail[-3:]:
                    print("   ℹ", redact(ln)[:180])
                bad_range = any(w in " ".join(tail).lower() for w in
                                ("conflicting range", "range not satisfiable", "http error 416"))
                if bad_range:
                    rescue = min(2, rescue + 1)
                    clean_partials(tmp)
                    print(" Range error: source resume support नहीं कर रहा; इस item का partial restart होगा।")
                if used_aria:
                    aria_ok = False
                    print(" aria2 से दिक्कत: native downloader से resume की कोशिश।")
                if attempt == 5 or not (used_aria or bad_range or rc == RC_STALL or net_error(tail)):
                    break
                if rc == RC_STALL:
                    print(" Progress अटकी: इसी quality के साथ retry...")
                if not wait_online(url):
                    break
                time.sleep(min(attempt + 1, 5))
            expect_audio = bool(info.get("acodec") not in (None, "none") or
                                any(f.get("acodec") not in (None, "none")
                                    for f in (info.get("formats") or [])))
            expected = seg or info.get("duration") or 0
            if item.get("sponsor"):
                expected = 0  # Removal intentionally changes duration.
            candidates = [p for p in list_files(tmp) if p.lower().endswith(MEDIA_EXT)
                          and not junk_fail(os.path.basename(p))
                          and not re.search(r"\.(fix|conv|encode)\.[^.]+$", p)]
            valid = []
            for path in candidates:
                chapter = item.get("chapters") and os.path.dirname(path) != tmp
                ok, reason = validate_media(path, item["is_audio"], 0 if chapter else expected,
                                            expect_audio and not item["is_audio"])
                if not ok:
                    warnings.append(f"{os.path.basename(path)}: {reason}")
                    # Preserve bad output for diagnosis but don't let yt-dlp skip it
                    # as an already-complete file on the next retry.
                    os.replace(path, path + ".invalid-" + str(time.time_ns()))
                    continue
                if path.endswith(".mp4") and not item["is_audio"]:
                    if not ensure_playable(path):
                        warnings.append("MP4 preparation failed; original retained")
                    if item.get("convert") and not convert_h264(path):
                        warnings.append("H.264 conversion skipped/failed; original retained")
                if item["ext"] == "m4a" and item.get("aq") not in (None, "0"):
                    if not encode_m4a(path, item["aq"]):
                        warnings.append("Requested audio bitrate conversion failed; original retained")
                decoded, decode_error = decode_check(path)
                if not decoded:
                    warnings.append("decode verification failed: " + redact(decode_error))
                    os.replace(path, path + ".invalid-" + str(time.time_ns()))
                    continue
                signal = audio_signal(path)
                if signal == "silent":
                    warnings.append("Audio track मौजूद है, लेकिन पूरी track लगभग silent है; source/दूसरी track जाँचें।")
                elif signal == "missing":
                    print("   ℹ इस source में audio उपलब्ध नहीं है; file video-only है।")
                elif signal == "unknown":
                    warnings.append("Audio track मिली, पर sound-level verification पूरी नहीं हो सकी।")
                valid.append(path)
            if valid:
                warnings.extend(verify_extras(valid, item, tmp))
                allowed = set(valid)
                for path in list_files(tmp):
                    # Only sidecars belonging to a verified media file are exported.
                    if path in allowed or path.lower().endswith(MEDIA_EXT) or junk_fail(os.path.basename(path)):
                        continue
                    if path.endswith((".srt", ".vtt", ".ass", ".jpg", ".png", ".webp")):
                        if any(os.path.dirname(path) == os.path.dirname(v) and
                               os.path.basename(path).startswith(os.path.splitext(os.path.basename(v))[0] + ".")
                               for v in valid):
                            allowed.add(path)
                moved = move_out(tmp, sorted(allowed))
                saved.extend(moved)
            media = [p for p in moved if p.lower().endswith(MEDIA_EXT)]
            status = "complete" if media and rc == 0 and not warnings else "partial" if media else "failed"
            if not media:
                warnings.append("कोई verified media file नहीं बनी; thumbnail को video नहीं माना गया")
            for message in warnings:
                print("   Warning: ", message)
            actual = "; ".join(actual_quality(p) for p in media)
            outcomes.append(status)
            history_add({"date": datetime.now().strftime("%d-%m-%Y %H:%M"), "title": title,
                         "id": vid, "tag": item["tag"], "status": status, "actual": actual,
                         "size": sum(os.path.getsize(p) for p in moved)})
            diagnostic(item, rc, tail + warnings, "verified-" + status)

            if status == "complete":
                os.remove(info_path)
                if not list_files(tmp):
                    os.rmdir(tmp)
            else:
                print(f"    Retry data सुरक्षित है: {tmp}")
        except KeyboardInterrupt:
            print("\nDownload रद्द।")
            history_add({"date": datetime.now().strftime("%d-%m-%Y %H:%M"), "title": title,
                         "id": vid, "tag": item["tag"], "status": "cancelled", "size": 0})
            return "stop"
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            print("   Error: ", redact(str(e)))
            diagnostic(item, 1, tail + [str(e)], "failed")
            outcomes.append("failed")
            history_add({"date": datetime.now().strftime("%d-%m-%Y %H:%M"), "title": title,
                         "id": vid, "tag": item["tag"], "status": "failed", "size": 0})
    complete = outcomes.count("complete")
    if complete == len(urls):
        folders = list(dict.fromkeys(os.path.dirname(os.path.abspath(path)) for path in saved))
        for folder in folders:
            print(f"\nSaved in: {folder}")
        notify(f"Download पूरा: {complete} video/audio")
    elif saved:
        print(f"Warning:  आंशिक परिणाम: {complete}/{len(urls)} पूरे; बाकी history में देखें।")
        notify("Download आंशिक रहा; history देखें")
    else:
        print("Error:  कोई verified media download नहीं हुआ।")
        notify("Download असफल रहा")
    if saved:
        open_folder(outdir())
    return None if complete == len(urls) else "stop"


# ===== Transcript (बोले गए शब्द, समय के साथ) =====
LANGNAMES = {"hi": "हिंदी", "en": "English", "ur": "उर्दू", "pa": "पंजाबी", "bn": "बांग्ला",
             "ta": "तमिल", "te": "तेलुगु", "mr": "मराठी", "gu": "गुजराती", "kn": "कन्नड़",
             "ml": "मलयालम", "ne": "नेपाली", "ar": "अरबी", "es": "Spanish", "fr": "French",
             "de": "German", "ja": "Japanese", "ko": "Korean", "pt": "Portuguese",
             "ru": "Russian", "id": "Indonesian", "tr": "Turkish", "zh": "Chinese"}


def lang_name(code):
    base = code.replace("-orig", "").split("-")[0]
    return f"{LANGNAMES.get(base, code)} ({code})"


def tr_tracks(info):
    """कौन सी captions मौजूद हैं: असली (इंसान की लिखी) और अपने-आप बनी।"""
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat" and v}
    auto = {k: v for k, v in (info.get("automatic_captions") or {}).items() if v}
    lang = info.get("language")
    orig = None
    if lang and lang in manual:
        orig = ("manual", lang)
    elif [k for k in auto if k.endswith("-orig")]:
        orig = ("auto", [k for k in auto if k.endswith("-orig")][0])
    elif lang and lang in auto:
        orig = ("auto", lang)
    elif len(manual) == 1:
        orig = ("manual", list(manual)[0])
    return manual, auto, orig


def srt_time(t):
    h, m, rest = t.split(":")
    s, ms = rest.replace(".", ",").split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")[:3]) / 1000


def srt_fmt(sec):
    sec = max(0, sec)
    ms = int(round((sec - int(sec)) * 1000))
    if ms == 1000:
        sec, ms = int(sec) + 1, 0
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d},{ms:03d}"


def srt_parse(text):
    cues = []
    for blk in re.split(r"\n\s*\n", text.replace("\r", "").strip()):
        ls = [l for l in blk.split("\n") if l.strip()]
        for i, l in enumerate(ls):
            m = re.match(r"(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)", l)
            if m:
                body = [re.sub(r"<[^>]+>", "", x).strip() for x in ls[i + 1:]]
                body = [x for x in body if x]
                if body:
                    cues.append([srt_time(m[1]), srt_time(m[2]), body])
                break
    return cues


def cues_clean(cues, auto):
    out = []
    for s, e, body in cues:
        if out and out[-1] == [s, e, body]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e, body])
    return out


def cues_clip(cues, rng):
    if not rng:
        return cues
    a, b = rng
    return [[max(s, a), min(e, b), body] for s, e, body in cues if e > a and s < b]


def write_transcript(cues, base):
    with open(base + ".srt", "w", encoding="utf-8") as f:
        for i, (s, e, body) in enumerate(cues, 1):
            f.write(f"{i}\n{srt_fmt(s)} --> {srt_fmt(e)}\n" + "\n".join(body) + "\n\n")
    with open(base + ".txt", "w", encoding="utf-8") as f:
        for s, e, body in cues:
            t = int(s)
            f.write(f"[{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}]  " + " ".join(body) + "\n")
    return [base + ".srt", base + ".txt"]



def transcript_flow(url, info, dur):
    try:
        with folder_lock(outdir()):
            try:
                return _transcript_flow(url, info, dur)
            finally:
                shutil.rmtree(os.path.join(outdir(), ".ytdl_tr"), ignore_errors=True)
    except FolderBusy:
        print("Warning:  इस folder में दूसरा काम चल रहा है। बाद में कोशिश करें।")
        return "back"

def _transcript_flow(url, info, dur):
    manual, auto, orig = tr_tracks(info)
    print("\n────────   Transcript (बोले गए शब्द, समय के साथ)  ────────\n")
    if not manual and not auto:
        print("Error:  इस source पर captions / subtitles उपलब्ध नहीं हैं।")
        print("   (आवाज़ से अपने-आप लिखना इस tool में अभी नहीं है।)")
        return "back"
    try:
        print("  भाषा कौन सी चाहिए?")
        if orig:
            kind = "असली captions " if orig[0] == "manual" else \
                "अपने-आप बनी (source की automatic captions; 100% सही नहीं होती) Warning: "
            print(f"   1) असली बोली गई भाषा: {lang_name(orig[1])} — {kind}")
        else:
            print("   1) असली भाषा पहचानी नहीं जा सकी (नीचे 2 या 3 चुनें)")
        print("   2) सारी इंसान की लिखी (असली) भाषाएँ"
              + (f" — {len(manual)} मिलीं" if manual else " — कोई नहीं मिली"))
        print("   3) भाषा खुद चुनें (code लिखकर)")
        c = ask("नंबर चुनें [1]  (b = वापस): ", ["1", "2", "3"], "1", back=True)
        names = []
        if c == "1":
            if not orig:
                print("Warning:   असली भाषा नहीं मिली, 2 या 3 चुनें।")
                return "back"
            names = [orig[1]]
        elif c == "2":
            names = list(manual)
            if not names:
                print("Warning:   इस वीडियो में इंसान की लिखी कोई captions नहीं हैं।")
                return "back"
        else:
            allk = sorted(set(manual) | set(auto))
            print("\n  मौजूद भाषाएँ ( = असली, बाकी अपने-आप/अनुवाद):")
            show = [(" " if k in manual else "   ") + k for k in allk]
            for i in range(0, len(show), 6):
                print("   " + "   ".join(f"{x:<12}" for x in show[i:i + 6]))
            while True:
                s = input("\n  भाषा code लिखें (कई हों तो , से अलग): ").strip()
                if s == "b":
                    return "back"
                names = [x.strip() for x in s.split(",") if x.strip() in allk]
                if names:
                    break
                print("  Warning:   इनमें से कोई code सूची में नहीं है।")
        print("\n  कौन सा हिस्सा चाहिए?")
        print("   1) पूरा वीडियो")
        print("   2)   कुछ हिस्सा (समय चुनकर)")
        part = ask("नंबर चुनें [1]  (b = वापस): ", ["1", "2"], "1", back=True)
        rng = None
        if part == "2":
            r = trim_ui(dur, url, None, 0)
            if r == "back" or not r:
                return "back"
            rng = (float(r[0]), float(r[1]))
    except Back:
        return "back"
    print("\n────────   सारांश  ────────\n")
    print("  भाषा   : " + ", ".join(lang_name(n) for n in names))
    print("  हिस्सा : " + (f"{hms(rng[0])} से {hms(rng[1])}" if rng else "पूरा वीडियो"))
    print("  फ़ाइल  : .srt (समय के साथ) और .txt (पढ़ने के लिए)")
    print("  Folder : " + CFG["out"])
    try:
        go = ask("\nEnter = शुरू    b = वापस    q = रद्द: ", ["", "b", "q"], "")
    except Back:
        return "back"
    if go == "b":
        return "back"
    if go == "q":
        return "cancel"
    tmp = os.path.join(outdir(), ".ytdl_tr")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    print("\n⏳ Transcript निकाल रहा हूँ...")
    cmd = base_args() + ["-q", "--no-playlist", "--skip-download",
                         "--write-subs", "--write-auto-subs", "--sub-langs", ",".join(names),
                         "--convert-subs", "srt", "-o", os.path.join(tmp, "tr.%(ext)s"), url]
    r = capture(cmd, timeout=300)
    if r.returncode:
        print("Warning:  कुछ captions नहीं मिल पाए; उपलब्ध files जाँच रहे हैं।")
        diagnostic({}, r.returncode, r.stderr.splitlines(), "transcript")
    files = [p for p in list_files(tmp) if p.endswith(".srt")]
    if not files:
        print("Error:  Transcript नहीं मिला।")
        for ln in (r.stderr or r.stdout or "").strip().splitlines()[-2:]:
            print("   ℹ ", ln[:120])
        shutil.rmtree(tmp, ignore_errors=True)
        return "back"
    title = clean_name(info.get("title") or "transcript") or "transcript"
    made = []
    for p in sorted(files):
        code = os.path.basename(p)[len("tr."):-len(".srt")] or "xx"
        is_auto = code in auto and code not in manual
        with open(p, encoding="utf-8", errors="replace") as fh:
            cues = cues_clean(srt_parse(fh.read()), is_auto)
        cues = cues_clip(cues, rng)
        if not cues:
            print(f"Warning:   {lang_name(code)}: इस हिस्से में कोई बोले गए शब्द नहीं मिले।")
            continue
        suffix = "" if not rng else f" [{hms(rng[0]).replace(':', '-')} to {hms(rng[1]).replace(':', '-')}]"
        folder = outdir()
        stem = unique_stem(folder, clean_name(f"{title}.{code}{suffix}", 200), [".srt", ".txt"])
        made += write_transcript(cues, os.path.join(folder, stem))
    shutil.rmtree(tmp, ignore_errors=True)
    if not made:
        return "back"
    received = {os.path.basename(p)[len("tr."):-len(".srt")] for p in files}
    missing = set(names) - received
    if missing:
        print("Warning:  ये captions नहीं मिले: " + ", ".join(sorted(missing)))
    print("\n Transcript files तैयार!" if not missing and r.returncode == 0
          else "\nWarning:  उपलब्ध transcript files तैयार; कुछ हिस्से असफल रहे।")
    for m in made:
        print(f"    {os.path.basename(m)}")
    print(f"    {outdir()}")
    if rng:
        print("   ℹ  समय वही हैं जो असली वीडियो में हैं (ताकि सही पल ढूँढ सकें)।")
    if any((n in auto and n not in manual) for n in names):
        print("   Warning:   अपने-आप बनी captions में कुछ शब्द गलत हो सकते हैं।")
    history_add({"date": datetime.now().strftime("%d-%m-%Y %H:%M"), "title": info.get("title"),
                 "id": info.get("id"), "tag": "Transcript", "status": "complete" if r.returncode == 0 and not missing else "partial",
                 "size": sum(os.path.getsize(p) for p in made)})
    open_folder(outdir())
    return "ok"


def single_flow(url, info):
    if info.get("is_live"):
        print("Warning:   यह Live वीडियो है, अभी डाउनलोड नहीं हो सकता।")
        return
    dur = info.get("duration") or 0
    print(f"\n Title   : {info.get('title')}")
    print(f" Channel : {info.get('uploader') or '?'}")
    print(f"⏱  Duration: {hms(dur) if dur else 'अभी उपलब्ध नहीं'}")
    fresh = bool(history_find(info.get("id")))
    while True:
        got = pick_steps(info, dur, 1, True, url, fresh)
        if not got:
            return
        ext, is_audio, q, st = got
        if ext == "transcript":
            res = transcript_flow(url, info, dur)
            if res == "back":
                fresh = True
                continue
            if res == "cancel":
                return "new"
        else:
            spec, _ = make_spec(ext, is_audio, q, st, dur)
            trim = spec.get("trim")
            spec["est"] = q["size"] * ((trim[1] - trim[0]) / dur) if (trim and dur) else q["size"]
            spec["aria_allowed"] = aria_safe(info, q)
            spec["source_info"] = info
            info = strip_range(info)
            fa_ = q.get("fargs") or []
            ids_ = fa_[1].split("+") if len(fa_) == 2 and fa_[0] == "-f" else []
            spec["dbg"] = [{k: f.get(k) for k in ("format_id", "protocol", "ext", "filesize",
                            "filesize_approx", "height", "format_note", "vcodec", "acodec")}
                           for f in (info.get("formats") or []) if f.get("format_id") in ids_]
            save_formats(info, spec)
            res = execute([url], spec, {"title": info.get("title"), "id": info.get("id")})
        if res != "stop":
            print("\n────────  Next action  ────────")
            print("  Enter =  नया वीडियो")
            print("  f     =  इसी वीडियो को दूसरे Format / Quality में (जैसे ऑडियो के बाद वीडियो)")
            print("  q     =  टूल बंद करें")
            c = ask("चुनें: ", ["", "f", "b", "q"], "")
            if c == "q":
                return "quit"
            if c in ("f", "b"):
                fresh = True
                continue
            return "new"
        nxt = after_stop()
        if nxt != "again":
            return nxt
        fresh = True


def ask_items(n):
    print("  1) सभी वीडियो   2) कुछ चुने हुए (जैसे 1-5 या 1,3,7)")
    if ask("नंबर चुनें [1]: ", ["1", "2"], "1") == "1":
        return f"1-{n}", n
    while True:
        s = input("   नंबर लिखें: ").strip().replace(" ", "")
        try:
            indices = range_indices(s, n)
            return ",".join(map(str, indices)), len(indices)
        except ValueError:
            print(f"Warning:  1 से {n} के अंदर सही नंबर लिखें।")


def batch_finish(urls, spec, q, extra, count, title):
    spec["est"] = q["size"]
    return execute(urls, spec, {"title": title, "id": None})


def playlist_flow(url, info, entries):
    print("\n कौन से वीडियो चाहिए?")
    items, cnt = ask_items(len(entries))
    selected = [(i, entries[i-1]) for i in range_indices(items, len(entries))]
    resolved = []
    for i, entry in selected:
        try:
            resolved.append((i, entry_url(entry, url, info.get("extractor_key") or info.get("extractor") or "")))
        except ValueError as e:
            print(f"Warning:  Item {i}: {e}")
    if not resolved:
        return
    cnt = len(resolved)
    finfo = None
    for _, resolved_url in resolved:
        finfo, err = fetch(resolved_url)
        if finfo:
            break
        print("Warning:  selected video की जानकारी नहीं मिली; अगले selected video से menu बना रहे हैं।")
    if not finfo:
        print("Error:  चुने हुए किसी video की जानकारी नहीं मिली।")
        return
    dur = finfo.get("duration") or 0
    while True:
        got = pick_steps(finfo, dur, cnt, False)
        if not got:
            return
        ext, is_audio, q, st = got
        spec, extra = make_spec(ext, is_audio, q, st, dur, cnt)
        spec["batch"] = True
        spec["playlist_name"] = clean_name(info.get("title") or "Playlist", 100)
        spec["playlist_indices"] = [i for i, _ in resolved]
        urls = [u for _, u in resolved]
        if batch_finish(urls, spec, q, extra, cnt, info.get("title") or "Playlist") != "stop":
            return
        nxt = after_stop()
        if nxt != "again":
            return nxt


def multi_flow(urls):
    print(f"\n {len(urls)} लिंक मिले। पहले वीडियो की जानकारी निकाल रहा हूँ...")
    finfo = None
    for candidate in urls:
        finfo, err = fetch(candidate)
        if finfo:
            break
    if not finfo:
        print("Error:  किसी link की जानकारी नहीं मिली।", err)
        return
    dur = finfo.get("duration") or 0
    while True:
        got = pick_steps(finfo, dur, len(urls), False)
        if not got:
            return
        ext, is_audio, q, st = got
        spec, extra = make_spec(ext, is_audio, q, st, dur, len(urls))
        if batch_finish(urls, spec, q, extra, len(urls), f"{len(urls)} वीडियो") != "stop":
            return
        nxt = after_stop()
        if nxt != "again":
            return nxt


class Back(Exception):
    pass


def ask_subs(info, ext):
    print("\n────────   Subtitles  ────────")
    if info:
        manual = sorted(info.get("subtitles") or {})
        auto = bool(info.get("automatic_captions"))
        if manual:
            print("  उपलब्ध:", ", ".join(manual[:12]))
        else:
            print("  कोई तैयार subtitle नहीं" + (" (auto-generated मिल सकते हैं)" if auto else ""))
    print("  1) नहीं   2) हिंदी   3) English   4) हिंदी + English   5) कोई और भाषा")
    c = ask("नंबर चुनें [1]: ", ["1", "2", "3", "4", "5"], "1")
    subs = {"1": None, "2": "hi", "3": "en", "4": "hi,en"}.get(c)
    if c == "5":
        subs = re.sub(r"\s+", "", input("   भाषा के कोड लिखें (जैसे ta,te): ")) or None
    embed = False
    if subs and ext in ("mp4", "mkv"):
        embed = ask("   1) अलग .srt फ़ाइल   2) वीडियो के अंदर जोड़ें [1]: ", ["1", "2"], "1") == "2"
    return subs, embed


def ask_thumb(ext):
    print("\n────────    Thumbnail  ────────")
    can = ext in EMBED_THUMB
    print("  1) नहीं   2) अलग फ़ोटो (jpg)" + ("   3) अंदर जोड़ें (cover)   4) दोनों" if can else ""))
    c = ask("नंबर चुनें [1]: ", ["1", "2"] + (["3", "4"] if can else []), "1")
    return {"1": None, "2": "file", "3": "embed", "4": "both"}[c]


THUMB_TXT = {"file": "अलग फ़ोटो", "embed": "अंदर cover", "both": "अलग + अंदर"}


def opt_label(k, st, chs):
    if k == "subs":
        t = " Subtitles"
        if st["subs"]:
            t += f"    {st['subs']}" + (" (वीडियो के अंदर)" if st["embed"] else "")
        return t
    if k == "thumb":
        t = "  Thumbnail"
        if st["thumb"]:
            t += f"    {THUMB_TXT[st['thumb']]}"
        return t
    if k == "trim":
        t = "  Trim / Cut video"
        if st["trim"]:
            t += f"    {hms(st['trim'][0])} से {hms(st['trim'][1])}"
        return t
    if k == "name":
        t = "  File name"
        if st["name"]:
            t += f"    {st['name']}"
        return t
    if k == "sponsor":
        return " Sponsor / प्रमोशन वाले हिस्से हटाएँ" + ("   " if st["sponsor"] else "")
    if k == "info":
        return "Video details"
    t = f" Chapters अलग फ़ाइलों में ({chs})"
    if st["chapters"]:
        t += "   "
    return t


def options_menu(info, ext, is_audio, dur, single, url, total, st, chs):
    ctx = {"info": info, "ext": ext, "dur": dur, "url": url, "total": total}
    items = [] if is_audio or (single and not (info.get("subtitles") or info.get("automatic_captions"))) else ["subs"]
    if not single or info.get("thumbnail") or info.get("thumbnails"):
        items.append("thumb")
    if single:
        items += ["trim", "name"]
    if source_is_youtube(info):
        items.append("sponsor")
    if single and chs > 1:
        items.append("chapters")
    if single:
        items.append("info")
    print("\n" + heading("  Options") + "\n")
    for i, k in enumerate(items, 1):
        print(f"  {i:>2})  {opt_label(k, st, chs).strip()}\n")
    print("   b)  Back\n")
    while True:
        raw = input("\n  Options: 1,3\n\n  Enter = Continue: ").strip().lower()
        if raw == "":
            return
        if raw == "b":
            raise Back
        toks = [t for t in re.split(r"[,\s]+", raw) if t]
        if any(not (t.isdigit() and 1 <= int(t) <= len(items)) for t in toks):
            print(f"Warning:   गलत चुनाव। 1 से {len(items)} तक के नंबर लिखें, या Enter दबाएं।")
            continue
        for t in toks:
            edit_item(items[int(t) - 1], st, ctx)


def pick_steps(info, dur, count, single, url=None, fresh=False):
    st = new_state()
    chs = len((info or {}).get("chapters") or []) if single else 0
    ext = is_audio = q = None
    step, after, editing = "format", "menu", False
    while True:
        try:
            if step == "format":
                ext, is_audio = choose_format(info)
                if ext == "transcript":
                    if not single:
                        print("Warning:   Transcript अभी सिर्फ़ एक वीडियो के लिए है। दूसरा format चुनें।")
                        continue
                    return "transcript", False, {"size": 0}, st
                sanitize_state(st, ext, is_audio)
                step = "quality"
            if step == "quality":
                q = choose_quality(info, ext, is_audio, dur, count, single)
                if not q:
                    print("Error:  इस output के लिए usable video format नहीं मिला।")
                    if not (info.get("formats") or []):
                        print("   Source की पूरी media जानकारी नहीं मिली; नया link दें या website का direct download link इस्तेमाल करें।")
                        return None
                    if ask("दूसरा format चुनें? y = हाँ, Enter = link पर वापस: ", ["y", ""], "") != "y":
                        return None
                    step = "format"
                    continue
                step, after, editing = after, "menu", False
            if step == "menu":
                options_menu(info, ext, is_audio, dur, single, url, q["size"], st, chs)
                step = "summary"
            if step == "summary":
                r = summary_screen(info, ext, is_audio, q, st, dur, count, single, url, chs)
                if r == "go":
                    return ext, is_audio, q, st
                if r == "cancel":
                    return None
                if r == "back":
                    step, after = "quality", "menu"
                else:
                    step, after, editing = r, "summary", True
        except Back:
            if editing:
                step, after, editing = "summary", "menu", False
            elif step == "format":
                return None
            else:
                step = "format" if step == "quality" else "quality"


def make_spec(ext, is_audio, q, st, dur, count=1):
    spec = {"ext": ext, "is_audio": is_audio, "fargs": q["fargs"], "aq": q["aq"],
            "est_exact": bool(q.get("exact")), "hdr": bool(q.get("hdr")),
            "batch": count > 1, "selection": {k: q.get(k) for k in ("h", "fps", "codec", "hdr")},
            "tag": q["tag"], "subs": st["subs"], "embed": st["embed"], "thumb": st["thumb"]}
    extra = []
    if st["trim"]:
        spec["trim"] = st["trim"]
        spec["precise"] = st["precise"]
    if st["chapters"] and not st["trim"]:
        spec["chapters"] = True
    if st["name"]:
        spec["name"] = st["name"]
    if st["sponsor"] and not st["trim"]:
        spec["sponsor"] = True
    if st["folder"]:
        spec["folder"] = st["folder"]
    if is_audio:
        spec["last"] = {"ext": ext, "is_audio": True, "aq": q["aq"]}
    else:
        spec["last"] = {"ext": ext, "is_audio": False, "h": q["h"], "fps": q.get("fps"),
                        "codec": q["codec"], "hdr": bool(q.get("hdr"))}
    return spec, extra


MEDIA_EXT = (".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".wav", ".opus", ".ogg", ".flac")


def list_files(root):
    res = []
    for r, _, files in os.walk(root):
        for f in files:
            res.append(os.path.join(r, f))
    return sorted(res)


def unique_stem(dest_dir, stem, exts):
    stem = clean_name(stem, 200) or "download"
    cand, n = stem, 1
    while any(os.path.exists(os.path.join(dest_dir, cand + e)) for e in exts):
        n += 1
        cand = f"{stem} ({n})"
    return cand


def move_out(tmp, files=None):
    root = outdir()
    files = list_files(tmp) if files is None else files
    done, moved = set(), []

    def put(src, rel_dir, name):
        d = os.path.join(root, rel_dir)
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, name)
        os.replace(src, dst)
        moved.append(dst)

    for m in files:
        if m in done or not m.lower().endswith(MEDIA_EXT):
            continue
        rel_dir = os.path.relpath(os.path.dirname(m), tmp)
        rel_dir = "" if rel_dir == "." else rel_dir
        base = os.path.basename(m)
        stem = os.path.splitext(base)[0]
        group = [m] + [c for c in files if c != m and c not in done
                       and os.path.dirname(c) == os.path.dirname(m)
                       and os.path.basename(c).startswith(stem + ".")
                       and not os.path.basename(c).lower().endswith(MEDIA_EXT)]
        exts = [os.path.basename(g)[len(stem):] for g in group]
        new_stem = unique_stem(os.path.join(root, rel_dir), stem, exts)
        for g in group:
            put(g, rel_dir, new_stem + os.path.basename(g)[len(stem):])
            done.add(g)
    for f in files:
        if f in done:
            continue
        rel_dir = os.path.relpath(os.path.dirname(f), tmp)
        rel_dir = "" if rel_dir == "." else rel_dir
        stem, ext = os.path.splitext(os.path.basename(f))
        new_stem = unique_stem(os.path.join(root, rel_dir), stem, [ext])
        put(f, rel_dir, new_stem + ext)
    return sorted(moved)


def parse_range(text, dur):
    t = text.strip().lower().replace(" to ", "-").replace("–", "-")
    if "-" in t:
        a, b = t.split("-", 1)
    else:
        parts = t.split()
        if len(parts) != 2:
            raise ValueError
        a, b = parts
    s = parse_time(a) or 0
    e = parse_time(b)
    if e is None:
        e = dur
    if dur and e > dur:
        e = dur
    if not e or s >= e or (dur and s >= dur):
        raise ValueError
    return (s, e)


def trim_manual(dur, total=0):
    print("\n────────    समय खुद लिखें  ────────\n")
    print("    45-2:10     0:45 से 2:10 तक")
    print("    1:30-       1:30 से अंत तक")
    print("    -45         शुरू के 45 सेकंड\n")
    print("  (समय 90, 1:30 या 0:01:30 — कुछ भी चलेगा)\n")
    while True:
        raw = input("  समय लिखें  (b = वापस):  ").strip()
        if raw.lower() == "b" or raw == "":
            return "back"
        try:
            s, e = parse_range(raw, dur)
        except ValueError:
            print("  Warning:   समझ नहीं आया। ऊपर के उदाहरण जैसा लिखें।")
            continue
        return (s, e)


UNITS = (3600, 60, 1)


def trim_draw(s, e, dur, active, total=0, typing=None, unit=2, width=44):
    a = int(s / dur * width)
    b = max(a + 1, int(e / dur * width))
    bar = "░" * a + "█" * (b - a) + "░" * max(0, width - b)

    def fld(i, v):
        mark = " ◀" if active == i else ""
        if typing is not None and active == i:
            return f"[ {typing}_ ]" + mark
        v = int(v)
        cells = [f"{v // 3600} घं", f"{v % 3600 // 60:02d} मि", f"{v % 60:02d} सै"]
        cells = [(f"\033[7m {c} \033[0m" if (active == i and k == unit) else f" {c} ")
                 for k, c in enumerate(cells)]
        return "[" + ":".join(cells) + "]" + mark

    size = f"   ·   साइज़ लगभग {mb(total * (e - s) / dur)}" if total else ""
    if typing is None:
        hints = ["  ← →      घंटा / मिनट / सेकंड चुनें",
                 "  ↑ ↓      चुने हुए को बढ़ाएँ / घटाएँ",
                 "  Tab      Start / End बदलें",
                 "  0-9      खुद समय लिखें",
                 "  Enter    पक्का करें",
                 "  b        वापस"]
    else:
        hints = ["  समय लिखें:   90   या   1:30   या   0:01:30",
                 "  ",
                 "  Enter    ठीक है",
                 "  Esc      रद्द",
                 "  ",
                 "  "]
    return [
        f"  0:00  {bar}  {hms(dur)}",
        "",
        f"  Start   {fld(0, s)}",
        f"  End     {fld(1, e)}",
        f"  लंबाई   {hms(e - s)}{size}",
        "",
    ] + hints


def read_key(fd):
    if WINDOWS:
        from desktop_support import windows_key
        return windows_key()
    import select
    k = os.read(fd, 1)
    if k == b"\x1b":
        if select.select([fd], [], [], 0.05)[0]:
            k += os.read(fd, 2)
    elif k and k[0] >= 0xC0:
        k += os.read(fd, 1 if k[0] < 0xE0 else 2 if k[0] < 0xF0 else 3)
    return k


def trim_ui(dur, url, cur, total=0):
    if not dur or not sys.stdin.isatty():
        return trim_manual(dur, total)
    if WINDOWS:
        from desktop_support import windows_terminal as termios
        tty = termios
    else:
        import termios
        import tty
    s, e = cur if cur else (0, dur)
    s, e = float(s), float(e)
    active = 0
    typing = None
    unit = 2          # 0 = घंटा, 1 = मिनट, 2 = सेकंड
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    head = "\n────────    Trim (काटना)  ────────\n"
    print(head)
    first = True
    msg = ""
    arrows = {b"\x1b[C": "R", b"\x1bOC": "R", b"\x1b[D": "L", b"\x1bOD": "L",
              b"\x1b[A": "U", b"\x1bOA": "U", b"\x1b[B": "D", b"\x1bOB": "D"}

    def commit():
        nonlocal s, e, typing
        try:
            v = parse_time(typing)
            if v is None:
                raise ValueError
        except ValueError:
            return "Warning:   समय समझ नहीं आया"
        if active == 0:
            if v >= e:
                return "Warning:   Start, End से पहले होना चाहिए"
            s = v
        else:
            v = min(v, dur)
            if v <= s:
                return "Warning:   End, Start के बाद होना चाहिए"
            e = v
        typing = None
        return ""

    try:
        tty.setcbreak(fd)
        while True:
            lines = trim_draw(s, e, dur, active, total, typing, unit) + [("  " + msg) if msg else ""]
            if not first:
                sys.stdout.write(f"\033[{len(lines)}A")
            first = False
            for ln in lines:
                sys.stdout.write("\r\033[K" + ln + "\n")
            sys.stdout.flush()
            msg = ""
            k = read_key(fd)
            ch = k.decode("utf-8", "ignore")
            if k in arrows:
                typing = None
                a = arrows[k]
                if a == "L":
                    unit = max(0, unit - 1)
                elif a == "R":
                    unit = min(2, unit + 1)
                else:
                    step = UNITS[unit] * (1 if a == "U" else -1)
                    if active == 0:
                        s = max(0, min(s + step, e - 1))
                    else:
                        e = min(dur, max(e + step, s + 1))
            elif ch and ch in "0123456789:.":
                typing = (typing or "") + ch
            elif k in (b"\x7f", b"\x08") and typing is not None:
                typing = typing[:-1] or None
            elif k == b"\x1b":
                typing = None
            elif k == b"\t":
                if typing is not None:
                    msg = commit()
                    if msg:
                        continue
                active = 1 - active
            elif k in (b"\r", b"\n"):
                if typing is not None:
                    msg = commit()
                else:
                    return (s, e)
            elif ch in ("b", "B", "ब"):
                return "back"
            elif ch in ("x", "X"):
                return None
            elif ch in ("m", "M", "म"):
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
                r = trim_manual(dur, total)
                tty.setcbreak(fd)
                if isinstance(r, tuple):
                    s, e = float(r[0]), float(r[1])
                typing = None
                print(head)
                first = True
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def heading(text):
    if sys.stdout.isatty() and not os.environ.get("NO_COLOR"):
        return col("38;5;153", text)
    return text


def col(code, text):
    return f"\033[{code}m{text}\033[0m"


def hl(text):
    return str(text)


def after_stop():
    print("\n────────  Next action  ────────")
    print("  1)   वापस — इसी वीडियो की Format / Quality फिर से चुनें")
    print("  2)  नया वीडियो डाउनलोड करें")
    print("  q)  टूल बंद करें")
    c = ask("चुनें [2]: ", ["1", "2", "q"], "2")
    return {"1": "again", "2": "new", "q": "quit"}[c]


def is_hdr(f):
    dr = (f.get("dynamic_range") or "").upper()
    if dr:
        return dr != "SDR"
    return (f.get("vcodec") or "").startswith("vp09.02")


def make_q(o, ext, count, single):
    if o.get("any"):
        fargs = ["-f", "bv*+ba/b"]
    else:
        # Batch execution re-resolves this preference for each source using
        # choose_batch(), with the same normalized dimensions as the menu.
        fargs = ["-f", o["fid"]]
    return {"fargs": fargs, "aq": None, "tag": o["label"],
            "qtxt": o["label"] + (" HDR" if o["hdr"] else ""),
            "size": o["size"] * count, "codec": o["codec"], "h": o["h"], "exact": o.get("exact", False),
            "fps": o["fps"], "hdr": o["hdr"], "audio": o.get("audio"), "nm": NAMES.get(o["h"], "")}


def audio_q(ext, opt, dur, count):
    name, aq, kbps = opt
    # M4A के लिए पहले m4a स्रोत (बिना दोबारा encode, quality नहीं घटती)
    fsel = "bestaudio[ext=m4a]/bestaudio/best" if ext == "m4a" else "bestaudio/best"
    return {"fargs": ["-f", fsel], "aq": aq, "tag": ext.upper(), "exact": False,
            "qtxt": name, "size": kbps * 125 * dur * count, "codec": None, "h": None,
            "fps": None, "hdr": False, "nm": ""}


def apply_last(info, dur, count, single):
    last = CFG.get("last")
    if not last:
        return None
    ext = last.get("ext")
    if ext not in ("mp4", "mkv", "webm", "mp3", "m4a", "wav", "flac"):
        return None
    if not isinstance(last.get("h", 0), (int, float)) or not math.isfinite(last.get("h") or 0):
        return None
    if last.get("is_audio"):
        opts = audio_options(ext)
        pick = next((o for o in opts if o[1] == last.get("aq")), opts[0])
        q = audio_q(ext, pick, dur, count)
        return ext, True, q, f"{ext.upper()}  |  {q['qtxt']}"
    opts = video_options(info, ext, dur, with_hdr=single)
    if not opts:
        return None
    h = last.get("h") or 0
    hdr = bool(last.get("hdr"))
    codec = last.get("codec")
    cand = ([o for o in opts if o["h"] == h and o["hdr"] == hdr and o["codec"] == codec]
            or [o for o in opts if o["h"] == h and o["hdr"] == hdr]
            or [o for o in opts if o["h"] == h]
            or [o for o in opts if o["h"] < h and not o["hdr"]]
            or [opts[-1]])
    fps = last.get("fps")
    o = next((o for o in cand if o["fps"] == fps), cand[0])
    q = make_q(o, ext, count, single)
    txt = f"{ext.upper()}  |  {q['qtxt']}  {o['codec']}"
    if o["h"] != h:
        txt += f"   (पिछली बार {h}p थी, वह इस वीडियो में नहीं है)"
    return ext, False, q, txt


def edit_item(k, st, c):
    if k == "subs":
        st["subs"], st["embed"] = ask_subs(c["info"], c["ext"])
    elif k == "thumb":
        st["thumb"] = ask_thumb(c["ext"])
    elif k == "trim":
        r = trim_ui(c["dur"], c["url"], st["trim"], c["total"])
        if r != "back":
            st["trim"] = r
            if r:
                st["chapters"] = False
                if st["sponsor"]:
                    st["sponsor"] = False
                    print("ℹ  Trim के साथ Sponsor हटाना नहीं चलता, इसलिए वह बंद कर दिया।")
        st["precise"] = bool(st["trim"]) and (st["trim"][1] - st["trim"][0]) <= 180
    elif k == "name":
        st["name"] = clean_name(input("\n  फ़ाइल का नाम (खाली = वीडियो का नाम): ")) or None
    elif k == "chapters":
        if st["trim"]:
            print("Warning:   Trim के साथ chapters अलग नहीं हो सकते।")
        else:
            st["chapters"] = not st["chapters"]
    elif k == "sponsor":
        if st["trim"]:
            print("Warning:   Trim के साथ Sponsor हटाना नहीं चलता। पहले Trim हटाएँ।")
        else:
            st["sponsor"] = not st["sponsor"]
    elif k == "folder":
        p = input("\n इस डाउनलोड का फ़ोल्डर (Finder से फ़ोल्डर यहाँ खींचकर छोड़ें, खाली = डिफ़ॉल्ट): ")
        p = p.strip().strip("'\"").replace("\\ ", " ")
        if not p:
            st["folder"] = None
        else:
            try:
                os.makedirs(os.path.expanduser(p), exist_ok=True)
                st["folder"] = p
            except OSError:
                print("Warning:   फ़ोल्डर नहीं बन पाया।")
    elif k == "info":
        show_info(c["info"], c["dur"])


def free_space(folder):
    p = os.path.abspath(os.path.expanduser(folder or CFG["out"]))
    while p and not os.path.exists(p):
        up = os.path.dirname(p)
        if up == p:
            break
        p = up
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None


def summary_screen(info, ext, is_audio, q, st, dur, count, single, url, chs):
    ctx = {"info": info, "ext": ext, "dur": dur, "url": url, "total": q["size"]}
    while True:
        trim = st["trim"]
        est = q["size"] * ((trim[1] - trim[0]) / dur) if (trim and dur) else q["size"]
        rows = [("format", "Format", ext)]
        qv = q["qtxt"] + (f"  ({q['nm']})" if q.get("nm") else "") + (f"  {q['codec']}" if q.get("codec") not in (None, "-") else "")
        rows.append(("quality", "Quality", qv))
        if single:
            rows.append(("trim", "Trim", f"{hms(trim[0])} से {hms(trim[1])}  (लंबाई {hms(trim[1] - trim[0])})"
                         if trim else "नहीं"))
        if not is_audio and (not single or info.get("subtitles") or info.get("automatic_captions")):
            rows.append(("subs", "Subtitles", (st["subs"] + (" (वीडियो के अंदर)" if st["embed"] else ""))
                         if st["subs"] else "नहीं"))
        if not single or info.get("thumbnail") or info.get("thumbnails"):
            rows.append(("thumb", "Thumbnail", THUMB_TXT[st["thumb"]] if st["thumb"] else "नहीं"))
        if single:
            rows.append(("name", "File name", st["name"] or "वीडियो का नाम"))
        if source_is_youtube(info):
            rows.append(("sponsor", "Sponsor", "हटाएँगे" if st["sponsor"] else "हटाएँगे नहीं"))
        if single and chs > 1:
            rows.append(("chapters", "Chapters", "हर chapter अलग फ़ाइल में" if st["chapters"] else "नहीं"))
        rows.append(("folder", "Folder", st["folder"] or CFG["out"]))
        free = free_space(st["folder"])
        need = est * 2
        low = free is not None and free < need * 1.1
        if low:
            print(f"Warning:  जगह कम है: चाहिए लगभग {mb(need)}, खाली {mb(free)}")
        raw = input("\n"
                    "  Enter = Download    o = Options    b = Back    q = Cancel: ").strip().lower()
        if raw == "":
            if low and ask("Warning:   जगह कम है। फिर भी शुरू करें? (y/n) [n]: ", ["y", "n"], "n") == "n":
                continue
            return "go"
        if raw == "o":
            try:
                options_menu(info, ext, is_audio, dur, single, url, q["size"], st, chs)
            except Back:
                pass
            continue
        if raw == "b":
            return "back"
        if raw == "q":
            return "cancel"
        toks = [t for t in re.split(r"[,\s]+", raw) if t]
        if any(not (t.isdigit() and 1 <= int(t) <= len(rows)) for t in toks):
            print(f"Warning:   गलत चुनाव। 1 से {len(rows)} तक के नंबर लिखें, या Enter दबाएं।")
            continue
        want = None
        for t in toks:
            k = rows[int(t) - 1][0]
            if k == "format":
                want = "format"
            elif k == "quality":
                want = want or "quality"
            else:
                edit_item(k, st, ctx)
        if want:
            return want



def new_state():
    return {"subs": None, "embed": False, "thumb": None, "trim": None, "precise": False,
            "name": None, "chapters": False, "sponsor": False, "folder": None, "_prev": {}}


def sanitize_state(st, ext, is_audio):
    if is_audio:
        st["subs"], st["embed"] = None, False
    if st["embed"] and ext not in ("mp4", "mkv"):
        st["embed"] = False
    if st["thumb"] in ("embed", "both") and ext not in EMBED_THUMB:
        st["thumb"] = "file"


def execute(urls, spec, meta):
    global OUT_OVERRIDE
    OUT_OVERRIDE = spec.get("folder")
    try:
        with folder_lock(outdir()):
            result = _execute(urls, spec, meta)
            if result != "stop" and spec.get("last"):
                CFG["last"] = spec["last"]
                save_json(CFG_PATH, CFG)
            return result
    except FolderBusy:
        print("Warning:  इस folder में दूसरा download/transcript चल रहा है। बाद में कोशिश करें।")
        return "stop"
    finally:
        OUT_OVERRIDE = None


def clip_url():
    try:
        urls = extract_urls(clipboard_text())
        return urls[0] if urls else None
    except (OSError, subprocess.SubprocessError):
        return None


def handle(raw):
    urls = extract_urls(raw)
    if not urls:
        print("Warning:   लिंक नहीं मिला। http से शुरू होने वाला लिंक पेस्ट करें।")
        return
    if len(urls) > 1:
        return multi_flow(urls)
    url = urls[0]
    host = urlsplit(url).hostname or "website"
    if host in ("google.com", "www.google.com", "google.co.in", "www.google.co.in") and urlsplit(url).path == "/search":
        print("Warning:  Google search page की जगह result में मौजूद actual video/audio page का link दें।")
        return
    print(f"\n⏳ {host}: supported extractor / generic media detection...")
    info, err = fetch(url, playlist=True)
    if not info:
        print("Error:  " + source_error(err or ""))
        if err:
            print("   ", err)
        return
    entries = [e for e in (info.get("entries") or []) if e]
    print(" Source: " + str(info.get("extractor_key") or info.get("extractor") or "generic/direct"))
    if info.get("_type") in ("playlist", "multi_video") and entries:
        print(f" Playlist: {info.get('title')} — {len(entries)} वीडियो")
        if source_is_youtube(info) and re.search(r"[?&]v=|youtu\.be/", url):
            if ask("  1) सिर्फ़ यह वीडियो   2) पूरी Playlist [1]: ", ["1", "2"], "1") == "1":
                info, err = fetch(url)
                if not info:
                    print("Error:  जानकारी नहीं मिली।", err)
                    return
                return single_flow(url, info)
        return playlist_flow(url, info, entries)
    if not info.get("formats") or info.get("_type") in ("url", "url_transparent"):
        print("⏳ Page की deferred media जानकारी resolve कर रहे हैं...")
        info, err = fetch(url)
        if not info or not info.get("formats"):
            print("Error:  " + source_error(err or "no media formats"))
            return
    return single_flow(url, info)


def reset_all():
    if ask("   Settings और current/default folder के partials, history, diagnostics reset होंगे।\n"
           "   दूसरे पुराने folders की files नहीं हटेंगी। Enter = Reset, n = रहने दें: ",
           ["", "n"], "") == "n":
        return
    from contextlib import ExitStack
    roots = sorted({os.path.abspath(os.path.expanduser("~/Downloads")),
                    os.path.abspath(os.path.expanduser(CFG["out"]))})
    try:
        with ExitStack() as stack:
            for root in roots:
                stack.enter_context(folder_lock(root))
            for root in roots:
                for name in (".ytdl_tmp", ".ytdl_tr"):
                    shutil.rmtree(os.path.join(root, name), ignore_errors=True)
            # Never remove another process's system-temp info files.
            CFG.clear()
            CFG.update({"out": "~/Downloads", "limit": "", "notify": True, "cookies": "", "convert": False})
            if not save_json(CFG_PATH, CFG):
                raise OSError("settings save failed")
            with open(HIST_PATH + ".lock", "a") as history_lock:
                fcntl.flock(history_lock, fcntl.LOCK_EX)
                if not save_json(HIST_PATH, []):
                    raise OSError("history reset failed")
            for name in ("last_run.json", "last_error.json", "last_formats.json"):
                path = os.path.join(os.path.dirname(CFG_PATH), name)
                if os.path.isfile(path):
                    os.remove(path)
    except FolderBusy:
        print("Warning:  Reset नहीं किया: एक folder में download/transcript चल रहा है।")
        return
    except OSError as e:
        print(f"Warning:  Reset पूरी तरह नहीं हुआ ({type(e).__name__})।")
        return
    print(" Settings, history और बताए गए temporary folders reset हुए।")


def tempfile_dir():
    import tempfile
    return tempfile.gettempdir()


def settings():
    while True:
        os.system("cls" if WINDOWS else "clear")
        print("  सेटिंग\n")
        print(f"  1) डाउनलोड फ़ोल्डर बदलें      (अभी: {CFG['out']})")
        print(f"  2) स्पीड की सीमा              (अभी: {CFG['limit'] or 'असीमित'})")
        print(f"  3) पूरा होने पर आवाज़/Notification (अभी: {'चालू' if CFG['notify'] else 'बंद'})")
        print(f"  4) Login वाले वीडियो के लिए Browser cookies (अभी: {CFG['cookies'] or 'बंद'})")
        print(f"  5) SDR वीडियो को H.264 में बदलना (धीमा; HDR/unknown सुरक्षित रहेगा) "
              f"(अभी: {'चालू' if CFG.get('convert') else 'बंद'})")
        aria_state = "चालू" if (have_aria() and not CFG.get("aria_off")) else "बंद"
        print(f"  6) तेज़ Download सेटअप (aria2)  (अभी: {aria_state})")
        print("  7) Reset: settings, history, current/default folder के partials")
        print("  0) वापस")
        c = ask("\nनंबर चुनें [0]: ", ["0", "1", "2", "3", "4", "5", "6", "7"], "0")
        if c == "0":
            return
        if c == "1":
            p = input("   नया फ़ोल्डर (Finder से फ़ोल्डर यहाँ खींचकर छोड़ सकते हैं): ")
            p = p.strip().strip("'\"").replace("\\ ", " ")
            if p:
                try:
                    os.makedirs(os.path.expanduser(p), exist_ok=True)
                    CFG["out"] = p
                except OSError:
                    print("Warning:   फ़ोल्डर नहीं बन पाया।")
                    time.sleep(1.5)
        elif c == "2":
            v = input("   MB/s में नंबर लिखें (जैसे 5), खाली = असीमित: ").strip()
            try:
                rate = float(v) if v else None
                if rate is not None and (not math.isfinite(rate) or rate <= 0):
                    raise ValueError
                CFG["limit"] = f"{rate:g}M" if rate is not None else ""
            except ValueError:
                print("Warning:   नंबर गलत है।")
                time.sleep(1.5)
        elif c == "3":
            CFG["notify"] = not CFG["notify"]
        elif c == "5":
            CFG["convert"] = not CFG.get("convert")
        elif c == "6":
            CFG.pop("aria_off", None)
            offer_speedup(force=True)
        elif c == "7":
            reset_all()
        elif c == "4":
            print("   1) बंद   2) Chrome   3) Safari   4) Firefox   5) Brave")
            k = ask("   चुनें [1]: ", ["1", "2", "3", "4", "5"], "1")
            CFG["cookies"] = {"1": "", "2": "chrome", "3": "safari",
                              "4": "firefox", "5": "brave"}[k]
            if CFG["cookies"]:
                print("   ℹ  पहली बार Mac पासवर्ड/Keychain की अनुमति माँग सकता है।")
                time.sleep(2)
        save_json(CFG_PATH, CFG)


def show_history():
    h = load_json(HIST_PATH, [])
    print("\n पिछले डाउनलोड (आख़िरी 20):\n")
    if not h:
        print("   अभी कुछ नहीं है।")
    for r in h[-20:][::-1]:
        print(f"  {r.get('date')} [{r.get('tag')}] [{r.get('status', 'old record')}] "
              f"{mb(r.get('size'))} {r.get('title')}  {r.get('actual', '')}")


def pause():
    try:
        input("\nEnter दबाएं...")
    except (KeyboardInterrupt, EOFError):
        pass


def main():
    configure_console()
    need("yt-dlp")
    need("ffmpeg")
    need("ffprobe")
    offer_speedup()
    last_done = None
    while True:
        os.system("cls" if WINDOWS else "clear")
        print("┌──────────────────────┐")
        print(heading("│      Downloader      │"))
        print("└──────────────────────┘")
        print("  s = सेटिंग    h = इतिहास    u = Update    q = बंद\n")
        clip = clip_url()
        if clip and clip == last_done:
            clip = None
        try:
            if clip:
                raw = input("  Link पेस्ट करें: ").strip()
                if raw == "":
                    raw = clip
            else:
                raw = input(" Link पेस्ट करें: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n धन्यवाद!")
            return
        cmd = raw.lower()
        if cmd == "q":
            print(" धन्यवाद!")
            return
        if cmd == "s":
            try:
                settings()
            except (KeyboardInterrupt, EOFError):
                print("\nसेटिंग से वापस आए।")
            continue
        if cmd == "h":
            show_history()
            pause()
            continue
        if cmd == "u":
            print("Update: close Downloader and run setup again to update its dependencies.")
            pause()
            continue
        if not raw:
            continue
        found = extract_urls(raw)
        last_done = found[0] if found else None
        try:
            res = handle(raw)
            if res == "quit":
                print(" धन्यवाद!")
                return
            if res == "new":
                continue
            again = ask("\n Enter = नया वीडियो     q = बंद करें: ", ["q", "n", "y", "b"], "y")
        except (KeyboardInterrupt, EOFError):
            print("\n रोक दिया गया।")
            again = "y"
        if again in ("n", "q"):
            print(" धन्यवाद!")
            return


if __name__ == "__main__":
    main()
