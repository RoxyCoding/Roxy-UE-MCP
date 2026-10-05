"""Beverage can generator.

Builds a can from a measured real-world profile (revolved), including the bottom dome and standing
ring, necking, double seam, countersunk lid panel, rivet, pull tab with finger hole and the score line
of the tear panel. The side wall gets a cylindrical UV for the printed label.

Units: Blender units are meters; dimensions below are in millimeters and converted with MM.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import bmesh
import bpy

from .. import materials
from .. import meshutil as mu
from ..label import LabelDesign, render_label

MM = 0.001


@dataclass(frozen=True)
class CanSpec:
    """Main dimensions in mm."""
    body_radius: float      # straight wall
    height: float           # standing height (bottom of the standing ring to top of the seam)
    seam_radius: float      # outer radius of the double seam (lid size)


PRESETS = {
    '350ml': CanSpec(33.0, 122.2, 27.3),      # 211 body / 202 end (JP/US 12 oz)
    '330ml': CanSpec(33.0, 115.2, 27.3),      # EU standard
    '500ml': CanSpec(33.0, 168.0, 27.3),      # tall
    '250ml_slim': CanSpec(26.6, 134.0, 25.0),  # slim / sleek
    '190ml': CanSpec(26.6, 104.0, 25.0),      # short coffee can size
}


def can_profile(spec: CanSpec) -> tuple[list[tuple[float, float]], dict]:
    """(r, z) profile in mm from the bottom pole to the lid pole, plus key heights."""
    R, H, Rs = spec.body_radius, spec.height, spec.seam_radius
    k = R / 33.0                              # scale bottom details with the body size
    dome_top = 10.0 * k
    ring_r = 0.76 * R                         # standing ring radius
    chime = 0.8 * k                           # radius of the standing ring curvature
    wall_start = 9.0 * k                      # where the bottom curves into the straight wall
    pts: list[tuple[float, float]] = []

    # bottom dome (spherical cap through the pole and the inner foot of the standing ring)
    foot = (ring_r - 1.8 * k, 3.2 * k)
    c = (foot[0] ** 2 + foot[1] ** 2 - dome_top ** 2) / (2 * (foot[1] - dome_top))
    rho = dome_top - c
    a_end = math.degrees(math.atan2(foot[1] - c, foot[0]))
    pts += mu.arc(0.0, c, rho, 90.0, a_end, 24)
    pts += mu.smoothstep_curve(foot, (ring_r - chime, chime), 6)[1:]
    pts += mu.arc(ring_r, chime, chime, 180.0, 360.0, 10)[1:]          # standing ring (touches z=0)
    a = ring_r + chime
    for i in range(1, 17):                                              # outer bottom up to the wall
        t = math.radians(90.0 * i / 16)
        pts.append((a + (R - a) * math.sin(t), chime + (wall_start - chime) * (1 - math.cos(t))))

    # straight wall, necking and double seam
    neck_start = H - 15.0
    neck_end = (Rs - 0.7, H - 3.4)
    pts += [(R, wall_start + (neck_start - wall_start) * i / 8) for i in range(1, 9)]
    pts += mu.smoothstep_curve((R, neck_start), neck_end, 20)[1:]
    pts += mu.smoothstep_curve(neck_end, (Rs, H - 2.3), 5)[1:]
    pts.append((Rs, H - 0.6))
    pts += mu.arc(Rs - 0.6, H - 0.6, 0.6, 0.0, 90.0, 6)[1:]           # rounded top of the seam
    pts += mu.arc(Rs - 1.0, H - 0.6, 0.6, 90.0, 180.0, 6)[1:]
    # countersink and lid panel
    sink_bottom = H - 6.3
    pts.append((Rs - 1.75, sink_bottom + 0.8))
    pts += mu.arc(Rs - 2.45, sink_bottom + 0.7, 0.7, 0.0, -180.0, 10)[1:]
    panel = H - 4.8
    pts.append((Rs - 3.3, panel - 0.6))
    pts += mu.arc(Rs - 3.9, panel - 0.6, 0.6, 0.0, 90.0, 6)[1:]        # panel edge radius
    bead = Rs - 7.0                                                     # reinforcing bead on the panel
    pts += [(bead + 0.6, panel), (bead + 0.3, panel + 0.18), (bead, panel + 0.22),
            (bead - 0.3, panel + 0.18), (bead - 0.6, panel)]
    pts.append((0.0, panel))
    keys = {'label_bottom': wall_start, 'label_top': neck_end[1], 'panel': panel, 'seam_top': H,
            'panel_radius': Rs - 3.9}
    return mu.dedupe(pts), keys


def _body_mesh(name: str, spec: CanSpec, segments: int):
    profile, keys = can_profile(spec)
    profile_m = [(r * MM, z * MM) for r, z in profile]
    lo, hi = keys['label_bottom'], keys['label_top']

    def is_label(i: int) -> bool:
        z0, z1 = profile[i][1], profile[i + 1][1]
        return z0 >= lo - 1e-6 and z1 <= hi + 1e-6 and profile[i][0] > spec.seam_radius - 1.0 and z1 >= z0

    def uv_for_span(i: int):
        if not is_label(i):
            return None
        return ((profile[i][1] - lo) / (hi - lo), (profile[i + 1][1] - lo) / (hi - lo))

    bm = bmesh.new()
    uv = bm.loops.layers.uv.new('UVMap')
    mu.revolve(bm, profile_m, segments, uv, lambda i: 1 if is_label(i) else 0, uv_for_span)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    return mesh, keys


def _tab_outline(scale: float):
    """Pull tab outline (outer) and holes, in mm, rivet at the origin, nose towards +X."""
    outer = []
    # finger end: big round at -X, tapering to a narrower round nose at +X
    for i in range(25):
        a = math.radians(90 + 180 * i / 24)
        outer.append((-9.5 + 6.2 * math.cos(a), 6.2 * math.sin(a)))
    for i in range(25):
        a = math.radians(-90 + 180 * i / 24)
        outer.append((3.0 + 4.6 * math.cos(a), 4.6 * math.sin(a)))
    finger_hole = mu.rounded_rect(-9.8, 0.0, 7.6, 7.4, 3.6, 8)
    rivet_slot = []
    for i in range(17):                                    # U-shaped cut around the rivet
        a = math.radians(110 + 140 * i / 16)
        rivet_slot.append((2.6 * math.cos(a), 2.6 * math.sin(a)))
    for i in range(17):
        a = math.radians(250 - 140 * i / 16)
        rivet_slot.append((3.3 * math.cos(a), 3.3 * math.sin(a)))
    sc = lambda pts: [(x * scale * MM, y * scale * MM) for x, y in pts]
    return [sc(outer), sc(finger_hole), sc(rivet_slot)]


def _score_path(panel_r: float, z: float, scale: float):
    """D-shaped score line of the tear panel on the +X side of the rivet (mm -> m)."""
    x0, x1 = 3.4 * scale, panel_r - 2.6 * scale
    cx, half_len, half_w = (x0 + x1) / 2, (x1 - x0) / 2, 7.2 * scale
    pts = []
    for i in range(72):
        t = 2 * math.pi * i / 72
        x = cx + half_len * math.copysign(abs(math.cos(t)) ** 0.7, math.cos(t))
        y = half_w * math.copysign(abs(math.sin(t)) ** 0.8, math.sin(t))
        pts.append((x * MM, y * MM, z * MM))
    return pts


def _rivet_mesh(name: str, panel: float, scale: float):
    """Rivet button in the lid center: a short collar with a rounded top (mm -> m)."""
    r, h = 1.9 * scale, 0.9
    profile = [(r + 0.6, panel), (r + 0.25, panel + 0.3), (r, panel + 0.6)]
    for i in range(1, 9):                                   # quarter ellipse up to the top pole
        t = math.radians(90.0 * i / 8)
        profile.append((r * math.cos(t), panel + 0.6 + (h - 0.6) * math.sin(t)))
    profile[-1] = (0.0, panel + h)
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new('UVMap')
    mu.revolve(bm, [(x * MM, z * MM) for x, z in profile], 32, uv, lambda i: 2)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def create_can(name: str = 'Can', size: str = '350ml', label: LabelDesign | None = None,
               label_image=None, tab_color: tuple | None = None, segments: int = 128,
               location=(0.0, 0.0, 0.0), rotation_z: float = 0.0, tab_angle: float = 0.0,
               collection=None) -> bpy.types.Object:
    """Creates a detailed beverage can and returns the object.

    Args:
        name: Object / mesh name.
        size: One of PRESETS ('350ml', '330ml', '500ml', '250ml_slim', '190ml').
        label: Label design (brand, flavor, colors); a default design is used when omitted.
        label_image: Existing bpy Image for the label (skips label rendering; label is ignored).
        tab_color: RGB of the pull tab (default: plain aluminum).
        segments: Radial resolution.
        location: World position (m).
        rotation_z: Rotation around Z in degrees.
        tab_angle: Rotation of the tab around the rivet in degrees (0 = nose over the tear panel).
        collection: Collection to link to (default: scene collection).
    """
    if size not in PRESETS:
        raise ValueError(f'size must be one of {sorted(PRESETS)}')
    spec = PRESETS[size]
    scale = spec.seam_radius / 27.3
    body, keys = _body_mesh(name, spec, segments)

    tab_z = keys['panel'] + 0.75
    tab_mesh = mu.outline_mesh(name + '_Tab', _tab_outline(scale), 0.42 * MM, 0.08 * MM)
    rivet_mesh = _rivet_mesh(name + '_Rivet', keys['panel'], scale)
    score_mesh = mu.tube_mesh(name + '_Score', _score_path(keys['panel_radius'], keys['panel'] + 0.02, scale),
                              0.28 * MM)

    import mathutils
    bm = bmesh.new()
    bm.from_mesh(body)
    uv_layer = bm.loops.layers.uv.get('UVMap') or bm.loops.layers.uv.new('UVMap')
    parts = [(tab_mesh, mathutils.Matrix.Translation((0, 0, tab_z * MM)) @ mathutils.Matrix.Rotation(
        math.radians(tab_angle), 4, 'Z') @ mathutils.Matrix.Rotation(math.radians(-1.5), 4, 'Y'), 2),
             (rivet_mesh, mathutils.Matrix.Identity(4), 2),
             (score_mesh, mathutils.Matrix.Identity(4), 0)]
    for mesh, matrix, mat_index in parts:
        mesh.transform(matrix)
        before = set(bm.faces)
        bm.from_mesh(mesh)
        for face in bm.faces:
            if face not in before:
                face.material_index = mat_index
        bpy.data.meshes.remove(mesh)
    bm.to_mesh(body)
    bm.free()
    body.shade_smooth()
    body.set_sharp_from_angle(angle=math.radians(40))

    obj = bpy.data.objects.new(name, body)
    (collection or bpy.context.scene.collection).objects.link(obj)
    obj.location = location
    obj.rotation_euler[2] = math.radians(rotation_z)

    if label_image is None:
        design = label or LabelDesign()
        circumference = 2 * math.pi * spec.body_radius
        label_image = render_label(design, circumference, keys['label_top'] - keys['label_bottom'],
                                   name=f'{name}_Label', volume_text=size.split('_')[0].upper())
    body.materials.append(materials.aluminum(f'{name}_Aluminum'))
    body.materials.append(materials.printed_label(f'{name}_Label', label_image))
    body.materials.append(materials.aluminum(f'{name}_TabMetal', tint=tab_color))
    obj['generator'] = 'blender_toolkit.can'
    obj['size'] = size
    return obj
