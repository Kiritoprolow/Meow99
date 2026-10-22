"""Hồi quy: job_20261022_101818_214dc5. Client đặt project_type/render_profile vào `options`; job bị chạy như video
thường (slideshow gradient) và QC báo PASS. Giờ phải bị từ chối rõ ràng, và tool schema phải expose đúng tham số cấp cao nhất."""
import asyncio
import json
import shutil
import unittest
from pathlib import Path

from app.mcp_server.server import build_mcp
from app.service import ServiceError
from tests.fakes import make_service
from video_engine.pipeline import run_job
from video_engine.storage.outputs import LocalStorage

REAL_PAYLOAD = {  # đúng payload của job lỗi (rút gọn topic)
    "topic": "Test Blender 3D: khối cầu kim loại", "duration_seconds": 5, "aspect_ratio": "16:9", "resolution": "480p",
    "subtitle_enabled": False, "music_enabled": False, "visual_style": "gradient", "output_mode": "preview",
    "options": {"project_type": "animation_3d", "render_profile": "eevee", "preview_only": True},
}


class MisplacedOptionsRejected(unittest.TestCase):
    def test_exact_failed_payload_is_rejected_and_never_dispatched(self):
        svc, gh, store = make_service()
        with self.assertRaises(ServiceError) as cm:
            svc.create_job(REAL_PAYLOAD)
        self.assertEqual(cm.exception.code, "invalid_payload")
        self.assertIn("CẤP CAO NHẤT", cm.exception.message)
        self.assertIn("project_type", cm.exception.message)
        self.assertEqual(gh.dispatched, [])
        self.assertEqual(list(store.list_job_ids()), [])           # không tạo job rác

    def test_each_3d_only_key_in_video_options_is_rejected(self):
        for key in ("project_type", "render_profile", "shape", "camera_move", "object_color", "export_frames"):
            svc, gh, _ = make_service()
            with self.subTest(key=key), self.assertRaises(ServiceError):
                svc.create_job({"topic": "x", "options": {key: "animation_3d"}})
            self.assertEqual(gh.dispatched, [])

    def test_misplaced_keys_inside_3d_options_rejected_with_hint(self):
        svc, gh, _ = make_service()
        bad = {**REAL_PAYLOAD, "project_type": "animation_3d", "options": {"fps": 12, "render_profile": "preview"}}
        with self.assertRaises(ServiceError) as cm:
            svc.create_job(bad)
        self.assertIn("CẤP CAO NHẤT", cm.exception.message)
        self.assertEqual(gh.dispatched, [])

    def test_correct_top_level_payload_runs_as_3d(self):
        svc, gh, store = make_service()
        out = svc.create_job({"topic": "Quả cầu", "project_type": "animation_3d", "render_profile": "preview",
                              "duration_seconds": 5, "resolution": "480p",
                              "options": {"engine": "cycles", "shape": "sphere", "camera_move": "orbit", "fps": 12}})
        self.assertEqual(out["project_type"], "animation_3d")
        stored = store.get_request(out["job_id"])
        self.assertEqual((stored["project_type"], stored["render_profile"]), ("animation_3d", "preview"))
        self.assertNotIn("project_type", stored["options"])
        self.assertEqual(gh.dispatched_types, ["animation_3d"])

    def test_ordinary_video_options_still_accepted(self):
        svc, gh, _ = make_service()
        svc.create_job({"topic": "x", "options": {"note": "bất kỳ"}})
        self.assertEqual(gh.dispatched_types, ["video"])


class ToolSchemaExposesProjectType(unittest.TestCase):
    def test_create_video_job_schema(self):
        svc, _gh, _store = make_service()
        tools = asyncio.run(build_mcp(svc).list_tools())
        tool = next(t for t in tools if t.name == "create_video_job")
        schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema")
        props = schema["properties"]
        self.assertEqual(set(props["project_type"].get("enum", [])), {"video", "animation_3d"})
        self.assertEqual(set(props["render_profile"].get("enum", [])), {"preview", "standard"})
        self.assertNotIn("project_type", schema.get("required", []))   # tương thích ngược: vẫn tùy chọn
        self.assertEqual(schema.get("required"), ["topic"])
        self.assertIn("options", props)


@unittest.skipUnless(shutil.which("ffmpeg"), "cần ffmpeg")
class WorkerNeverFakes3D(unittest.TestCase):
    def setUp(self):
        self.svc, self.gh, self.store = make_service()
        self.root = Path(self.svc.s.local_store_dir)

    def test_old_style_request_json_fails_instead_of_slideshow(self):
        """request.json kiểu job lỗi (tạo bởi gateway cũ) đã nằm trong store: worker mới phải báo lỗi, không render gradient."""
        ok = self.svc.create_job({"topic": "x"})["job_id"]
        raw = self.store.get_request(ok)
        (self.root / "jobs" / ok / "request.json").write_text(
            json.dumps({**raw, "options": REAL_PAYLOAD["options"]}), encoding="utf-8")
        rec = run_job(ok, 1, self.store, LocalStorage(self.root), env={})
        self.assertEqual((rec["status"], rec["stage"]), ("failed", "loading_request"))
        self.assertIn("CẤP CAO NHẤT", rec["error"])
        self.assertFalse((self.root / "outputs").exists())

    def test_video_job_result_states_engine_and_still_works(self):
        jid = self.svc.create_job({"topic": "Video thường vẫn chạy", "duration_seconds": 5, "resolution": "480p"})["job_id"]
        rec = run_job(jid, 1, self.store, LocalStorage(self.root), env={})
        self.assertEqual(rec["status"], "completed", rec.get("error"))
        self.assertEqual((rec["result"]["project_type"], rec["result"]["engine"]), ("video", "slideshow"))
        self.assertTrue(rec["result"]["qc"]["ok"])
        self.assertNotIn("scene_blend_url", rec["result"])


if __name__ == "__main__":
    unittest.main()
