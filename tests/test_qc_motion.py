"""QC chuyển động: video tĩnh phải bị loại, video có chuyển động phải đạt (ffmpeg thật, nguồn lavfi)."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from video_engine.quality_control.qc import run_qc

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
META = {"title": "t", "tags": []}


def make(path: Path, src: str, secs: int = 4, fps: int = 12, audio: bool = False) -> Path:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"{src}{':' if '=' in src else '='}size=320x180:rate={fps}:duration={secs}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:d={secs}", "-c:a", "aac", "-shortest"]
    subprocess.run(cmd + ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def checks(res):
    return {c["name"]: c for c in res["checks"]}


@unittest.skipUnless(HAVE_FFMPEG, "cần ffmpeg/ffprobe")
class MotionQc(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)

    def qc(self, video, **kw):
        return run_qc(video, (320, 180), 4.0, None, META, require_audio=False, check_motion=True, **kw)

    def test_static_video_fails_motion(self):
        res = self.qc(make(self.d / "static.mp4", "color=c=blue"))
        self.assertFalse(res["ok"])
        self.assertFalse(checks(res)["motion"]["ok"])

    def test_moving_video_passes_motion(self):
        res = self.qc(make(self.d / "moving.mp4", "testsrc2"), expected_frames=48, expected_fps=12.0)
        c = checks(res)
        self.assertTrue(res["ok"], res["checks"])
        self.assertTrue(c["motion"]["ok"] and c["frame_count"]["ok"] and c["frame_rate"]["ok"])
        self.assertGreaterEqual(res["probe"]["motion"]["changed_ratio"], 0.6)

    def test_wrong_frame_count_and_fps_detected(self):
        res = self.qc(make(self.d / "m.mp4", "testsrc2"), expected_frames=60, expected_fps=24.0)
        c = checks(res)
        self.assertFalse(c["frame_count"]["ok"])
        self.assertFalse(c["frame_rate"]["ok"])

    def test_unexpected_audio_flagged_for_silent_design(self):
        res = self.qc(make(self.d / "a.mp4", "testsrc2", audio=True))
        self.assertFalse(checks(res)["no_audio_stream"]["ok"])

    def test_default_qc_unchanged_requires_audio(self):
        res = run_qc(make(self.d / "n.mp4", "testsrc2"), (320, 180), 4.0, None, META)
        self.assertFalse(checks(res)["audio_stream"]["ok"])
        self.assertNotIn("motion", checks(res))


if __name__ == "__main__":
    unittest.main()
