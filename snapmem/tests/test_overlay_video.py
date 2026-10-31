import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from snapmem.overlay import merge_video


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def _probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    return json.loads(out)["streams"]


class MergeVideoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.main = self.tmp / "main.mp4"
        _ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=64x96:d=1:r=10",
                "-f", "lavfi", "-i", "sine=frequency=440:d=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(self.main))
        # Overlay in anderer Größe (32x48): untere Hälfte rot, obere durchsichtig.
        img = Image.new("RGBA", (32, 48), (0, 0, 0, 0))
        img.paste((255, 0, 0, 255), (0, 24, 32, 48))
        self.overlay = self.tmp / "overlay.png"
        img.save(self.overlay)

    def _pixel(self, video, x, y):
        frame = self.tmp / "f.png"
        _ffmpeg("-i", str(video), "-frames:v", "1", str(frame))
        return Image.open(frame).convert("RGB").getpixel((x, y))

    def test_overlay_is_burned_in_and_audio_kept(self):
        out = self.tmp / "out.mp4"
        merge_video(self.main, self.overlay, out)
        types = {s["codec_type"]: s for s in _probe(out)}
        self.assertEqual((types["video"]["width"], types["video"]["height"]), (64, 96))
        self.assertIn("audio", types)
        r, _, b = self._pixel(out, 32, 80)   # unten: rot
        self.assertGreater(r, 180)
        self.assertLess(b, 90)
        r, _, b = self._pixel(out, 32, 10)   # oben: weiter blau
        self.assertLess(r, 90)
        self.assertGreater(b, 150)

    def test_video_without_audio_works(self):
        silent = self.tmp / "silent.mp4"
        _ffmpeg("-i", str(self.main), "-an", "-c:v", "copy", str(silent))
        out = self.tmp / "out.mp4"
        merge_video(silent, self.overlay, out)
        self.assertEqual([s["codec_type"] for s in _probe(out)], ["video"])

    def test_broken_input_raises(self):
        bad = self.tmp / "bad.mp4"
        bad.write_bytes(b"kein video")
        with self.assertRaises(RuntimeError):
            merge_video(bad, self.overlay, self.tmp / "o.mp4")


if __name__ == "__main__":
    unittest.main()
