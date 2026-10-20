"""Test video_engine: render FFmpeg thật (smoke), QC, nhánh lỗi/hủy, redact secret, không ghi đè, YouTube."""
import json
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from app.job_store import LocalJobStore
from tests.fakes import make_service
from video_engine import pipeline
from video_engine.pipeline import Cancelled, run_job
from video_engine.quality_control.qc import run_qc
from video_engine.script.generator import generate
from video_engine.storage.outputs import HubStorage, LocalStorage, OutputExists, StorageError

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
SECRET = "hf_SECRETTOKEN_abcdef123456"


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


class Env(unittest.TestCase):
    def setUp(self):
        self.svc, self.gh, self.store = make_service()
        self.root = Path(self.svc.s.local_store_dir)
        self.storage = LocalStorage(self.root)

    def new_job(self, **kw):
        return self.svc.create_job({"topic": "Vì sao bầu trời có màu xanh?", "duration_seconds": 6,
                                    "resolution": "480p", **kw})["job_id"]

    def run_it(self, jid, attempt=1, storage=None, env=None):
        return run_job(jid, attempt, self.store, storage or self.storage, env=env or {})


class ScriptTests(unittest.TestCase):
    def test_template_marked_and_no_double_punctuation(self):
        segs, src = generate("Vì sao trời xanh?", "vi", 30, None)
        self.assertEqual(src, "template")
        self.assertNotIn("?.", segs[0].text)

    def test_user_script_segments_cover_duration_exactly(self):
        segs, src = generate("T", "vi", 30, "Đoạn một.\n\nĐoạn hai dài hơn một chút.\n\nĐoạn ba.")
        self.assertEqual((src, len(segs)), ("user", 3))
        self.assertAlmostEqual(segs[0].start, 0.0)
        self.assertAlmostEqual(segs[-1].end, 30.0, places=2)
        for a, b in zip(segs, segs[1:]):
            self.assertAlmostEqual(a.end, b.start, places=2)


@unittest.skipUnless(HAVE_FFMPEG, "cần ffmpeg/ffprobe")
class RenderSmoke(Env):
    def test_real_mp4_rendered_and_stored(self):
        jid = self.new_job(aspect_ratio="9:16")
        rec = self.run_it(jid)
        self.assertEqual(rec["status"], "completed", rec.get("error"))
        res = rec["result"]
        video = self.root / res["prefix"] / "video.mp4"
        info = probe(video)
        kinds = {s["codec_type"]: s for s in info["streams"]}
        self.assertEqual((kinds["video"]["width"], kinds["video"]["height"]), (480, 854))
        self.assertEqual(kinds["video"]["codec_name"], "h264")
        self.assertIn("audio", kinds)
        self.assertAlmostEqual(float(info["format"]["duration"]), 6.0, delta=0.6)
        for name in ("thumbnail.jpg", "metadata.json", "captions.srt", "script.txt", "report.json"):
            self.assertTrue((self.root / res["prefix"] / name).stat().st_size > 0, name)
        self.assertEqual(res["youtube"]["state"], "skipped")
        self.assertEqual(res["script_source"], "template")

    def test_landscape_and_square_sizes(self):
        for ratio, res, want in (("16:9", "480p", (854, 480)), ("1:1", "480p", (480, 480))):
            with self.subTest(ratio=ratio):
                rec = self.run_it(self.new_job(aspect_ratio=ratio, resolution=res))
                self.assertEqual(rec["status"], "completed", rec.get("error"))
                v = next(s for s in probe(self.root / rec["result"]["prefix"] / "video.mp4")["streams"] if s["codec_type"] == "video")
                self.assertEqual((v["width"], v["height"]), want)


@unittest.skipUnless(HAVE_FFMPEG, "cần ffmpeg/ffprobe")
class QcTests(Env):
    def test_qc_detects_empty_and_garbage_files(self):
        d = Path(tempfile.mkdtemp())
        empty, junk = d / "e.mp4", d / "j.mp4"
        empty.write_bytes(b"")
        junk.write_bytes(b"not a video" * 1000)
        meta = {"title": "t", "tags": []}
        for f in (empty, junk, d / "missing.mp4"):
            with self.subTest(f=f.name):
                self.assertFalse(run_qc(f, (480, 854), 6.0, None, meta)["ok"])

    def test_qc_failure_blocks_upload(self):
        jid = self.new_job()
        calls = []
        class Spy(LocalStorage):
            def upload(self, *a, **k):
                calls.append(a); return super().upload(*a, **k)
        bad = {"ok": False, "checks": [{"name": "duration", "ok": False, "detail": "x"}], "probe": {}}
        with mock.patch.object(pipeline, "run_qc", return_value=bad):
            rec = self.run_it(jid, storage=Spy(self.root))
        self.assertEqual((rec["status"], rec["stage"]), ("failed", "quality_control"))
        self.assertEqual(calls, [])
        self.assertFalse((self.root / "outputs").exists())


@unittest.skipUnless(HAVE_FFMPEG, "cần ffmpeg/ffprobe")
class FailureModes(Env):
    def test_error_message_redacts_secrets(self):
        jid = self.new_job()
        class Boom(LocalStorage):
            def upload(self, *a, **k):
                raise RuntimeError(f"401 bad token {SECRET}")
        rec = self.run_it(jid, storage=Boom(self.root), env={"HF_TOKEN": SECRET})
        self.assertEqual(rec["status"], "failed")
        self.assertNotIn(SECRET, json.dumps(rec))
        self.assertEqual(self.store.get_status(jid)["status"], "failed")
        self.assertNotIn(SECRET, json.dumps(self.store.get_status(jid)))

    def test_signal_cancel_marks_cancelled(self):
        jid = self.new_job()
        class Cut(LocalStorage):
            def upload(self, *a, **k):
                raise Cancelled("sigterm")
        self.assertEqual(self.run_it(jid, storage=Cut(self.root))["status"], "cancelled")

    def test_missing_request_fails_cleanly(self):
        rec = run_job("job_20261020_120000_abcdef", 1, self.store, self.storage, env={})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("request.json", rec["error"])

    def test_duplicate_run_of_completed_attempt_is_a_noop(self):
        jid = self.new_job()
        first = self.run_it(jid)
        self.assertEqual(first["status"], "completed")
        video = self.root / first["result"]["prefix"] / "video.mp4"
        mtime = video.stat().st_mtime_ns
        again = self.run_it(jid)  # dispatch đúp cùng attempt
        self.assertEqual(again["status"], "completed")           # không bị đánh dấu failed
        self.assertEqual(self.store.get_status(jid)["result"], first["result"])
        self.assertEqual(video.stat().st_mtime_ns, mtime)        # không ghi đè thành phẩm

    def test_storage_refuses_existing_destination(self):
        jid = self.new_job()
        self.assertEqual(self.run_it(jid)["status"], "completed")
        files = {"a.txt": self.root / "jobs" / jid / "request.json"}
        with self.assertRaises(OutputExists):
            LocalStorage(self.root).upload(jid, 1, files)

    def test_retry_flow_writes_new_attempt_dir_and_keeps_old(self):
        jid = self.new_job()
        class Flaky(LocalStorage):
            def upload(self, *a, **k):
                raise StorageError("mạng lỗi")
        self.assertEqual(self.run_it(jid, storage=Flaky(self.root))["status"], "failed")
        out = self.svc.retry_job(jid)            # gateway: failed -> attempt 2
        self.assertEqual(out["attempt"], 2)
        rec = self.run_it(jid, attempt=2)
        self.assertEqual(rec["status"], "completed", rec.get("error"))
        self.assertTrue(rec["result"]["prefix"].endswith("attempt-2"))

    def test_gateway_reads_final_result_written_by_worker(self):
        jid = self.new_job()
        self.run_it(jid)
        st = self.svc.get_job_result(jid)
        self.assertTrue(st["ready"])
        self.assertTrue(st["result"]["video_url"].startswith("file://"))


@unittest.skipUnless(HAVE_FFMPEG, "cần ffmpeg/ffprobe")
class YouTubeStates(Env):
    def test_preview_never_publishes(self):
        self.assertEqual(self.run_it(self.new_job())["result"]["youtube"]["state"], "skipped")

    def test_publish_without_oauth_still_completes_pending(self):
        rec = self.run_it(self.new_job(output_mode="publish"))
        self.assertEqual(rec["status"], "completed")
        self.assertEqual(rec["result"]["youtube"]["state"], "pending_configuration")

    def test_publish_with_secrets_never_claims_uploaded(self):
        env = {"YOUTUBE_CLIENT_ID": "cid-123456", "YOUTUBE_CLIENT_SECRET": "csecret-123456", "YOUTUBE_REFRESH_TOKEN": "rtoken-123456"}
        rec = self.run_it(self.new_job(output_mode="publish"), env=env)
        self.assertEqual(rec["result"]["youtube"]["state"], "not_implemented")
        self.assertNotIn("url", rec["result"]["youtube"])
        self.assertNotIn("rtoken-123456", json.dumps(rec))


# ---------------------------------------------------------------- HubStorage với huggingface_hub giả
class FakeCommitInfo:
    oid = "a1b2c3d4e5f6"


class FakeHfApi:
    private = True
    existing_prefix: list = []
    commit_failures = 0
    size_mismatch = False
    instances: list = []

    def __init__(self, token=None):
        self.token, self.commits, self.files = token, [], {}
        FakeHfApi.instances.append(self)

    def repo_info(self, repo_id, repo_type=None):
        return types.SimpleNamespace(private=FakeHfApi.private)

    def list_repo_tree(self, repo_id, path_in_repo=None, repo_type=None, recursive=False):
        return list(FakeHfApi.existing_prefix)

    def create_commit(self, repo_id, ops, repo_type=None, commit_message=None):
        if FakeHfApi.commit_failures > 0:
            FakeHfApi.commit_failures -= 1
            raise ConnectionError("tạm thời lỗi " + (self.token or ""))
        for op in ops:
            self.files[op.path_in_repo] = Path(op.path_or_fileobj).stat().st_size + (1 if FakeHfApi.size_mismatch else 0)
        self.commits.append(commit_message)
        return FakeCommitInfo()

    def get_paths_info(self, repo_id, paths, repo_type=None, revision=None):
        return [types.SimpleNamespace(path=p, size=self.files[p]) for p in paths if p in self.files]


class HubStorageTests(unittest.TestCase):
    def setUp(self):
        FakeHfApi.private, FakeHfApi.existing_prefix, FakeHfApi.commit_failures = True, [], 0
        FakeHfApi.size_mismatch, FakeHfApi.instances = False, []
        mod = types.ModuleType("huggingface_hub")
        mod.HfApi = FakeHfApi
        mod.CommitOperationAdd = lambda path_in_repo, path_or_fileobj: types.SimpleNamespace(
            path_in_repo=path_in_repo, path_or_fileobj=path_or_fileobj)
        self.patch = mock.patch.dict(sys.modules, {"huggingface_hub": mod})
        self.patch.start()
        d = Path(tempfile.mkdtemp())
        (d / "video.mp4").write_bytes(b"x" * 100)
        (d / "metadata.json").write_text("{}")
        self.files = {"video.mp4": d / "video.mp4", "metadata.json": d / "metadata.json"}
        self.st = HubStorage(SECRET, "user/data", retries=3, sleep=lambda _s: None)
        self.jid = "job_20261020_120000_abcdef"

    def tearDown(self):
        self.patch.stop()

    def test_success_pins_revision_and_reports_private(self):
        out = self.st.upload(self.jid, 1, self.files)
        self.assertEqual(out["revision"], "a1b2c3d4e5f6")
        self.assertEqual(out["access"], "private")
        self.assertEqual(out["urls"]["video.mp4"],
                         f"https://huggingface.co/datasets/user/data/resolve/a1b2c3d4e5f6/outputs/{self.jid}/attempt-1/video.mp4")
        self.assertEqual(len(FakeHfApi.instances[0].commits), 1)  # một commit cho mọi file

    def test_public_repo_reported_public(self):
        FakeHfApi.private = False
        self.assertEqual(self.st.upload(self.jid, 1, self.files)["access"], "public")

    def test_refuses_to_overwrite_existing_prefix(self):
        FakeHfApi.existing_prefix = [object()]
        with self.assertRaises(OutputExists):
            self.st.upload(self.jid, 1, self.files)
        self.assertEqual(FakeHfApi.instances[0].commits, [])

    def test_retries_then_succeeds_and_error_has_no_token(self):
        FakeHfApi.commit_failures = 2
        self.assertEqual(self.st.upload(self.jid, 1, self.files)["revision"], "a1b2c3d4e5f6")
        FakeHfApi.commit_failures = 3
        with self.assertRaises(StorageError) as cm:
            self.st.upload(self.jid, 1, self.files)
        self.assertNotIn(SECRET, str(cm.exception))

    def test_verification_failure_is_an_error_not_success(self):
        FakeHfApi.size_mismatch = True
        with self.assertRaises(StorageError):
            self.st.upload(self.jid, 1, self.files)


if __name__ == "__main__":
    unittest.main()
