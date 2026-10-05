"""Product labels rendered inside Blender (no external images).

A label is laid out as flat shapes and text in a temporary scene, rendered orthographically with
Cycles to an image, and packed into the .blend. The front panel (brand) is centered at U = 0.75,
which faces -Y (Blender's front view) on revolved products; the back panel (ingredients, barcode)
is centered at U = 0.25.
"""

from __future__ import annotations

import os
import random
import tempfile
from dataclasses import dataclass, field

import bpy

from . import materials

MM = 0.001


def hex_color(text: str) -> tuple[float, float, float]:
    """'#ff8800' (sRGB) -> linear RGB."""
    text = text.lstrip('#')
    rgb = [int(text[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb)


@dataclass
class LabelDesign:
    brand: str = 'AQUA FIZZ'
    flavor: str = 'SPARKLING LEMON'
    tagline: str = 'NATURALLY FLAVORED'
    background: str = '#0f4fa8'
    accent: str = '#ffd23f'
    accent2: str = '#3fa9f5'
    text: str = '#ffffff'
    seed: int = 7
    font_path: str | None = None     # .ttf/.otf for non-Latin text (Blender's built-in font is Latin only)
    extra: dict = field(default_factory=dict)


class _Layout:
    def __init__(self, scene, width: float, height: float, font):
        self.scene, self.w, self.h, self.font = scene, width, height, font
        self.objects, self.z = [], 0.0

    def _link(self, obj):
        self.scene.collection.objects.link(obj)
        self.objects.append(obj)
        self.z += 0.00001
        obj.location.z = self.z
        return obj

    def rect(self, x, y, w, h, color, name='rect'):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata([(x, y, 0), (x + w, y, 0), (x + w, y + h, 0), (x, y + h, 0)], [], [(0, 1, 2, 3)])
        mesh.materials.append(materials.emission(f'_label_{color}', hex_color(color)))
        return self._link(bpy.data.objects.new(name, mesh))

    def circle(self, cx, cy, r, color, name='circle', segments=96):
        import math
        verts = [(cx + r * math.cos(2 * math.pi * i / segments), cy + r * math.sin(2 * math.pi * i / segments), 0)
                 for i in range(segments)]
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(verts, [], [list(range(segments))])
        mesh.materials.append(materials.emission(f'_label_{color}', hex_color(color)))
        return self._link(bpy.data.objects.new(name, mesh))

    def text(self, body, cx, cy, max_w, max_h, color, name='text', rotate=0.0):
        import math
        curve = bpy.data.curves.new(name, 'FONT')
        curve.body = body
        curve.align_x, curve.align_y = 'CENTER', 'CENTER'
        if self.font:
            curve.font = self.font
        curve.materials.append(materials.emission(f'_label_{color}', hex_color(color)))
        obj = self._link(bpy.data.objects.new(name, curve))
        obj.location.x, obj.location.y = cx, cy
        obj.rotation_euler.z = math.radians(rotate)
        self.scene.view_layers[0].update()
        dims = obj.dimensions
        if dims.x > 0 and dims.y > 0:
            w, h = (dims.y, dims.x) if abs(rotate) == 90 else (dims.x, dims.y)
            s = min(max_w / w, max_h / h)
            obj.scale = (s, s, 1)
        return obj


def render_label(design: LabelDesign, width_mm: float, height_mm: float, name: str = 'Label',
               volume_text: str = '', resolution: int = 2048, samples: int = 16):
    """Renders the label to an image (packed into the .blend) and returns the bpy Image."""
    rng = random.Random(design.seed)
    W, H = width_mm * MM, height_mm * MM
    scene = bpy.data.scenes.new(f'_{name}_scene')
    font = bpy.data.fonts.load(design.font_path, check_existing=True) if design.font_path else None
    lay = _Layout(scene, W, H, font)
    try:
        bg, acc, acc2, txt = design.background, design.accent, design.accent2, design.text
        lay.rect(0, 0, W, H, bg, 'bg')
        # bands around the whole can
        lay.rect(0, 0, W, H * 0.16, acc, 'band_bottom')
        lay.rect(0, H * 0.16, W, H * 0.025, acc2, 'band_bottom_line')
        lay.rect(0, H * 0.93, W, H * 0.07, acc, 'band_top')
        # front panel (U = 0.75)
        fx = W * 0.75
        lay.circle(fx, H * 0.56, H * 0.34, acc2, 'badge')
        lay.circle(fx, H * 0.56, H * 0.30, bg, 'badge_inner')
        placed = 0
        while placed < 14:                                # bubbles, kept clear of the text block
            r = H * rng.uniform(0.008, 0.03)
            x, y = W * rng.uniform(-0.2, 0.2), H * rng.uniform(0.22, 0.9)
            if abs(x) < W * 0.17 and 0.32 * H < y < 0.7 * H:
                continue
            lay.circle(fx + x, y, r, acc2, f'bubble{placed}', 32)
            placed += 1
        lay.text(design.brand, fx, H * 0.6, W * 0.3, H * 0.2, txt, 'brand')
        lay.text(design.flavor, fx, H * 0.4, W * 0.24, H * 0.07, acc, 'flavor')
        if design.tagline:
            lay.text(design.tagline, fx, H * 0.085, W * 0.2, H * 0.05, bg, 'tagline')
        if volume_text:
            lay.text(volume_text, fx + W * 0.16, H * 0.085, W * 0.05, H * 0.06, bg, 'volume')
        # side panels: vertical brand
        for x in (W * 0.5, W, 0.0):
            lay.text(design.brand, x, H * 0.56, H * 0.6, W * 0.05, acc2, 'brand_side', rotate=90)
        # back panel (U = 0.25): nutrition box and barcode
        bx = W * 0.25
        lay.rect(bx - W * 0.1, H * 0.3, W * 0.2, H * 0.55, '#ffffff', 'info_box')
        lay.text('NUTRITION FACTS', bx, H * 0.8, W * 0.17, H * 0.045, '#111111', 'info_title')
        for i in range(9):
            y = H * (0.72 - i * 0.045)
            lay.rect(bx - W * 0.085, y, W * rng.uniform(0.09, 0.17), H * 0.012, '#555555', f'info_line{i}')
        lay.rect(bx - W * 0.06, H * 0.19, W * 0.12, H * 0.09, '#ffffff', 'barcode_bg')
        x = bx - W * 0.05
        while x < bx + W * 0.05:
            bw = W * rng.choice((0.0012, 0.0024, 0.0036))
            lay.rect(x, H * 0.205, bw, H * 0.065, '#000000', 'bar')
            x += bw + W * rng.choice((0.0012, 0.0024))

        cam_data = bpy.data.cameras.new('_label_cam')
        cam_data.type = 'ORTHO'
        cam_data.ortho_scale = W
        cam = bpy.data.objects.new('_label_cam', cam_data)
        scene.collection.objects.link(cam)
        lay.objects.append(cam)
        cam.location = (W / 2, H / 2, 1.0)
        scene.camera = cam
        r = scene.render
        r.engine = 'CYCLES'
        scene.cycles.device = 'CPU'
        scene.cycles.samples = samples
        scene.cycles.use_denoising = False
        r.resolution_x = resolution
        r.resolution_y = max(1, round(resolution * H / W))
        r.resolution_percentage = 100
        scene.view_settings.view_transform = 'Standard'
        r.image_settings.file_format = 'PNG'
        path = os.path.join(tempfile.gettempdir(), f'{name}.png')
        r.filepath = path
        bpy.ops.render.render(write_still=True, scene=scene.name)
        image = bpy.data.images.load(path, check_existing=False)
        image.name = name
        image.pack()
        return image
    finally:
        for obj in lay.objects:
            data = obj.data
            bpy.data.objects.remove(obj)
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Curve):
                bpy.data.curves.remove(data)
            elif isinstance(data, bpy.types.Camera):
                bpy.data.cameras.remove(data)
        bpy.data.scenes.remove(scene)
        for mat in [m for m in bpy.data.materials if m.name.startswith('_label_') and m.users == 0]:
            bpy.data.materials.remove(mat)


@dataclass
class StickerDesign:
    """Front sticker of a packaged product (e.g. onigiri)."""
    title: str = 'TUNA MAYO'
    subtitle: str = 'ONIGIRI'
    price: str = '150'
    header: str = '#c8102e'
    accent: str = '#f2a900'
    background: str = '#ffffff'
    text: str = '#1a1a1a'
    font_path: str | None = None     # e.g. a Japanese .ttf/.otf for Japanese titles


def render_sticker(design: StickerDesign, width_mm: float, height_mm: float, name: str = 'Sticker',
                   resolution: int = 1024, samples: int = 16):
    """Renders a rectangular product sticker and returns the packed bpy Image."""
    W, H = width_mm * MM, height_mm * MM
    scene = bpy.data.scenes.new(f'_{name}_scene')
    font = bpy.data.fonts.load(design.font_path, check_existing=True) if design.font_path else None
    lay = _Layout(scene, W, H, font)
    try:
        lay.rect(0, 0, W, H, design.background, 'bg')
        lay.rect(0, H * 0.7, W, H * 0.3, design.header, 'header')
        lay.rect(0, H * 0.66, W, H * 0.04, design.accent, 'header_line')
        lay.text(design.subtitle, W * 0.5, H * 0.85, W * 0.8, H * 0.18, '#ffffff', 'subtitle')
        lay.text(design.title, W * 0.5, H * 0.42, W * 0.9, H * 0.3, design.text, 'title')
        lay.rect(W * 0.62, H * 0.03, W * 0.35, H * 0.18, design.accent, 'price_bg')
        lay.text(design.price, W * 0.795, H * 0.12, W * 0.3, H * 0.13, design.text, 'price')
        lay.rect(W * 0.04, H * 0.06, W * 0.3, H * 0.03, '#999999', 'small1')
        lay.rect(W * 0.04, H * 0.13, W * 0.42, H * 0.03, '#999999', 'small2')
        return _render(scene, lay, W, H, name, resolution, samples)
    finally:
        _cleanup(scene, lay)


def _render(scene, lay, W, H, name, resolution, samples):
    cam_data = bpy.data.cameras.new('_label_cam')
    cam_data.type = 'ORTHO'
    cam_data.ortho_scale = max(W, H)
    cam = bpy.data.objects.new('_label_cam', cam_data)
    scene.collection.objects.link(cam)
    lay.objects.append(cam)
    cam.location = (W / 2, H / 2, 1.0)
    scene.camera = cam
    r = scene.render
    r.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = samples
    scene.cycles.use_denoising = False
    if W >= H:
        r.resolution_x, r.resolution_y = resolution, max(1, round(resolution * H / W))
    else:
        r.resolution_x, r.resolution_y = max(1, round(resolution * W / H)), resolution
    r.resolution_percentage = 100
    scene.view_settings.view_transform = 'Standard'
    r.image_settings.file_format = 'PNG'
    path = os.path.join(tempfile.gettempdir(), f'{name}.png')
    r.filepath = path
    bpy.ops.render.render(write_still=True, scene=scene.name)
    image = bpy.data.images.load(path, check_existing=False)
    image.name = name
    image.pack()
    return image


def _cleanup(scene, lay):
    for obj in lay.objects:
        data = obj.data
        bpy.data.objects.remove(obj)
        if isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
        elif isinstance(data, bpy.types.Curve):
            bpy.data.curves.remove(data)
        elif isinstance(data, bpy.types.Camera):
            bpy.data.cameras.remove(data)
    bpy.data.scenes.remove(scene)
    for mat in [m for m in bpy.data.materials if m.name.startswith('_label_') and m.users == 0]:
        bpy.data.materials.remove(mat)
