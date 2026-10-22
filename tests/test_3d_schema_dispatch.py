"""Test tương thích ngược của schema + dispatch cho project_type (không cần Blender)."""
import unittest

from pydantic import ValidationError

from app.github_client import GitHubClient
from app.schemas import Scene3DOptions, VideoJobRequest
from app.service import ServiceError
from tests.fakes import make_service

OLD_PAYLOAD = {  # payload y hệt trước khi có project_type
    "topic": "Vì sao bầu trời xanh", "style": "explainer", "duration_seconds": 30, "aspect_ratio": "16:9",
    "resolution": "720p", "language": "vi", "subtitle_enabled": True, "output_mode": "preview", "options": {},
}


def make3d(**over):
    base = {"topic": "Hình khối quay", "project_type": "animation_3d", "duration_seconds": 5, "resolution": "480p"}
    base.update(over)
    return base


class SchemaBackwardCompat(unittest.TestCase):
    def test_old_payload_still_valid_with_video_defaults(self):
        r = VideoJobRequest.model_validate(OLD_PAYLOAD)
        self.assertEqual((r.project_type, r.render_profile), ("video", "preview"))

    def test_unknown_fields_still_forbidden(self):
        with self.assertRaises(ValidationError):
            VideoJobRequest.model_validate({**OLD_PAYLOAD, "nonsense": 1})

    def test_unimplemented_project_types_rejected_not_faked(self):
        for pt in ("animation_2d", "hybrid", "3d"):
            with self.subTest(pt=pt), self.assertRaises(ValidationError):
                VideoJobRequest.model_validate({**OLD_PAYLOAD, "project_type": pt})

    def test_render_profile_only_for_3d(self):
        with self.assertRaises(ValidationError):
            VideoJobRequest.model_validate({**OLD_PAYLOAD, "render_profile": "standard"})

    def test_3d_defaults_and_options(self):
        r = VideoJobRequest.model_validate(make3d())
        self.assertEqual((Scene3DOptions.model_validate(r.options).fps, Scene3DOptions.model_validate(r.options).engine), (24, "cycles"))
        ok = VideoJobRequest.model_validate(make3d(options={"shape": "cone", "object_color": "#00ff88", "fps": 12, "export_frames": True}))
        self.assertEqual(ok.options["shape"], "cone")

    def test_3d_invalid_options_rejected_early(self):
        bad = [{"shape": "teapot"}, {"object_color": "red"}, {"background_color": "#12345"}, {"fps": 60},
               {"camera_move": "shake"}, {"engine": "luxrender"}, {"typo_field": 1}]
        for opts in bad:
            with self.subTest(opts=opts), self.assertRaises(ValidationError):
                VideoJobRequest.model_validate(make3d(options=opts))

    def test_3d_frame_budget_fail_fast(self):
        VideoJobRequest.model_validate(make3d(duration_seconds=10))                     # 240 frame: đúng giới hạn preview
        with self.assertRaises(ValidationError):
            VideoJobRequest.model_validate(make3d(duration_seconds=11))                 # 264 > 240 frame
        with self.assertRaises(ValidationError):  # 1080p yêu cầu profile standard
            VideoJobRequest.model_validate(make3d(resolution="1080p"))

    def test_3d_render_time_estimate_fail_fast(self):
        std12 = {"fps": 12}
        VideoJobRequest.model_validate(make3d(render_profile="standard", options=std12))        # ~700s <= 1000s
        with self.assertRaises(ValidationError) as cm:                                          # 120 frame x 11s => vượt ngân sách
            VideoJobRequest.model_validate(make3d(render_profile="standard"))
        self.assertIn("Ước lượng render", str(cm.exception))
        with self.assertRaises(ValidationError):                                                # 720p preview 10s@24fps quá nặng
            VideoJobRequest.model_validate(make3d(resolution="720p", duration_seconds=10))
        VideoJobRequest.model_validate(make3d(resolution="720p", duration_seconds=5, options=std12))  # 60 frame preview 720p
        with self.assertRaises(ValidationError):                                                # 1080p/standard không vừa runner miễn phí
            VideoJobRequest.model_validate(make3d(resolution="1080p", render_profile="standard", options=std12))

    def test_estimate_function_scales_with_pixels_and_engine(self):
        from app.schemas import estimate_render_seconds as est
        base = est("cycles", "preview", "16:9", "480p", 60)
        self.assertAlmostEqual(est("cycles", "preview", "16:9", "480p", 120) - base, 60 * 3.0)
        self.assertGreater(est("cycles", "preview", "16:9", "720p", 60), base * 1.5)
        self.assertGreater(est("eevee", "preview", "16:9", "480p", 60), base)
        self.assertLess(est("cycles", "preview", "1:1", "480p", 60), base)


class ServiceDispatch(unittest.TestCase):
    def test_video_job_request_json_and_dispatch_unchanged(self):
        svc, gh, store = make_service()
        jid = svc.create_job(OLD_PAYLOAD)["job_id"]
        stored = store.get_request(jid)
        self.assertNotIn("project_type", stored)     # worker cũ (extra=forbid) vẫn đọc được request.json
        self.assertNotIn("render_profile", stored)
        self.assertEqual(gh.dispatched, [(jid, 1)])
        self.assertEqual(gh.dispatched_types, ["video"])

    def test_3d_job_persisted_and_dispatched_with_type(self):
        svc, gh, store = make_service()
        out = svc.create_job(make3d(options={"shape": "sphere"}))
        jid = out["job_id"]
        self.assertEqual(out["project_type"], "animation_3d")
        self.assertEqual(store.get_request(jid)["project_type"], "animation_3d")
        self.assertEqual(store.get_request(jid)["options"], {"shape": "sphere"})
        self.assertEqual(gh.dispatched_types, ["animation_3d"])

    def test_invalid_3d_payload_never_dispatches(self):
        svc, gh, _ = make_service()
        with self.assertRaises(ServiceError) as cm:
            svc.create_job(make3d(duration_seconds=100))
        self.assertEqual(cm.exception.code, "invalid_payload")
        self.assertIn("frame", cm.exception.message)
        self.assertEqual(gh.dispatched, [])

    def test_retry_keeps_project_type(self):
        svc, gh, _ = make_service()
        jid = svc.create_job(make3d())["job_id"]
        gh.finish(1001, "failure")
        self.assertEqual(svc.retry_job(jid)["attempt"], 2)
        self.assertEqual(gh.dispatched[-1], (jid, 2))
        self.assertEqual(gh.dispatched_types[-1], "animation_3d")

    def test_retry_of_legacy_job_without_type_is_video(self):
        svc, gh, store = make_service()
        jid = svc.create_job(OLD_PAYLOAD)["job_id"]
        gh.finish(1001, "failure")
        svc.retry_job(jid)
        self.assertEqual(gh.dispatched_types[-1], "video")

    def test_cancel_and_status_work_for_3d_job(self):
        svc, gh, _ = make_service()
        jid = svc.create_job(make3d())["job_id"]
        self.assertEqual(svc.get_job_status(jid)["status"], "queued")
        self.assertTrue(svc.cancel_job(jid)["cancelled"])
        self.assertEqual(svc.get_job_status(jid)["status"], "cancelled")


class FakeResp:
    status_code = 204
    headers: dict = {}


class FakeSession:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw.get("json")))
        return FakeResp()


class GitHubClientDispatch(unittest.TestCase):
    def test_dispatch_inputs(self):
        sess = FakeSession()
        gh = GitHubClient("ghp_x" * 4, "o/r", "render.yml", "feat/3d-engine", session=sess)
        gh.dispatch("job_20261022_000000_abcdef", 1)
        gh.dispatch("job_20261022_000000_abcdef", 2, project_type="animation_3d")
        (_m, url, body1), (_m2, _u2, body2) = sess.calls
        self.assertTrue(url.endswith("/repos/o/r/actions/workflows/render.yml/dispatches"))
        # job video: payload y hệt cũ (không input lạ -> workflow chưa có input project_type vẫn nhận được)
        self.assertEqual(body1, {"ref": "feat/3d-engine", "inputs": {"job_id": "job_20261022_000000_abcdef", "attempt": "1"}})
        self.assertEqual(body2["inputs"], {"job_id": "job_20261022_000000_abcdef", "attempt": "2", "project_type": "animation_3d"})


if __name__ == "__main__":
    unittest.main()
