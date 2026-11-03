"""Platform adapters; keep the downloader's menus and media choices unchanged."""
import os
import sys
import subprocess
import queue
import threading
import time

WINDOWS = os.name == 'nt'
if WINDOWS:
    import msvcrt
    class FileLocks:
        LOCK_EX = 1
        LOCK_NB = 2
        @staticmethod
        def flock(file, flags):
            file.seek(0, 2)
            if file.tell() == 0:
                file.write(' ')
                file.flush()
            file.seek(0)
            mode = msvcrt.LK_NBLCK
            while True:
                try:
                    msvcrt.locking(file.fileno(), mode, 1)
                    return
                except OSError as exc:
                    if flags & FileLocks.LOCK_NB:
                        raise BlockingIOError(str(exc)) from exc
                    time.sleep(.1)
    file_locks = FileLocks()
else:
    import fcntl as file_locks


def configure_console():
    if WINDOWS:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, 'reconfigure'):
                stream.reconfigure(encoding='utf-8', errors='replace')
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel.SetConsoleMode(handle, mode.value | 4)


def open_folder(path):
    if WINDOWS:
        os.startfile(os.path.abspath(path))
    else:
        subprocess.run(['open' if sys.platform == 'darwin' else 'xdg-open', path], check=False)


def clipboard_text():
    command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
               '[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); Get-Clipboard -Raw'] if WINDOWS else ['pbpaste']
    try:
        return subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def pause_tree(pid, resume=False):
    if not WINDOWS:
        import signal
        os.killpg(pid, signal.SIGCONT if resume else signal.SIGSTOP)
        return
    import psutil
    try:
        root = psutil.Process(pid)
        # Stop parent first to prevent new children appearing during suspension.
        if not resume:
            root.suspend()
        children = root.children(recursive=True)
        for proc in children:
            try:
                (proc.resume if resume else proc.suspend)()
            except psutil.NoSuchProcess:
                pass
        if resume:
            root.resume()
    except psutil.NoSuchProcess:
        pass


def stop_tree(process):
    if not WINDOWS:
        return False
    import psutil
    try:
        root = psutil.Process(process.pid)
        targets = root.children(recursive=True) + [root]
    except psutil.NoSuchProcess:
        return True
    for proc in targets:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(targets, timeout=5)
    process.wait(timeout=5)
    return True


class WindowsPipe:
    """Windows select() cannot watch subprocess pipes or console descriptors."""
    def __init__(self, fd):
        self.events = queue.Queue()
        def read():
            try:
                while True:
                    data = os.read(fd, 4096)
                    self.events.put(data)
                    if not data:
                        break
            except OSError:
                self.events.put(b'')
        self.thread = threading.Thread(target=read, daemon=True)
        self.thread.start()

    def poll(self, timeout=.2):
        key = None
        if sys.stdin.isatty() and msvcrt.kbhit():
            key = msvcrt.getwch()
            if key == '\x03':
                raise KeyboardInterrupt
        try:
            data = self.events.get(timeout=timeout)
        except queue.Empty:
            data = None
        return data, key


def process_options():
    return {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {'start_new_session': True}
