"""Optional real download/merge checks; needs FFmpeg, ffprobe and yt-dlp."""
import contextlib
import functools
import http.server
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# test_desktop imports with an isolated state directory first under discovery.
_state = tempfile.TemporaryDirectory()
if "downloader" not in sys.modules:
    os.environ["DOWNLOADER_STATE_DIR"] = _state.name
import downloader as app


@unittest.skipUnless(all(shutil.which(n) for n in ('ffmpeg','ffprobe','yt-dlp')), 'media tools unavailable')
class MediaIntegration(unittest.TestCase):
    def test_real_http_video_audio_download(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root/'fixture.mp4'
            subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','testsrc=size=256x144:rate=30',
                            '-f','lavfi','-i','sine=frequency=440:sample_rate=44100',
                            '-t','1.5','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(source)], check=True)
            class Quiet(http.server.SimpleHTTPRequestHandler):
                def log_message(self, *args):
                    pass
            handler = functools.partial(Quiet, directory=str(root))
            server = http.server.ThreadingHTTPServer(('127.0.0.1',0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f'http://127.0.0.1:{server.server_port}/fixture.mp4'
                info, error = app.fetch(url)
                self.assertIsNotNone(info, error)
                st = app.new_state()
                q = dict(fargs=['-f','best'], aq=None, size=source.stat().st_size,
                         exact=True, hdr=False, h=144, fps=30, codec='H.264', tag='144p', qtxt='144p')
                spec, _ = app.make_spec('mp4', False, q, st, 1.5)
                spec.update(source_info=info, est=source.stat().st_size, aria_allowed=False)
                with patch.dict(app.CFG, out=str(root/'downloads'), cookies='', notify=False, limit=''), \
                     patch.object(app,'open_folder'), patch.object(app,'notify'), \
                     patch.object(sys.stdin,'isatty',return_value=False), \
                     contextlib.redirect_stdout(io.StringIO()):
                    result = app.execute([url], spec, {'title':'Local fixture','id':'fixture'})
                self.assertIsNone(result)
                outputs = list((root/'downloads').glob('*.mp4'))
                self.assertTrue(outputs)
                details = app.media_details(str(outputs[0]))
                self.assertIn('audio', {s.get('codec_type') for s in details['streams']})
                self.assertIn('video', {s.get('codec_type') for s in details['streams']})
                self.assertEqual(app.audio_signal(str(outputs[0])), 'audible')
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
