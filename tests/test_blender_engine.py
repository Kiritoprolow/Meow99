"""Test engine Blender 3D.

Hai nhóm, KHÔNG được nhầm lẫn:
  * FakeBlender*  : kiểm thử phần HARNESS (subprocess, timeout, hủy, fallback, cách ly, QC, upload) bằng một "Blender giả"
                    (script Python ghi PNG). Nhóm này KHÔNG chứng minh Blender dựng được cảnh.
  * RealBlender*  : chạy Blender thật; tự SKIP (kèm lý do) nếu không có Blender (đặt BLENDER_BIN hoặc cài `blender` vào PATH).
"""
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.job_store import LocalJobStore
from tests.fakes import make_service
from video_engine.engines.blender_3d import Blender3DEngine, blender_version, find_blender
from video_engine.engines.base import RenderContext, StageError
from video_engine.pipeline import Cancelled, run_job
from video_engine.storage.outputs import LocalStorage

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
REAL_BLENDER = os.environ.get("BLENDER_BIN") or shutil.which("blender")
SECRET = "hf_SECRETTOKEN_abcdef123456"

OK_BODY = '''
from PIL import Image, ImageDraw
w, h, n = cfg["width"], cfg["height"], cfg["frames"]
os.makedirs(cfg["frames_dir"], exist_ok=True)
for i in range(1, n + 1):
    im = Image.new("RGB", (w, h), (20, 30, 50))
    x = int((w - w // 4) * (i - 1) / max(n - 1, 1))
    ImageDraw.Draw(im).rectangle([x, h // 3, x + w // 4, h // 3 + h // 3], fill=(230, 90, 40))
    if not (SKIP_FRAME and i == SKIP_FRAME):
        im.save(os.path.join(cfg["frames_dir"], "frame_%04d.png" % i))
open(cfg["blend_path"], "wb").write(b"FAKEBLEND" * 100)
print("RENDER_DONE")
'''

TEMPLATE = '''#!{py}
import json, os, sys, time
args = sys.argv[1:]
if args == ["--version"]:
    print("Blender {version}")
    sys.exit(0)
cfg = json.load(open(args[args.index("--config") + 1]))
SKIP_FRAME = {skip}
if os.environ.get("FAKE_CALLS"):
    open(os.environ["FAKE_CALLS"], "a").write(cfg["engine"] + "\\n")
if os.environ.get("FAKE_ENVDUMP"):
    json.dump(dict(os.environ), open(os.environ["FAKE_ENVDUMP"], "w"))
{pre}
{body}
'''


def make_fake(directory: Path, *, version="5.2.2", pre="", body=OK_BODY, skip=0) -> str:
    path = directory / "fake_blender"
    path.write_text(TEMPLATE.format(py=sys.executable, version=version, pre=pre, body=body, skip=skip), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def req3d(**over):
    base = {"topic": "Khối tròn xoay", "project_type": "animation_3d", "duration_seconds": 5, "resolution": "480p",
            "options": {"fps": 12}}
    base.update(over)
    return base


class Env(unittest.TestCase):
    def setUp(self):
        self.svc, self.gh, self.store = make_service()
        self.root = Path(self.svc.s.local_store_dir)
        self.storage = LocalStorage(self.root)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def new_job(self, **over):
        return self.svc.create_job(req3d(**over))["job_id"]

    def run_it(self, jid, binary, attempt=1, extra=None):
        env = {**os.environ, "BLENDER_BIN": binary, **(extra or {})}
        return run_job(jid, attempt, self.store, self.storage, env=env)


# ====================================================================== harness với Blender GIẢ
@unittest.skipUnless(HAVE_FFMPEG, "cần ffmpeg/ffprobe")
class FakeBlenderPipeline(Env):
    def test_full_pipeline_outputs_and_qc(self):
        rec = self.run_it(self.new_job(), make_fake(self.tmp))
        self.assertEqual(rec["status"], "completed", rec.get("error"))
        res = rec["result"]
        out = self.root / res["prefix"]
        info = probe(out / "video.mp4")
        kinds = {s["codec_type"]: s for s in info["streams"]}
        self.assertNotIn("audio", kinds)                          # không giả vờ có âm thanh
        self.assertEqual((kinds["video"]["width"], kinds["video"]["height"]), (854, 480))
        self.assertEqual(int(kinds["video"]["nb_frames"]), 60)
        for name in ("scene.blend", "frames.zip", "thumbnail.jpg", "metadata.json", "report.json"):
            self.assertGreater((out / name).stat().st_size, 0, name)
        with zipfile.ZipFile(out / "frames.zip") as zf:
            self.assertEqual(len(zf.namelist()), 60)
        self.assertEqual((res["project_type"], res["engine"]), ("animation_3d", "blender_3d"))
        self.assertTrue(res["scene_blend_url"].endswith("scene.blend"))
        self.assertTrue(res["qc"]["motion"]["changed_ratio"] >= 0.6)
        meta = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual((meta["audio"], meta["engine"]), ("none", "blender_3d"))
        self.assertNotIn("subtitle_timing", meta)
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["engine_report"]["blender"]["version"], "5.2.2")
        self.assertNotIn("script.txt", [f["path"].rsplit("/", 1)[-1] for f in res["files"]])
        self.assertIsNone(report["engine_report"]["blender"]["fallback"])

    def test_blender_not_installed_is_clear_error(self):
        jid = self.new_job()
        rec = run_job(jid, 1, self.store, self.storage, env={"PATH": "/nonexistent", "BLENDER_BIN": "/nonexistent/blender"})
        self.assertEqual((rec["status"], rec["stage"]), ("failed", "blender_check"))
        self.assertIn("Không tìm thấy Blender", rec["error"])
        self.assertFalse((self.root / "outputs").exists())

    def test_old_blender_rejected(self):
        rec = self.run_it(self.new_job(), make_fake(self.tmp, version="3.6.0"))
        self.assertEqual((rec["status"], rec["stage"]), ("failed", "blender_check"))
        self.assertIn("quá cũ", rec["error"])

    def test_version_parser(self):
        self.assertEqual(blender_version(make_fake(self.tmp, version="4.5.14 LTS"))[0], (4, 5, 14))
        self.assertEqual(blender_version(make_fake(self.tmp, version="5.2.2"))[0], (5, 2, 2))
        with self.assertRaises(StageError):
            blender_version(make_fake(self.tmp, version="4.1.0"))
        with self.assertRaises(StageError):
            find_blender({"BLENDER_BIN": "/nonexistent/blender", "PATH": ""})

    def test_eevee_failure_falls_back_to_cycles_and_says_so(self):
        calls = self.tmp / "calls.txt"
        pre = 'if cfg["engine"] == "eevee":\n    print("EGL init failed"); sys.exit(3)'
        rec = self.run_it(self.new_job(options={"fps": 12, "engine": "eevee"}), make_fake(self.tmp, pre=pre), extra={"FAKE_CALLS": str(calls)})
        self.assertEqual(rec["status"], "completed", rec.get("error"))
        self.assertEqual(calls.read_text().split(), ["eevee", "cycles"])
        report = json.loads((self.root / rec["result"]["prefix"] / "report.json").read_text(encoding="utf-8"))
        fb = report["engine_report"]["blender"]["fallback"]
        self.assertEqual((fb["from"], fb["to"]), ("eevee", "cycles"))
        self.assertEqual(report["engine_report"]["blender"]["engine"], "cycles")
        self.assertTrue(any("Cycles" in n for n in rec["result"]["notes"]))

    def test_cycles_failure_does_not_retry_forever(self):
        calls = self.tmp / "calls.txt"
        fake = make_fake(self.tmp, pre='print("fatal: boom"); sys.exit(3)')
        rec = self.run_it(self.new_job(options={"fps": 12, "engine": "cycles"}), fake, extra={"FAKE_CALLS": str(calls)})
        self.assertEqual((rec["status"], rec["stage"]), ("failed", "render_3d"))
        self.assertEqual(calls.read_text().split(), ["cycles"])   # không có fallback vô hạn
        self.assertIn("fatal: boom", rec["error"])
        self.assertFalse((self.root / "outputs").exists())        # lỗi -> không upload gì

    def test_missing_frame_detected(self):
        rec = self.run_it(self.new_job(), make_fake(self.tmp, skip=7))
        self.assertEqual((rec["status"], rec["stage"]), ("failed", "render_3d"))
        self.assertIn("Thiếu 1/60 frame", rec["error"])

    def test_timeout_kills_blender_fail_fast(self):
        pidfile = self.tmp / "pid"
        pre = f'open({str(pidfile)!r}, "w").write(str(os.getpid()))\ntime.sleep(60)'
        rec = self.run_it(self.new_job(), make_fake(self.tmp, pre=pre), extra={"BLENDER_RENDER_TIMEOUT": "2"})
        self.assertEqual((rec["status"], rec["stage"]), ("failed", "render_3d"))
        self.assertIn("vượt quá 2s", rec["error"])
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)                  # tiến trình Blender đã bị giết

    @unittest.skipUnless(hasattr(signal, "SIGALRM"), "cần SIGALRM (Unix)")
    def test_cancel_signal_kills_blender_and_marks_cancelled(self):
        pidfile = self.tmp / "pid"
        pre = f'open({str(pidfile)!r}, "w").write(str(os.getpid()))\ntime.sleep(60)'
        fake = make_fake(self.tmp, pre=pre)

        def on_alarm(_s, _f):  # mô phỏng handler SIGTERM của video_engine.run khi GitHub hủy run
            raise Cancelled("signal")
        old = signal.signal(signal.SIGALRM, on_alarm)
        signal.alarm(3)
        try:
            rec = self.run_it(self.new_job(), fake)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)
        self.assertEqual((rec["status"], rec["stage"]), ("cancelled", "cancelled"))
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)
        self.assertFalse((self.root / "outputs").exists())        # hủy giữa chừng -> không có thành phẩm dở dang

    def test_secrets_not_passed_to_blender_process(self):
        dump = self.tmp / "env.json"
        rec = self.run_it(self.new_job(), make_fake(self.tmp),
                          extra={"HF_TOKEN": SECRET, "GITHUB_TOKEN": "ghp_SECRETTOKEN123", "MY_API_KEY": "abcdef123456",
                                 "FAKE_ENVDUMP": str(dump)})
        self.assertEqual(rec["status"], "completed", rec.get("error"))
        seen = json.loads(dump.read_text())
        for k in ("HF_TOKEN", "GITHUB_TOKEN", "MY_API_KEY"):
            self.assertNotIn(k, seen)
        self.assertTrue(seen["HOME"].endswith("/home"))            # HOME cô lập theo job
        self.assertNotIn(SECRET, json.dumps(rec))

    def test_hand_written_request_over_budget_rejected_before_blender(self):
        jid = self.new_job()
        raw = self.store.get_request(jid)
        self.store.put_status(jid, {"job_id": jid, "status": "queued", "attempt": 1, "owner": "default"})
        shutil.rmtree(self.root / "jobs" / jid / "request.json", ignore_errors=True)
        (self.root / "jobs" / jid / "request.json").write_text(
            json.dumps({**raw, "duration_seconds": 120}), encoding="utf-8")  # bỏ qua gateway: 120s x 12 = 1440 frame
        rec = self.run_it(jid, make_fake(self.tmp))
        self.assertEqual(rec["status"], "failed")
        self.assertIn("frame", rec["error"])

    def test_stage_progress_is_reported(self):
        seen = []
        real = self.store.put_status

        def spy(job_id, status):
            seen.append(status.get("stage"))
            real(job_id, status)
        self.store.put_status = spy
        self.assertEqual(self.run_it(self.new_job(), make_fake(self.tmp))["status"], "completed")
        order = [s for i, s in enumerate(seen) if s and (i == 0 or seen[i - 1] != s)]
        for a, b in zip(["blender_check", "render_3d", "encode", "thumbnail", "metadata", "quality_control", "uploading_to_hf", "done"],
                        [s for s in order if s in {"blender_check", "render_3d", "encode", "thumbnail", "metadata",
                                                   "quality_control", "uploading_to_hf", "done"}]):
            self.assertEqual(a, b)


@unittest.skipUnless(HAVE_FFMPEG, "cần ffmpeg/ffprobe")
class FakeBlenderIsolation(Env):
    def test_concurrent_jobs_write_separate_directories(self):
        fake = make_fake(self.tmp)
        ids = [self.new_job(topic=f"Cảnh số {i}") for i in range(3)]
        with ThreadPoolExecutor(3) as ex:
            recs = list(ex.map(lambda j: self.run_it(j, fake), ids))
        prefixes = set()
        for jid, rec in zip(ids, recs):
            self.assertEqual(rec["status"], "completed", rec.get("error"))
            prefixes.add(rec["result"]["prefix"])
            meta = json.loads((self.root / rec["result"]["prefix"] / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["job_id"], jid)                 # mỗi thư mục đúng job của nó
        self.assertEqual(len(prefixes), 3)

    def test_retry_after_failure_uses_new_attempt_dir_and_keeps_type(self):
        jid = self.new_job()
        bad = self.run_it(jid, make_fake(self.tmp, pre='sys.exit(3)', body=""), attempt=1)
        self.assertEqual(bad["status"], "failed")
        self.gh.finish(1001, "failure")
        out = self.svc.retry_job(jid)
        self.assertEqual(out["attempt"], 2)
        self.assertEqual(self.gh.dispatched_types[-1], "animation_3d")
        good = self.run_it(jid, make_fake(self.tmp / ".." / self.tmp.name), attempt=2)
        self.assertEqual(good["status"], "completed", good.get("error"))
        self.assertTrue(good["result"]["prefix"].endswith("attempt-2"))
        self.assertFalse((self.root / "outputs" / jid / "attempt-1").exists())  # attempt hỏng không để lại thành phẩm

    def test_duplicate_run_of_completed_3d_attempt_is_noop(self):
        jid = self.new_job()
        fake = make_fake(self.tmp)
        first = self.run_it(jid, fake)
        blend = self.root / first["result"]["prefix"] / "scene.blend"
        m = blend.stat().st_mtime_ns
        again = self.run_it(jid, fake)
        self.assertEqual(again["status"], "completed")
        self.assertEqual(blend.stat().st_mtime_ns, m)


# ====================================================================== Blender THẬT
CHECK_BLEND = '''
import bpy, json
sc = bpy.context.scene
def pos(name, f):
    sc.frame_set(f)
    return [round(v, 4) for v in bpy.data.objects[name].matrix_world.translation]
out = {"frame_end": sc.frame_end, "fps": sc.render.fps, "camera": sc.camera.name,
       "anim": {n: bpy.data.objects[n].animation_data is not None for n in ("MainObject", "SatellitePivot", "CameraRig", "Camera")},
       "main_pos": [pos("MainObject", 1), pos("MainObject", 7)],
       "cam_pos": [pos("Camera", 1), pos("Camera", sc.frame_end)]}
print("RESULT=" + json.dumps(out))
'''


@unittest.skipUnless(REAL_BLENDER, "Không tìm thấy Blender (đặt BLENDER_BIN hoặc cài `blender` vào PATH) — test Blender thật bị bỏ qua")
@unittest.skipUnless(HAVE_FFMPEG, "cần ffmpeg/ffprobe")
class RealBlender(Env):
    def _check_blend(self, blend: Path) -> dict:
        p = subprocess.run([REAL_BLENDER, "-b", "--factory-startup", str(blend), "--python-expr", CHECK_BLEND],
                           capture_output=True, text=True, timeout=180)
        line = next(ln for ln in p.stdout.splitlines() if ln.startswith("RESULT="))
        return json.loads(line[len("RESULT="):])

    def _run_real(self, **over):
        jid = self.new_job(**over)
        rec = self.run_it(jid, REAL_BLENDER)
        self.assertEqual(rec["status"], "completed", rec.get("error"))
        return rec, self.root / rec["result"]["prefix"]

    def _assert_real_outputs(self, rec, out: Path, frames: int, w: int, h: int):
        info = probe(out / "video.mp4")
        kinds = {s["codec_type"]: s for s in info["streams"]}
        self.assertEqual(kinds["video"]["codec_name"], "h264")
        self.assertEqual((kinds["video"]["width"], kinds["video"]["height"]), (w, h))
        self.assertEqual(int(kinds["video"]["nb_frames"]), frames)
        self.assertNotIn("audio", kinds)
        self.assertAlmostEqual(float(info["format"]["duration"]), 5.0, delta=0.2)
        motion = rec["result"]["qc"]["motion"]
        self.assertGreaterEqual(motion["changed_ratio"], 0.6, motion)
        with zipfile.ZipFile(out / "frames.zip") as zf:
            self.assertEqual(len(zf.namelist()), frames)
        self.assertEqual(rec["result"]["qc"]["ok"], True)
        return self._check_blend(out / "scene.blend")

    def test_eevee_orbit_camera_and_object_animation(self):
        rec, out = self._run_real(options={"fps": 12, "shape": "torus", "camera_move": "orbit", "engine": "eevee"})
        b = self._assert_real_outputs(rec, out, 60, 854, 480)
        self.assertEqual((b["frame_end"], b["fps"], b["camera"]), (60, 12, "Camera"))
        self.assertTrue(b["anim"]["MainObject"] and b["anim"]["CameraRig"] and b["anim"]["SatellitePivot"])
        self.assertNotEqual(b["main_pos"][0], b["main_pos"][1])     # vật thể thật sự di chuyển trong file .blend
        self.assertNotEqual(b["cam_pos"][0], b["cam_pos"][1])       # camera thật sự di chuyển
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["engine_report"]["blender"]["engine"], "eevee")
        self.assertIsNone(report["engine_report"]["blender"]["fallback"])

    def test_cycles_dolly_camera_square(self):
        rec, out = self._run_real(aspect_ratio="1:1", options={"fps": 12, "shape": "cone", "camera_move": "dolly", "engine": "cycles"})
        b = self._assert_real_outputs(rec, out, 60, 480, 480)
        self.assertTrue(b["anim"]["Camera"])
        self.assertNotEqual(b["cam_pos"][0], b["cam_pos"][1])

    def test_invalid_blender_script_failure_is_reported_not_hidden(self):
        # cfg hợp lệ nhưng ép lỗi bằng engine không tồn tại trong script -> script phải thoát mã != 0, harness báo lỗi thật
        cfg = self.tmp / "cfg.json"
        cfg.write_text(json.dumps({"frames_dir": str(self.tmp / "f"), "blend_path": str(self.tmp / "s.blend"), "width": 64,
                                   "height": 64, "fps": 12, "frames": 2, "shape": "teapot", "object_color": "#ffffff",
                                   "background_color": "#000000", "camera_move": "orbit", "engine": "cycles", "samples": 1}))
        (self.tmp / "f").mkdir()
        script = Path(__import__("video_engine.engines.blender_3d", fromlist=["x"]).SCENE_SCRIPT)
        p = subprocess.run([REAL_BLENDER, "-b", "--factory-startup", "--python-exit-code", "3", "--python", str(script),
                            "--", "--config", str(cfg)], capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 3)
        self.assertIn("shape không hỗ trợ", p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
