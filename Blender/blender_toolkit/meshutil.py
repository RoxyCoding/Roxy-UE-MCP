"""Small mesh-building helpers shared by the generators."""

from __future__ import annotations

import math

import bmesh
import bpy


def arc(cx: float, cz: float, r: float, a0: float, a1: float, steps: int) -> list[tuple[float, float]]:
    """Points on a circular arc in the (r, z) profile plane; angles in degrees."""
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / steps)),
             cz + r * math.sin(math.radians(a0 + (a1 - a0) * i / steps))) for i in range(steps + 1)]


def smoothstep_curve(p0, p1, steps: int) -> list[tuple[float, float]]:
    """S-shaped transition from p0 to p1 (radius eases, height linear)."""
    out = []
    for i in range(steps + 1):
        t = i / steps
        s = t * t * (3 - 2 * t)
        out.append((p0[0] + (p1[0] - p0[0]) * s, p0[1] + (p1[1] - p0[1]) * t))
    return out


def dedupe(points, eps: float = 1e-7):
    out = []
    for p in points:
        if not out or abs(out[-1][0] - p[0]) > eps or abs(out[-1][1] - p[1]) > eps:
            out.append(p)
    return out


def revolve(bm: bmesh.types.BMesh, profile, segments: int, uv_layer, material_for_span,
            uv_for_span=None) -> None:
    """Revolves an (r, z) profile around Z into bm. Points with r == 0 become poles.

    material_for_span(i) -> material index of the band between profile[i] and profile[i+1].
    uv_for_span(i, u0, u1) -> (v_a, v_b) to override the band's V range, or None for the default
    (arc-length along the profile). U runs around the axis 0..1.
    """
    rings = []
    for r, z in profile:
        if r <= 1e-9:
            rings.append([bm.verts.new((0.0, 0.0, z))])
        else:
            rings.append([bm.verts.new((r * math.cos(2 * math.pi * k / segments),
                                        r * math.sin(2 * math.pi * k / segments), z)) for k in range(segments)])
    total = sum(math.dist(profile[i], profile[i + 1]) for i in range(len(profile) - 1)) or 1.0
    acc = 0.0
    for i in range(len(profile) - 1):
        a, b = rings[i], rings[i + 1]
        seg_len = math.dist(profile[i], profile[i + 1])
        va, vb = acc / total, (acc + seg_len) / total
        acc += seg_len
        if uv_for_span:
            override = uv_for_span(i)
            if override is not None:
                va, vb = override
        for k in range(segments):
            k2 = (k + 1) % segments
            u0, u1 = k / segments, (k + 1) / segments
            uv_of = {}
            for ring, v in ((a, va), (b, vb)):
                if len(ring) == 1:
                    uv_of[ring[0]] = ((u0 + u1) / 2, v)
                else:
                    uv_of[ring[k]] = (u0, v)
                    uv_of[ring[k2]] = (u1, v)
            if len(a) == 1:
                verts = (a[0], b[k], b[k2])
            elif len(b) == 1:
                verts = (a[k], a[k2], b[0])
            else:
                verts = (a[k], a[k2], b[k2], b[k])
            try:
                face = bm.faces.new(verts)
            except ValueError:
                continue
            face.material_index = material_for_span(i)
            for loop in face.loops:
                loop[uv_layer].uv = uv_of[loop.vert]
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])


def outline_mesh(name: str, outlines, thickness: float, bevel: float = 0.0):
    """Flat 2D outlines (first = outer, rest = holes, as lists of (x, y)) extruded to a mesh object."""
    curve = bpy.data.curves.new(name + '_curve', 'CURVE')
    curve.dimensions = '2D'
    curve.fill_mode = 'BOTH'
    curve.extrude = thickness / 2
    curve.bevel_depth = bevel
    curve.resolution_u = 1
    for pts in outlines:
        spline = curve.splines.new('POLY')
        spline.points.add(len(pts) - 1)
        for p, (x, y) in zip(spline.points, pts):
            p.co = (x, y, 0.0, 1.0)
        spline.use_cyclic_u = True
    return _curve_to_mesh(name, curve)


def tube_mesh(name: str, path, radius: float, cyclic: bool = True):
    """A round tube following a 3D path (used for embossed score lines / beads)."""
    curve = bpy.data.curves.new(name + '_curve', 'CURVE')
    curve.dimensions = '3D'
    curve.bevel_depth = radius
    curve.bevel_resolution = 2
    curve.use_fill_caps = True
    spline = curve.splines.new('POLY')
    spline.points.add(len(path) - 1)
    for p, (x, y, z) in zip(spline.points, path):
        p.co = (x, y, z, 1.0)
    spline.use_cyclic_u = cyclic
    return _curve_to_mesh(name, curve)


def _curve_to_mesh(name: str, curve):
    tmp = bpy.data.objects.new(name + '_tmp', curve)
    bpy.context.scene.collection.objects.link(tmp)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(tmp.evaluated_get(depsgraph))
    mesh.name = name
    bpy.data.objects.remove(tmp)
    bpy.data.curves.remove(curve)
    return mesh


def rounded_rect(cx, cy, w, h, r, steps=6):
    """Rounded rectangle outline (counter-clockwise)."""
    r = min(r, w / 2, h / 2)
    pts = []
    for (ox, oy, a0) in ((w / 2 - r, h / 2 - r, 0), (-w / 2 + r, h / 2 - r, 90),
                         (-w / 2 + r, -h / 2 + r, 180), (w / 2 - r, -h / 2 + r, 270)):
        for i in range(steps + 1):
            a = math.radians(a0 + 90 * i / steps)
            pts.append((cx + ox + r * math.cos(a), cy + oy + r * math.sin(a)))
    return pts
