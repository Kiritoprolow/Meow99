"""Unit test logic gateway: tạo job, ánh xạ job_id->run, trạng thái, retry, cancel, bảo mật."""
import unittest

from app.github_client import GitHubError
from app.service import ServiceError
from tests.fakes import make_service


def create(svc, **kw):
    return svc.create_job({"topic": "Vì sao bầu trời xanh", **kw})


class CreateTests(unittest.TestCase):
    def test_create_dispatches_and_persists(self):
        svc, gh, store = make_service()
        out = create(svc)
        jid = out["job_id"]
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "queued")
        self.assertEqual(gh.dispatched, [(jid, 1)])
        self.assertEqual(store.get_request(jid)["topic"], "Vì sao bầu trời xanh")
        self.assertEqual(store.get_status(jid)["status"], "queued")

    def test_invalid_payload_does_not_dispatch(self):
        svc, gh, _ = make_service()
        with self.assertRaises(ServiceError) as cm:
            svc.create_job({"topic": "x", "aspect_ratio": "4:3"})
        self.assertEqual(cm.exception.code, "invalid_payload")
        self.assertEqual(gh.dispatched, [])

    def test_duration_cap(self):
        svc, _, _ = make_service(max_duration_seconds=60)
        with self.assertRaises(ServiceError):
            create(svc, duration_seconds=61)

    def test_hourly_create_limit(self):
        svc, _, _ = make_service(create_limit_per_hour=2)
        create(svc), create(svc)
        with self.assertRaises(ServiceError) as cm:
            create(svc)
        self.assertEqual(cm.exception.code, "rate_limited")

    def test_dispatch_failure_marks_job_failed_and_hides_token(self):
        svc, gh, store = make_service()
        gh.fail_dispatch = GitHubError("lỗi ghp_SECRETTOKEN123 rò rỉ", 403)
        with self.assertRaises(ServiceError) as cm:
            create(svc)
        self.assertNotIn("ghp_SECRETTOKEN123", cm.exception.message)
        jid = store.list_job_ids()[0]
        rec = store.get_status(jid)
        self.assertEqual(rec["status"], "failed")
        self.assertNotIn("ghp_SECRETTOKEN123", rec["error"])


class StatusTests(unittest.TestCase):
    def test_job_id_to_run_mapping_and_status(self):
        svc, gh, _ = make_service()
        jid = create(svc)["job_id"]
        st = svc.get_job_status(jid)
        self.assertEqual(st["status"], "queued")
        self.assertEqual(st["run"]["id"], 1001)          # tìm theo run-name chứa job_id
        self.assertNotIn("progress", st)                  # không bịa phần trăm

    def test_unknown_and_malformed_ids(self):
        svc, _, _ = make_service()
        for bad, code in (("job_20261020_120000_abcdef", "not_found"), ("../../x", "invalid_job_id")):
            with self.assertRaises(ServiceError) as cm:
                svc.get_job_status(bad)
            self.assertEqual(cm.exception.code, code)

    def test_other_owner_cannot_see_job(self):
        svc, _, _ = make_service()
        jid = svc.create_job({"topic": "x"}, owner="alice")["job_id"]
        with self.assertRaises(ServiceError) as cm:
            svc.get_job_status(jid, owner="bob")
        self.assertEqual(cm.exception.code, "not_found")
        self.assertEqual(svc.list_jobs(owner="bob")["count"], 0)

    def test_reconcile_when_worker_died(self):
        svc, gh, _ = make_service()
        jid = create(svc)["job_id"]
        gh.finish(1001, "failure")
        st = svc.get_job_status(jid)
        self.assertEqual(st["status"], "failed")
        self.assertTrue(st["reconciled"])
        gh2_jid = create(svc)["job_id"]
        gh.finish(1002, "cancelled")
        self.assertEqual(svc.get_job_status(gh2_jid)["status"], "cancelled")

    def test_result_not_ready_then_ready(self):
        svc, _, store = make_service()
        jid = create(svc)["job_id"]
        self.assertFalse(svc.get_job_result(jid)["ready"])
        rec = store.get_status(jid)
        rec.update(status="completed", stage="done", result={"video_url": "https://huggingface.co/x"})
        store.put_status(jid, rec)
        out = svc.get_job_result(jid)
        self.assertTrue(out["ready"])
        self.assertEqual(out["result"]["video_url"], "https://huggingface.co/x")

    def test_list_jobs_filter_and_limit(self):
        svc, _, store = make_service()
        ids = [create(svc)["job_id"] for _ in range(3)]
        rec = store.get_status(ids[0]); rec["status"] = "completed"; store.put_status(ids[0], rec)
        self.assertEqual(svc.list_jobs(limit=2)["count"], 2)
        self.assertEqual([j["job_id"] for j in svc.list_jobs(status="completed")["jobs"]], [ids[0]])
        with self.assertRaises(ServiceError):
            svc.list_jobs(status="nonsense")


class RetryTests(unittest.TestCase):
    def _failed(self, svc, gh):
        jid = create(svc)["job_id"]
        gh.finish(1001, "failure")
        return jid

    def test_retry_failed_creates_new_attempt(self):
        svc, gh, store = make_service()
        jid = self._failed(svc, gh)
        out = svc.retry_job(jid)
        self.assertEqual(out["attempt"], 2)
        self.assertEqual(gh.dispatched[-1], (jid, 2))
        self.assertEqual(store.get_status(jid)["status"], "queued")

    def test_retry_refused_for_active_job(self):
        svc, _, _ = make_service()
        jid = create(svc)["job_id"]
        with self.assertRaises(ServiceError) as cm:
            svc.retry_job(jid)
        self.assertEqual(cm.exception.code, "not_retryable")

    def test_retry_refused_after_youtube_upload(self):
        svc, _, store = make_service()
        jid = create(svc)["job_id"]
        rec = store.get_status(jid)
        rec.update(status="failed", result={"youtube": {"state": "uploaded", "url": "https://youtu.be/abc"}})
        store.put_status(jid, rec)
        with self.assertRaises(ServiceError) as cm:
            svc.retry_job(jid)
        self.assertEqual(cm.exception.code, "duplicate_upload")

    def test_retry_limit(self):
        svc, gh, store = make_service()
        jid = create(svc)["job_id"]
        rec = store.get_status(jid); rec.update(status="failed", attempt=5); store.put_status(jid, rec)
        with self.assertRaises(ServiceError) as cm:
            svc.retry_job(jid)
        self.assertEqual(cm.exception.code, "retry_limit")


class CancelTests(unittest.TestCase):
    def test_cancel_confirmed_by_github(self):
        svc, gh, store = make_service()
        jid = create(svc)["job_id"]
        out = svc.cancel_job(jid)
        self.assertTrue(out["cancelled"])
        self.assertEqual(store.get_status(jid)["status"], "cancelled")

    def test_cancel_not_confirmed_is_not_reported_as_cancelled(self):
        svc, gh, store = make_service()
        gh.cancel_result = "stuck"
        jid = create(svc)["job_id"]
        out = svc.cancel_job(jid)
        self.assertFalse(out["cancelled"])
        self.assertTrue(out["cancel_requested"])
        self.assertNotEqual(store.get_status(jid)["status"], "cancelled")

    def test_cancel_without_run_mapping(self):
        svc, gh, _ = make_service()
        jid = create(svc)["job_id"]
        gh.runs.clear()
        out = svc.cancel_job(jid)
        self.assertFalse(out["cancelled"])
        self.assertFalse(out["cancel_requested"])
        self.assertEqual(gh.cancel_calls, [])

    def test_cancel_finished_job(self):
        svc, gh, _ = make_service()
        jid = create(svc)["job_id"]
        gh.finish(1001, "failure")
        out = svc.cancel_job(jid)
        self.assertFalse(out["cancelled"])
        self.assertEqual(gh.cancel_calls, [])


class SystemStatusTests(unittest.TestCase):
    def test_no_secrets_in_system_status(self):
        svc, _, _ = make_service()
        import json
        blob = json.dumps(svc.system_status())
        for secret in svc.s.secrets:
            self.assertNotIn(secret, blob)


if __name__ == "__main__":
    unittest.main()
