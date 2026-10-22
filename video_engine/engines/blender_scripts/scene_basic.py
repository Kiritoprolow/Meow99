"""Dựng cảnh 3D, tạo animation, lưu .blend và render PNG sequence. CHẠY BÊN TRONG BLENDER (bpy), không import code của repo.

    blender -b --factory-startup --python-exit-code 3 --python scene_basic.py -- --config config.json

Config JSON (do video_engine.engines.blender_3d ghi):
    frames_dir, blend_path, width, height, fps, frames, shape, object_color, background_color,
    camera_move, engine (eevee|cycles), samples

Cảnh: nền phẳng, vật thể chính (nảy + xoay), vệ tinh nhỏ quay quanh vật thể, đèn mặt trời + môi trường,
camera luôn nhìn vào vật thể (Track To) và chuyển động (orbit quanh cảnh hoặc dolly tiến lại gần).
Mọi chuyển động là keyframe thật trong file .blend nên người dùng mở bằng Blender vẫn chỉnh sửa tiếp được.
"""
import json
import math
import sys
import time
import traceback

import bpy

PI2 = math.tau
FOCUS = (0.0, 0.0, 1.2)  # điểm camera nhìn vào


def parse_config() -> dict:
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    if len(argv) != 2 or argv[0] != "--config":
        raise SystemExit("usage: -- --config <file.json>")
    with open(argv[1], encoding="utf-8") as fh:
        return json.load(fh)


def hex_rgba(value: str, alpha: float = 1.0) -> tuple:
    h = value.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    # chuyển sRGB -> linear để màu hiển thị đúng như mã hex
    lin = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return (lin(r), lin(g), lin(b), alpha)


def make_material(name: str, color: tuple, roughness: float, metallic: float = 0.0):
    mat = bpy.data.materials.new(name)
    try:
        mat.use_nodes = True  # Blender >= 5.0 coi use_nodes là deprecated nhưng vẫn chấp nhận
    except Exception:  # noqa: BLE001
        pass
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is None:
        raise RuntimeError("Không tìm thấy node Principled BSDF")
    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def add_shape(kind: str):
    if kind == "cube":
        bpy.ops.mesh.primitive_cube_add(size=2.0)
    elif kind == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, segments=48, ring_count=24)
    elif kind == "torus":
        bpy.ops.mesh.primitive_torus_add(major_radius=1.0, minor_radius=0.38, major_segments=48, minor_segments=24)
    elif kind == "cone":
        bpy.ops.mesh.primitive_cone_add(radius1=1.0, depth=2.0, vertices=48)
    else:
        raise RuntimeError(f"shape không hỗ trợ: {kind}")
    obj = bpy.context.active_object
    if kind != "cube":
        bpy.ops.object.shade_smooth()
    return obj


def set_background(cfg: dict) -> None:
    world = bpy.data.worlds.new("World")
    try:
        world.use_nodes = True
    except Exception:  # noqa: BLE001
        pass
    bg = world.node_tree.nodes.get("Background")
    if bg is None:
        raise RuntimeError("Không tìm thấy node Background của World")
    bg.inputs[0].default_value = hex_rgba(cfg["background_color"])
    bg.inputs[1].default_value = 1.0
    bpy.context.scene.world = world


def build_scene(cfg: dict) -> dict:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.name = "AIVideoStudio3D"
    n = int(cfg["frames"])
    fps = int(cfg["fps"])
    scene.render.fps, scene.render.fps_base = fps, 1.0
    scene.frame_start, scene.frame_end = 1, n

    set_background(cfg)

    # nền
    bpy.ops.mesh.primitive_plane_add(size=40.0, location=(0, 0, 0))
    ground = bpy.context.active_object
    ground.name = "Ground"
    ground.data.materials.append(make_material("GroundMat", hex_rgba("#9aa3ad"), roughness=0.9))

    # vật thể chính
    main = add_shape(cfg["shape"])
    main.name = "MainObject"
    main.location = (0, 0, 1.0)
    main.data.materials.append(make_material("MainMat", hex_rgba(cfg["object_color"]), roughness=0.35, metallic=0.15))

    # vệ tinh quay quanh vật thể chính: empty xoay + quả cầu con
    pivot = bpy.data.objects.new("SatellitePivot", None)
    scene.collection.objects.link(pivot)
    pivot.location = (0, 0, 1.6)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.3, segments=32, ring_count=16, location=(0, 0, 0))
    sat = bpy.context.active_object
    sat.name = "Satellite"
    bpy.ops.object.shade_smooth()
    sat.parent = pivot
    sat.location = (2.6, 0, 0)
    sat.data.materials.append(make_material("SatMat", hex_rgba("#f5f5f5"), roughness=0.25))

    # đèn
    sun_data = bpy.data.lights.new("Sun", "SUN")
    sun_data.energy = 3.5
    sun = bpy.data.objects.new("Sun", sun_data)
    scene.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(50), math.radians(10), math.radians(35))

    # camera + mục tiêu nhìn
    target = bpy.data.objects.new("CameraTarget", None)
    scene.collection.objects.link(target)
    target.location = FOCUS
    rig = bpy.data.objects.new("CameraRig", None)
    scene.collection.objects.link(rig)
    cam_data = bpy.data.cameras.new("Camera")
    cam_data.lens = 40
    cam = bpy.data.objects.new("Camera", cam_data)
    scene.collection.objects.link(cam)
    cam.parent = rig
    cam.location = (0, -9.0, 3.2)
    track = cam.constraints.new("TRACK_TO")
    track.target = target
    track.track_axis, track.up_axis = "TRACK_NEGATIVE_Z", "UP_Y"
    scene.camera = cam
    return {"scene": scene, "main": main, "pivot": pivot, "rig": rig, "cam": cam, "n": n, "fps": fps}


def animate(cfg: dict, parts: dict) -> None:
    """Keyframe thật cho vật thể và camera. Nội suy LINEAR để quỹ đạo quay đều; nảy tính theo công thức mỗi frame."""
    bpy.context.preferences.edit.keyframe_new_interpolation_type = "LINEAR"
    n, fps = parts["n"], parts["fps"]
    main, pivot, rig, cam = parts["main"], parts["pivot"], parts["rig"], parts["cam"]
    span = max(n - 1, 1)

    for f in range(1, n + 1):
        t = (f - 1) / fps  # giây
        u = (f - 1) / span  # 0..1 trên toàn clip
        # vật thể chính: nảy 1 lần/giây + xoay 0.5 vòng/giây quanh trục Z
        main.location[2] = 1.0 + 1.3 * abs(math.sin(math.pi * t))
        main.rotation_euler[2] = PI2 * 0.5 * t
        main.keyframe_insert(data_path="location", index=2, frame=f)
        main.keyframe_insert(data_path="rotation_euler", index=2, frame=f)
        # vệ tinh quay quanh vật thể: 1 vòng / 3 giây
        pivot.rotation_euler[2] = PI2 * t / 3.0
        pivot.keyframe_insert(data_path="rotation_euler", index=2, frame=f)
        # camera
        if cfg["camera_move"] == "orbit":
            rig.rotation_euler[2] = math.radians(100.0) * u  # quét 100 độ quanh cảnh
            rig.keyframe_insert(data_path="rotation_euler", index=2, frame=f)
        else:  # dolly: tiến lại gần và hạ thấp dần
            cam.location[1] = -12.0 + 5.5 * u
            cam.location[2] = 4.2 - 1.6 * u
            cam.keyframe_insert(data_path="location", index=1, frame=f)
            cam.keyframe_insert(data_path="location", index=2, frame=f)


def configure_render(cfg: dict, scene) -> str:
    r = scene.render
    r.resolution_x, r.resolution_y, r.resolution_percentage = int(cfg["width"]), int(cfg["height"]), 100
    r.image_settings.file_format = "PNG"
    r.image_settings.color_mode = "RGB"
    r.image_settings.compression = 5
    r.use_file_extension = True
    scene.view_settings.view_transform = "Standard"
    samples = int(cfg["samples"])
    available = {i.identifier for i in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    if cfg["engine"] == "cycles":
        r.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = samples
        scene.cycles.use_denoising = False if samples >= 64 else True
        used = "CYCLES"
    else:
        # Blender 4.2-4.5 gọi là BLENDER_EEVEE_NEXT, 5.x quay lại BLENDER_EEVEE
        used = next((e for e in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT") if e in available), None)
        if used is None:
            raise RuntimeError(f"Không có EEVEE trong build này (engines={sorted(available)})")
        r.engine = used
        scene.eevee.taa_render_samples = samples
    return used


def main() -> None:
    cfg = parse_config()
    t0 = time.time()
    parts = build_scene(cfg)
    animate(cfg, parts)
    used = configure_render(cfg, parts["scene"])
    print(f"SCENE_READY engine={used} frames={parts['n']} fps={parts['fps']} size={cfg['width']}x{cfg['height']}", flush=True)

    bpy.ops.wm.save_as_mainfile(filepath=cfg["blend_path"], compress=True)  # lưu trước khi render
    print(f"BLEND_SAVED {cfg['blend_path']}", flush=True)

    def on_frame(scene, *_a):
        print(f"FRAME_DONE {scene.frame_current}/{scene.frame_end} t={time.time() - t0:.1f}s", flush=True)

    bpy.app.handlers.render_post.append(on_frame)
    parts["scene"].render.filepath = cfg["frames_dir"].rstrip("/") + "/frame_"
    bpy.ops.render.render(animation=True)
    print(f"RENDER_DONE frames={parts['n']} seconds={time.time() - t0:.1f}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(3)
