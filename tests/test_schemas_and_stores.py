"""Unit test: schema payload, job store (local + Hub với session giả)."""
import json
import tempfile
import unittest

from pydantic import ValidationError

from app.job_store import HubJobStore, JobStoreError, LocalJobStore
from app.schemas import VideoJobRequest


class SchemaTests(unittest.TestCase):
    def test_defaults_are_safe(self):
        r = VideoJobRequest(topic="Vì sao bầu trời xanh")
        self.assertEqual(r.output_mode, "preview")
        self.assertEqual(r.youtube_privacy, "private")
        self.assertEqual(r.aspect_ratio, "16:9")

    def test_invalid_payloads(self):
        bad = [
            {},                                              # thiếu topic
            {"topic": ""},
            {"topic": "x", "aspect_ratio": "4:3"},
            {"topic": "x", "duration_seconds": 2},
            {"topic": "x", "unknown_field": 1},              # extra=forbid
            {"topic": "x", "options": {"a": "x" * 5000}},    # options quá lớn
            {"topic": "x", "output_mode": "publish", "youtube_privacy": "public"},  # thiếu confirm_public
        ]
        for p in bad:
            with self.subTest(p=str(p)[:60]):
                with self.assertRaises(ValidationError):
                    VideoJobRequest.model_validate(p)

    def test_public_with_confirmation_ok(self):
        r = VideoJobRequest(topic="x", output_mode="publish", youtube_privacy="public", confirm_public=True)
        self.assertTrue(r.confirm_public)


class LocalStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = LocalJobStore(tempfile.mkdtemp())
        self.jid = "job_20261020_120000_abcdef"

    def test_roundtrip_and_no_overwrite_of_request(self):
        self.store.put_request(self.jid, {"a": 1})
        with self.assertRaises(JobStoreError):
            self.store.put_request(self.jid, {"a": 2})
        self.assertEqual(self.store.get_request(self.jid), {"a": 1})
        self.store.put_status(self.jid, {"s": 1})
        self.store.put_status(self.jid, {"s": 2})
        self.assertEqual(self.store.get_status(self.jid), {"s": 2})
        self.assertEqual(self.store.list_job_ids(), [self.jid])

    def test_rejects_path_traversal_ids(self):
        for bad in ("../etc/passwd", "job_x", "job_20261020_120000_ABCDEF", ""):
            with self.assertRaises(JobStoreError):
                self.store.get_status(bad)


class FakeResp:
    def __init__(self, code, body=b"", js=None):
        self.status_code, self.content, self._js = code, body, js

    def json(self):
        return self._js


class FakeSession:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def get(self, url, headers=None, timeout=None, **kw):
        self.calls.append((url, headers))
        return self.resp


class HubStoreReadTests(unittest.TestCase):
    """Chỉ kiểm tra đường ĐỌC (HTTP giả). Đường ghi cần huggingface_hub + Hub thật — xem docs/TESTING.md."""

    jid = "job_20261020_120000_abcdef"

    def test_read_ok_and_404_and_403(self):
        ok = HubJobStore("hf_x" * 5, "u/state", FakeSession(FakeResp(200, json.dumps({"k": "v"}).encode())))
        self.assertEqual(ok.get_status(self.jid), {"k": "v"})
        self.assertIsNone(HubJobStore("hf_x" * 5, "u/state", FakeSession(FakeResp(404))).get_status(self.jid))
        with self.assertRaises(JobStoreError):
            HubJobStore("hf_x" * 5, "u/state", FakeSession(FakeResp(403))).get_status(self.jid)

    def test_list_filters_non_job_entries(self):
        entries = [{"path": f"jobs/{self.jid}"}, {"path": "jobs/README.md"}, {"path": "jobs/../x"}]
        st = HubJobStore("hf_x" * 5, "u/state", FakeSession(FakeResp(200, js=entries)))
        self.assertEqual(st.list_job_ids(), [self.jid])

    def test_token_sent_only_as_header(self):
        sess = FakeSession(FakeResp(404))
        HubJobStore("hf_secret_token_value", "u/state", sess).get_status(self.jid)
        url, headers = sess.calls[0]
        self.assertNotIn("hf_secret_token_value", url)
        self.assertIn("Bearer", headers["Authorization"])


if __name__ == "__main__":
    unittest.main()
