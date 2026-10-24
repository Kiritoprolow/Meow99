"""Engine Blender 3D: dựng cảnh bằng bpy (scripts/scene_basic.py chạy TRONG Blender), render PNG sequence,
ghép MP4 bằng FFmpeg, lưu .blend. Toàn bộ logic Blender nằm ở đây và trong blender_scripts/, không lẫn vào slideshow.

Giới hạn có chủ đích (ghi rõ để không hiểu nhầm):
  * Cảnh là PROCEDURAL (vật thể nguyên thủy + camera + đèn); không nhập mô hình/rig/ảnh của người dùng.
  * Video đầu ra KHÔNG có audio (chưa có pipeline âm thanh).
  * Render CPU (runner miễn phí không có GPU). EEVEE chạy qua OpenGL phần mềm (Mesa/EGL) nên chậm hơn Cycles ở cảnh nhỏ.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any, Mapping

from PIL import Image

from app.schemas import (MAX_3D_FRAMES, MAX_3D_FRAMES_1080P, RENDER_BUDGET_SECONDS, Scene3DOptions,
                         estimate_render_seconds)
from video_engine.engines.base import EngineOutput, RenderContext, StageError
from video_engine.renderer.ffmpeg_renderer import run as run_cmd

logger = logging.getLogger("video_engine.blender_3d")
SCENE_SCRIPT = Path(__file__).resolve().parent / "blender_scripts" / "scene_basic.py"
MIN_BLENDER = (4, 2)                    # EEVEE Next ổn định từ 4.2; script đã thử trên 5.2 LTS
SAMPLES = {("cycles", "preview"): 12, ("cycles", "standard"): 32,   # cycles: denoise tự bật từ 32 mẫu
           ("eevee", "preview"): 4, ("eevee", "standard"): 16}
DEFAULT_RENDER_TIMEOUT = 1200           # giây; thấp hơn timeout-minutes=30 của workflow để còn thời gian encode/upload
MAX_FRAMES_ZIP_BYTES = 150 * 1024 * 1024
_SECRET_HINTS = ("TOKEN", "SECRET", "KEY", "PASSWORD")
_VERSION_RE = re.compile(r"Blender\s+(\d+)\.(\d+)(?:\.(\d+))?")


# Màu mặc định (thân, nền) theo shape; thỏ dùng tông kem + nền cam theo ảnh tham khảo
DEFAULT_COLORS = {"rabbit": ("#fbecb5", "#f9a35b")}
GENERIC_COLORS = ("#e4572e", "#1b2a41")


def resolve_colors(shape: str, object_color: str | None, background_color: str | None) -> tuple[str, str]:
    obj_default, bg_default = DEFAULT_COLORS.get(shape, GENERIC_COLORS)
    return object_color or obj_default, background_color or bg_default


def find_blender(env: Mapping[str, str]) -> str:
    path = env.get("BLENDER_BIN") or shutil.which("blender")
    if not path or not Path(path).exists():
        raise StageError("blender_check", "Không tìm thấy Blender: đặt BLENDER_BIN hoặc cài 'blender' vào PATH "
                                          "(workflow chỉ cài Blender khi project_type=animation_3d)")
    return path


def blender_version(binary: str) -> tuple[tuple[int, int, int], str]:
    try:
        p = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StageError("blender_check", f"Không chạy được Blender --version: {type(exc).__name__}") from None
    m = _VERSION_RE.search(p.stdout or "")
    if p.returncode != 0 or not m:
        raise StageError("blender_check", "Blender --version thất bại hoặc đầu ra không nhận diện được")
    ver = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    if ver[:2] < MIN_BLENDER:
        raise StageError("blender_check", f"Blender {ver[0]}.{ver[1]} quá cũ (cần >= {MIN_BLENDER[0]}.{MIN_BLENDER[1]})")
    return ver, m.group(0)


def _child_env(base: Mapping[str, str], home: Path) -> dict[str, str]:
    """Môi trường tối thiểu cho Blender: không chuyển token/secret sang tiến trình con."""
    env = {k: v for k, v in base.items() if not any(h in k.upper() for h in _SECRET_HINTS)}
    env["HOME"] = str(home)  # Blender ghi cache/config vào HOME; cô lập theo job
    return env


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _tail(path: Path, lines: int = 12) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    keep = [ln for ln in text.splitlines() if ln.strip()][-lines:]
    return "\n".join(keep)


class Blender3DEngine:
    name = "blender_3d"

    # ------------------------------------------------------------------ chạy Blender
    def _run_blender(self, binary: str, cfg_path: Path, log_path: Path, workdir: Path, timeout: int,
                     env: Mapping[str, str]) -> int:
        home = workdir / "home"
        home.mkdir(exist_ok=True)
        cmd = [binary, "-b", "--factory-startup", "--python-exit-code", "3",
               "--python", str(SCENE_SCRIPT), "--", "--config", str(cfg_path)]
        with open(log_path, "wb") as lf:
            proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, cwd=workdir,
                                    env=_child_env(env, home), start_new_session=True)
            try:
                return proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_group(proc)
                raise StageError("render_3d", f"Blender vượt quá {timeout}s nên bị dừng (fail-fast). "
                                              "Giảm duration_seconds, fps, resolution hoặc dùng engine=cycles") from None
            except BaseException:  # Cancelled (SIGTERM), KeyboardInterrupt...: không để Blender mồ côi
                _kill_group(proc)
                raise

    # ------------------------------------------------------------------ engine API
    def render(self, ctx: RenderContext) -> EngineOutput:
        r, wd = ctx.request, ctx.workdir
        profile = r.get("render_profile", "preview")
        try:
            opts = Scene3DOptions.model_validate(r.get("options") or {})
        except ValueError as exc:
            raise StageError("render_3d", f"options không hợp lệ cho animation_3d: {exc}") from None
        fps = opts.fps
        frames = int(r["duration_seconds"]) * fps
        cap = MAX_3D_FRAMES[profile]
        if r["resolution"] == "1080p":
            cap = min(cap, MAX_3D_FRAMES_1080P)
        if frames > cap:  # kiểm tra lại ở worker (phòng request tạo tay, bỏ qua gateway)
            raise StageError("render_3d", f"{frames} frame vượt giới hạn {cap} của profile {profile}")
        est = estimate_render_seconds(opts.engine, profile, r["aspect_ratio"], r["resolution"], frames)
        if est > RENDER_BUDGET_SECONDS:
            raise StageError("render_3d", f"Ước lượng render ~{est:.0f}s vượt ngân sách {RENDER_BUDGET_SECONDS:.0f}s (fail-fast)")

        ctx.progress("blender_check")
        binary = find_blender(ctx.env)
        ver, ver_text = blender_version(binary)
        logger.info("Blender: %s | %d frame @ %d fps, %dx%d, engine=%s, profile=%s", ver_text, frames, fps, *ctx.size, opts.engine, profile)

        frames_dir = wd / "frames"
        blend_path = wd / "scene.blend"
        w, h = ctx.size
        timeout = int(ctx.env.get("BLENDER_RENDER_TIMEOUT", DEFAULT_RENDER_TIMEOUT))

        def attempt(engine: str) -> tuple[int, Path]:
            if frames_dir.exists():
                shutil.rmtree(frames_dir)
            frames_dir.mkdir()
            obj_color, bg_color = resolve_colors(opts.shape, opts.object_color, opts.background_color)
            cfg = {"frames_dir": str(frames_dir), "blend_path": str(blend_path), "width": w, "height": h, "fps": fps,
                   "frames": frames, "shape": opts.shape, "object_color": obj_color,
                   "background_color": bg_color, "camera_move": opts.camera_move, "engine": engine,
                   "samples": SAMPLES[(engine, profile)]}
            if opts.shape == "rabbit":
                cfg["ground_color"] = bg_color  # sàn cùng tông nền để nhân vật đứng trên nền liền mạch
            cfg_path = wd / f"scene_config_{engine}.json"
            cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            log_path = wd / f"blender_{engine}.log"
            return self._run_blender(binary, cfg_path, log_path, wd, timeout, ctx.env), log_path

        ctx.progress("render_3d")
        t0 = time.monotonic()
        used, fallback = opts.engine, None
        rc, log = attempt(used)
        if rc != 0 and used == "eevee":
            # EEVEE cần OpenGL; nếu runner không khởi tạo được thì thử Cycles (CPU) đúng một lần và ghi rõ vào báo cáo
            fallback = {"from": "eevee", "to": "cycles", "reason": f"Blender exit={rc}", "log_tail": _tail(log, 6)}
            used = "cycles"
            rc, log = attempt(used)
        if rc != 0:
            raise StageError("render_3d", f"Blender lỗi (exit={rc}, engine={used}):\n{_tail(log)}")
        render_seconds = round(time.monotonic() - t0, 2)
        logger.info("Blender render xong: engine=%s, %d frame trong %.1fs (%.2f s/frame), scene.blend đã lưu", used, frames, render_seconds, render_seconds / frames)

        missing = [i for i in range(1, frames + 1)
                   if not (frames_dir / f"frame_{i:04d}.png").exists() or (frames_dir / f"frame_{i:04d}.png").stat().st_size == 0]
        if missing:
            raise StageError("render_3d", f"Thiếu {len(missing)}/{frames} frame sau khi render (ví dụ #{missing[0]})")
        if not blend_path.exists() or blend_path.stat().st_size == 0:
            raise StageError("render_3d", "Blender không lưu được file scene.blend")

        ctx.progress("encode")
        video = wd / "video.mp4"
        run_cmd(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(frames_dir / "frame_%04d.png"),
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(fps),
                 "-movflags", "+faststart", str(video)])

        notes = ["animation_3d chưa có audio: video không có track âm thanh; music_enabled/voice_id/subtitle_enabled không áp dụng",
                 "Cảnh 3D là cảnh mẫu procedural (vật thể nguyên thủy + camera + đèn), chưa nhập mô hình/rig của người dùng"]
        if fallback:
            notes.append("EEVEE không chạy được trên runner này nên đã tự chuyển sang Cycles (CPU)")

        files: dict[str, Path] = {"scene.blend": blend_path}
        zip_path = wd / "frames.zip"
        total = sum(p.stat().st_size for p in frames_dir.glob("frame_*.png"))
        if opts.export_frames:  # mặc định KHÔNG đóng gói: video + scene.blend đã đủ, tránh tốn dung lượng và thời gian upload
            if total <= MAX_FRAMES_ZIP_BYTES:
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:  # PNG đã nén sẵn
                    for i in range(1, frames + 1):
                        zf.write(frames_dir / f"frame_{i:04d}.png", f"frame_{i:04d}.png")
                files["frames.zip"] = zip_path
            else:
                notes.append(f"PNG sequence ({total // (1024 * 1024)} MB) vượt {MAX_FRAMES_ZIP_BYTES // (1024 * 1024)} MB nên không đóng gói frames.zip")

        thumb = None
        if r["thumbnail_enabled"]:
            ctx.progress("thumbnail")
            thumb = wd / "thumbnail.jpg"
            with Image.open(frames_dir / f"frame_{max(frames // 2, 1):04d}.png") as im:
                im = im.convert("RGB")
                im.thumbnail((1280, 1280))
                im.save(thumb, "JPEG", quality=88)

        report: dict[str, Any] = {
            "blender": {"version": ".".join(map(str, ver)), "version_text": ver_text, "engine": used,
                        "samples": SAMPLES[(used, profile)], "render_profile": profile, "frames": frames, "fps": fps,
                        "render_seconds": render_seconds, "estimated_seconds": round(est, 1),
                        "seconds_per_frame": round(render_seconds / frames, 2), "fallback": fallback},
            "scene": opts.model_dump(),
            "artifacts": {"png_sequence": "frames.zip" if "frames.zip" in files else None, "blend": "scene.blend"},
        }
        overrides: dict[str, Any] = {
            "audio": "none", "subtitle_timing": None, "engine": self.name, "project_type": "animation_3d",
            "blender_version": report["blender"]["version"],
            "assets": {"visuals": "procedural_blender_scene", "music": None, "licenses": "no third-party assets"},
        }
        return EngineOutput(video=video, script_source="procedural", files=files, thumbnail=thumb, segments=None,
                            notes=notes, report=report, metadata_overrides=overrides,
                            qc_options={"require_audio": False, "check_motion": True, "expected_frames": frames,
                                        "expected_fps": float(fps)})
