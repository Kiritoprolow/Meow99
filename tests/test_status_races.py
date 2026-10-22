"""Rủi ro ghi đè status.json giữa gateway và worker, retry đang chạy, tạo job song song."""
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from tests.fakes import make_service
from video_engine import pipeline
from video_engine.pipeline import run_job
from video_engine.storage.outputs import LocalStorage

PAYLOAD = {"topic": "Vì sao bầu trời xanh"}
DONE = {"youtube": {"state": "skipped"}, "video_url": "file:///x/video.mp4", "prefix": "outputs/j/attempt-1"}


class CancelDoesNotClobberNewerStatus(unittest.TestCase):
    def test_worker_completed_while_gateway_confirms_cancel(self):
        svc, gh, store = make_service()
        jid = svc.create_job(PAYLOAD)["job_id"]
        real_cancel = gh.cancel_run

        def cancel_and_worker_finishes(run_id):
            # trong lúc gateway chờ GitHub xác nhận, worker kịp ghi completed + result
            rec = store.get_status(jid)
            rec.update(status="completed", stage="done", result=DONE)
            store.put_status(jid, rec)
            real_cancel(run_id)
        gh.cancel_run = cancel_and_worker_finishes
        out = svc.cancel_job(jid)
        self.assertEqual(out["status"], "completed")
        self.assertFalse(out["cancelled"])
        final = store.get_status(jid)
        self.assertEqual((final["status"], final["result"]), ("completed", DONE))   # kết quả không bị xóa

    def test_retry_started_while_gateway_confirms_cancel(self):
        svc, gh, store = make_service()
        jid = svc.create_job(PAYLOAD)["job_id"]
        real_cancel = gh.cancel_run

        def cancel_and_job_moves_on(run_id):
            rec = store.get_status(jid)
            rec.update(status="queued", stage="queued", attempt=2)
            store.put_status(jid, rec)
            real_cancel(run_id)
        gh.cancel_run = cancel_and_job_moves_on
        svc.cancel_job(jid)
        self.assertEqual((store.get_status(jid)["status"], store.get_status(jid)["attempt"]), ("queued", 2))

    def test_plain_cancel_still_writes_cancelled(self):
        svc, gh, store = make_service()
        jid = svc.create_job(PAYLOAD)["job_id"]
        out = svc.cancel_job(jid)
        self.assertTrue(out["cancelled"])
        self.assertEqual(store.get_status(jid)["status"], "cancelled")


class StaleWorkerGuard(unittest.TestCase):
    def test_old_attempt_run_cannot_overwrite_new_attempt(self):
        svc, gh, store = make_service()
        jid = svc.create_job(PAYLOAD)["job_id"]
        gh.finish(1001, "failure")
        svc.retry_job(jid)                                   # status.json -> attempt 2, queued
        before = dict(store.get_status(jid))
        root = Path(svc.s.local_store_dir)
        with mock.patch.object(pipeline, "get_engine", side_effect=AssertionError("không được render")):
            rec = run_job(jid, 1, store, LocalStorage(root), env={})   # run attempt 1 khởi động trễ
        self.assertEqual(rec["attempt"], 2)
        self.assertEqual(store.get_status(jid), before)       # status.json nguyên vẹn
        self.assertFalse((root / "outputs").exists())

    def test_same_attempt_still_runs(self):
        svc, gh, store = make_service()
        jid = svc.create_job(PAYLOAD)["job_id"]
        with mock.patch.object(pipeline, "get_engine", side_effect=RuntimeError("đã vào engine")):
            rec = run_job(jid, 1, store, LocalStorage(Path(svc.s.local_store_dir)), env={})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("đã vào engine", rec["error"])


class ConcurrentCreate(unittest.TestCase):
    def test_parallel_creates_get_distinct_ids_and_dispatches(self):
        svc, gh, store = make_service()
        lock = threading.Lock()
        with ThreadPoolExecutor(8) as ex:
            outs = list(ex.map(lambda i: svc.create_job({"topic": f"Chủ đề {i}"}), range(24)))
        ids = {o["job_id"] for o in outs}
        self.assertEqual(len(ids), 24)
        with lock:
            self.assertEqual({j for j, _a in gh.dispatched}, ids)
        self.assertEqual(set(store.list_job_ids()), ids)


if __name__ == "__main__":
    unittest.main()
