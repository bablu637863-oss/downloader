import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

STATE = tempfile.TemporaryDirectory()
os.environ['DOWNLOADER_STATE_DIR'] = STATE.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import downloader as app
import desktop_support as platform


class DesktopTests(unittest.TestCase):
    def test_import_state_is_private(self):
        self.assertEqual(Path(app.CFG_PATH).parent, Path(STATE.name))
        self.assertFalse(Path('settings.json').exists())

    def test_sizes_and_stream_pairing(self):
        info = {'formats': [dict(format_id='v', height=1080, width=1920, fps=59.94,
                                vcodec='vp9', acodec='none', dynamic_range='HDR10', filesize=5000000),
                            dict(format_id='a', vcodec='none', acodec='aac', ext='m4a', filesize=1000000)]}
        q = app.video_options(info, 'mp4', 10)[0]
        self.assertEqual(q['size'], 6000000)
        self.assertEqual(q['fid'], 'v+a')
        self.assertEqual(q['fps'], 59.94)
        self.assertTrue(q['hdr'])
        self.assertEqual(app.fsize({'filesize_approx': 100, 'tbr': 500}, 10), 0)
        self.assertEqual(app.size_text(0), 'Unavailable')

    def test_folder_lock_rejects_other_process(self):
        root = Path(STATE.name) / 'locked'
        script = ('import sys; sys.path.insert(0, '+repr(str(Path(app.__file__).parent))+'); '
                  'import downloader as a; c=a.folder_lock('+repr(str(root))+'); c.__enter__()')
        with app.folder_lock(str(root)):
            r = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('FolderBusy', r.stderr)
        with app.folder_lock(str(root)):
            pass

    def test_process_suspend_resume_and_kill(self):
        counter = Path(STATE.name)/'counter'
        code = ('import time,pathlib\np=pathlib.Path('+repr(str(counter))+')\n'
                'for i in range(200):\n with p.open("a") as f: f.write(str(i)+"\\n")\n time.sleep(.03)\n')
        p = subprocess.Popen([sys.executable, '-c', code], **platform.process_options())
        try:
            deadline = time.monotonic()+5
            while not counter.exists() and time.monotonic()<deadline:
                time.sleep(.03)
            self.assertTrue(counter.exists())
            platform.pause_tree(p.pid)
            time.sleep(.15)
            before = counter.read_text()
            time.sleep(.25)
            self.assertEqual(counter.read_text(), before)
            platform.pause_tree(p.pid, resume=True)
            time.sleep(.2)
            self.assertGreater(int(counter.read_text().splitlines()[-1]), int(before.splitlines()[-1]))
        finally:
            app.kill_group(p)
        self.assertIsNotNone(p.poll())

    def test_progress_pipe_and_combined_total(self):
        messages = ['PROG|downloading|1000000|5000000|NA|100000|10',
                    'PROG|finished|5000000|5000000|NA|100000|0',
                    'PROG|downloading|500000|1000000|NA|100000|5',
                    'PROG|finished|1000000|1000000|NA|100000|0']
        code = '\n'.join('print('+repr(m)+',flush=True)' for m in messages)
        with patch.object(sys.stdin, 'isatty', return_value=False), contextlib.redirect_stdout(io.StringIO()) as out:
            rc, _ = app.run_with_bar([sys.executable, '-c', code], parts=2,
                                     est_total=6000000, total_exact=True)
        self.assertEqual(rc, 0)
        self.assertIn('1.0 MB / 6.0 MB', out.getvalue())
        self.assertIn('5.5 MB / 6.0 MB', out.getvalue())
        self.assertIn('6.0 MB / 6.0 MB', out.getvalue())

    def test_optional_menu_once_and_codec_preserved(self):
        info = {'thumbnail': 'present'}
        with patch('builtins.input', side_effect=['1', '', '']), contextlib.redirect_stdout(io.StringIO()) as out:
            app.options_menu(info, 'mp4', False, 10, True, '', 0, app.new_state(), 0)
        self.assertEqual(out.getvalue().count('  Options'), 1)
        q = dict(size=1, qtxt='1080p', codec='VP9', exact=True, hdr=True, h=1080,
                 fps=59.94, tag='1080p', fargs=['-f', 'v+a'], aq=None)
        with patch.dict(app.CFG, convert=True):
            spec, _ = app.make_spec('mp4', False, q, app.new_state(), 10)
        self.assertNotIn('convert', spec)

    def test_console_configuration_and_cli_quit(self):
        code = 'import downloader as a; a.configure_console(); a.main()'
        env = dict(os.environ, DOWNLOADER_STATE_DIR=STATE.name, TERM='xterm')
        # Homebrew speed-up prompts are unrelated to this launch/quit check.
        code = 'import downloader as a; a.offer_speedup=lambda:None; a.main()'
        if all(__import__('shutil').which(n) for n in ('ffmpeg','ffprobe','yt-dlp')):
            result = subprocess.run([sys.executable, '-c', code], input='q\n', text=True,
                                    encoding='utf-8', capture_output=True, env=env, timeout=15,
                                    cwd=str(Path(app.__file__).parent))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Downloader', result.stdout)

    def test_capture_timeout_reaps_process(self):
        r = app.capture([sys.executable, '-c', 'import time;time.sleep(20)'], timeout=.1)
        self.assertEqual(r.returncode, 124)

    def test_noninteractive_manual_trim(self):
        with patch.object(sys.stdin, 'isatty', return_value=False), patch.object(app, 'trim_manual', return_value=(1, 3)) as manual:
            self.assertEqual(app.trim_ui(10, '', None), (1, 3))
        manual.assert_called_once()


class SetupTests(unittest.TestCase):
    def test_mac_shortcuts_do_not_replace_aliases(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('installer', Path(app.__file__).parent/'setup.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            profile = home/'.zshrc'
            profile.write_text("alias yt='existing-tool'\n")
            folder = installer.install_shortcuts(Path(app.__file__).parent, home=home, windows=False)
            installer.install_shortcuts(Path(app.__file__).parent, home=home, windows=False)
            self.assertEqual(profile.read_text().count('# Downloader user commands'), 1)
            self.assertIn("alias yt='existing-tool'", profile.read_text())
            self.assertTrue((folder/'yt').exists())
            self.assertTrue((folder/'DD').exists())

    def test_interactive_windows_trim_keys(self):
        with patch.object(app, 'WINDOWS', True), patch.object(sys.stdin, 'isatty', return_value=True), patch.object(app, 'read_key', side_effect=[b'\x1b[A',b'\r']), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(app.trim_ui(10, '', None), (1, 10))


if __name__ == '__main__':
    unittest.main()
