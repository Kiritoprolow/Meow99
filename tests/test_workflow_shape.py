"""render.yml: Blender chỉ được cài cho animation_3d; job video cũ không bị ảnh hưởng."""
import re
import unittest
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "render.yml"


class WorkflowShape(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.wf = yaml.safe_load(cls.text)
        cls.sha = re.search(r'BLENDER_SHA256: "([0-9a-f]+)"', cls.text)

    def test_inputs_backward_compatible(self):
        inputs = self.wf[True]["workflow_dispatch"]["inputs"]  # PyYAML đọc khóa `on` thành True
        self.assertEqual({k for k in inputs if inputs[k]["required"]}, {"job_id"})  # chỉ job_id bắt buộc như trước
        self.assertEqual(inputs["project_type"]["default"], "video")

    def test_blender_step_is_conditional_and_pinned(self):
        steps = self.wf["jobs"]["render"]["steps"]
        blender = [s for s in steps if "Blender" in (s.get("name") or "")]
        self.assertEqual(len(blender), 1)
        self.assertEqual(blender[0]["if"], "inputs.project_type == 'animation_3d'")
        self.assertIsNotNone(self.sha)
        self.assertEqual(len(self.sha.group(1)), 64)  # hash ghim cứng
        self.assertIn("sha256sum -c", blender[0]["run"])
        self.assertTrue(re.fullmatch("[0-9]+[.][0-9]+[.][0-9]+", self.wf["env"]["BLENDER_VERSION"]))

    def test_no_untrusted_input_interpolated_into_shell(self):
        for step in self.wf["jobs"]["render"]["steps"]:
            self.assertNotIn("${{ inputs", step.get("run", ""), step.get("name"))

    def test_run_name_still_maps_job_id(self):
        self.assertIn("inputs.job_id", self.wf["run-name"])
        self.assertIn("inputs.attempt", self.wf["run-name"])


class WorkflowNetworkResilience(unittest.TestCase):
    def _step(self, word):
        wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        return next(s for s in wf["jobs"]["render"]["steps"] if word in (s.get("name") or ""))

    def test_apt_step_cannot_hang_silently(self):
        step = self._step("FFmpeg")
        self.assertLessEqual(step["timeout-minutes"], 15)
        self.assertIn("Acquire::http::Timeout", step["run"])
        self.assertIn("sudo timeout", step["run"])       # mỗi lần apt có giới hạn thời gian
        self.assertIn("for attempt in 1 2 3", step["run"])  # và được thử lại
        self.assertIn("exit 1", step["run"])               # hết lượt thì fail rõ ràng

    def test_blender_download_has_time_limit(self):
        self.assertIn("--max-time", self._step("Blender")["run"])


if __name__ == "__main__":
    unittest.main()
