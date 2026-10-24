'''Nhân vật thỏ chibi dựng thủ công từ hình khối (ellipsoid) trong Blender. CHẠY TRONG BLENDER, được scene_basic.py import.

Đây là mô hình dựng tay theo ảnh tham khảo của người dùng (thân tròn bầu, hai tai dài có ruột hồng, mắt chấm đen, má hồng,
miệng nhỏ, tay chân ngắn, đuôi trắng): KHÔNG phải mô hình chính thức, không được trích xuất từ ảnh tự động.
Tỉ lệ lấy từ ảnh tham khảo: thân rộng ~ cao, tai nhô lên khoảng 0.47 chiều cao thân, mắt ở ~29% từ đỉnh thân.
'''
import math

import bpy

A, B, C, Z0 = 1.0, 0.95, 0.98, 1.0   # bán kính thân theo x, y, z và độ cao tâm thân
EAR_TILT = math.radians(8)
PALETTE = {'inner_ear': '#f4a3ae', 'cheek': '#f29aa6', 'mouth': '#e0707f', 'ink': '#14110f', 'white': '#ffffff'}


def surface_y(x: float, z: float) -> float:
    '''Toạ độ y (phía camera là -y) của bề mặt thân tại (x, z); dùng để đặt mắt/má/miệng đúng lên mặt thân.'''
    t = 1.0 - (x / A) ** 2 - ((z - Z0) / C) ** 2
    return -B * math.sqrt(max(t, 0.0))


def build(scene, cfg: dict, make_material, hex_rgba) -> dict:
    outline = bpy.data.collections.new('Outline')  # chỉ nhóm này được vẽ viền đen (Freestyle)
    scene.collection.children.link(outline)
    root = bpy.data.objects.new('MainObject', None)
    scene.collection.objects.link(root)

    def mat(name, hex_color, rough=0.75):
        return make_material(name, hex_rgba(hex_color), roughness=rough)

    body_mat = mat('RabbitBody', cfg['object_color'])
    inner_mat = mat('RabbitInnerEar', PALETTE['inner_ear'])
    cheek_mat = mat('RabbitCheek', PALETTE['cheek'])
    mouth_mat = mat('RabbitMouth', PALETTE['mouth'], 0.5)
    ink_mat = mat('RabbitInk', PALETTE['ink'], 0.4)
    white_mat = mat('RabbitWhite', PALETTE['white'])

    def part(name, loc, scale, material, rot=(0.0, 0.0, 0.0), outlined=False, segs=32):
        bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, segments=segs, ring_count=segs // 2, location=loc)
        obj = bpy.context.active_object
        obj.name = name
        obj.scale = scale
        obj.rotation_euler = rot
        bpy.ops.object.shade_smooth()
        obj.data.materials.append(material)
        for col in list(obj.users_collection):
            col.objects.unlink(obj)
        (outline if outlined else scene.collection).objects.link(obj)
        obj.parent = root
        return obj

    part('Body', (0, 0, Z0), (A, B, C), body_mat, outlined=True, segs=48)

    ears = []
    for side in (-1, 1):
        tilt = side * EAR_TILT
        x = side * 0.2
        outer = part('Ear' + ('R' if side > 0 else 'L'), (x, 0.0, Z0 + C + 0.27), (0.12, 0.09, 0.55), body_mat,
                     rot=(0.0, tilt, 0.0), outlined=True)
        inner = part('EarInner' + ('R' if side > 0 else 'L'), (x, -0.055, Z0 + C + 0.31), (0.07, 0.045, 0.44), inner_mat,
                     rot=(0.0, tilt, 0.0))
        ears += [(outer, tilt), (inner, tilt)]

    # mắt: chấm đen + điểm sáng nhỏ
    for side in (-1, 1):
        ex, ez = side * 0.35, Z0 + 0.41
        ey = surface_y(ex, ez)
        part('Eye' + str(side), (ex, ey + 0.02, ez), (0.065, 0.035, 0.085), ink_mat, segs=16)
        part('EyeShine' + str(side), (ex + 0.02, ey - 0.012, ez + 0.03), (0.022, 0.012, 0.025), white_mat, segs=12)
        cx, cz = side * 0.64, Z0 + 0.2
        part('Cheek' + str(side), (cx, surface_y(cx, cz) + 0.03, cz), (0.17, 0.03, 0.1), cheek_mat, segs=16)

    # miệng nhỏ: viền đen + lưỡi hồng
    mz = Z0 + 0.21
    my = surface_y(0.0, mz)
    part('MouthOutline', (0.0, my + 0.02, mz), (0.085, 0.03, 0.06), ink_mat, segs=16)
    part('Mouth', (0.0, my - 0.003, mz - 0.01), (0.062, 0.03, 0.04), mouth_mat, segs=16)

    # tay (ngắn, hai bên thân) và chân (hai bầu dưới đáy)
    for side in (-1, 1):
        part('Arm' + str(side), (side * 0.97, -0.12, Z0 - 0.3), (0.15, 0.11, 0.2), body_mat,
             rot=(0.0, side * 0.35, 0.0), outlined=True, segs=24)
        part('Foot' + str(side), (side * 0.38, -0.3, 0.14), (0.24, 0.2, 0.14), body_mat, outlined=True, segs=24)

    # đuôi trắng phía sau bên trái (nhìn từ phía trước), có đốm hồng
    part('Tail', (-0.95, 0.4, 0.45), (0.22, 0.2, 0.22), white_mat, outlined=True, segs=24)
    part('TailSpot', (-1.0, 0.3, 0.5), (0.07, 0.04, 0.07), mat('RabbitTailSpot', PALETTE['inner_ear']), segs=12)
    return {'root': root, 'ears': ears, 'outline': outline}


def animate(parts: dict, n: int, fps: int) -> None:
    '''Nhún nhẹ cả nhân vật + tai lắc theo nhịp (keyframe thật). Camera turntable do scene_basic xử lý.'''
    root = parts['root']
    for f in range(1, n + 1):
        t = (f - 1) / fps
        hop = abs(math.sin(math.pi * t * 0.75))
        root.location[2] = 0.22 * hop
        root.keyframe_insert(data_path='location', index=2, frame=f)
        wob = math.radians(7) * math.sin(math.tau * t * 0.75 + 0.6)
        for ear, base in parts['ears']:
            ear.rotation_euler[1] = base + (wob if base > 0 else -wob)
            ear.keyframe_insert(data_path='rotation_euler', index=1, frame=f)


def setup_outline(scene, parts: dict, thickness: float) -> None:
    '''Viền đen kiểu hoạt hình bằng Freestyle, chỉ áp cho nhóm Outline (mắt/má/miệng không bị viền).'''
    scene.render.use_freestyle = True
    view_layer = bpy.context.view_layer
    view_layer.use_freestyle = True
    settings = view_layer.freestyle_settings
    lineset = settings.linesets[0] if len(settings.linesets) else settings.linesets.new('Outline')
    lineset.select_silhouette = True
    lineset.select_border = True
    lineset.select_crease = False
    lineset.select_by_collection = True
    lineset.collection = parts['outline']
    if lineset.linestyle is None:  # scene rỗng sau read_factory_settings(use_empty=True) không có sẵn linestyle
        lineset.linestyle = bpy.data.linestyles.new('OutlineStyle')
    lineset.linestyle.color = (0.008, 0.006, 0.005)
    lineset.linestyle.thickness = thickness
