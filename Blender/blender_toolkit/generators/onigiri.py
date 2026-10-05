"""Onigiri (triangular rice ball) generator.

The rice body is a rounded triangle "pillow" with hand-pressed lumps; individual rice grains are
scattered over the visible surface; nori wraps the lower part of the body. Dimensions follow a
typical convenience-store onigiri (about 80 x 72 x 33 mm).
"""

from __future__ import annotations

import math
import random

import bmesh
import bpy
import mathutils
from mathutils import Vector, noise

from .. import materials
from .. import meshutil as mu
from ..label import StickerDesign, render_sticker

MM = 0.001


def rounded_triangle(width: float, height: float, corner: float, bulge: float, count: int):
    """Closed outline (x, z) in mm, counter-clockwise, resampled to `count` evenly spaced points."""
    centers = [(-width / 2 + corner, corner), (width / 2 - corner, corner), (0.0, height - corner)]
    pts = []
    for i in range(3):
        c0, c1 = centers[i], centers[(i + 1) % 3]
        dx, dz = c1[0] - c0[0], c1[1] - c0[1]
        length = math.hypot(dx, dz)
        nx, nz = dz / length, -dx / length                         # outward normal of this edge
        edge_bulge = bulge * 0.1 if i == 0 else bulge             # flat bottom so it stands
        for k in range(40):                                        # slightly bulging straight edge
            t = k / 40
            b = edge_bulge * math.sin(math.pi * t)
            pts.append((c0[0] + dx * t + nx * (corner + b), c0[1] + dz * t + nz * (corner + b)))
        c2 = centers[(i + 2) % 3]
        ex, ez = c2[0] - c1[0], c2[1] - c1[1]
        el = math.hypot(ex, ez)
        a0 = math.atan2(nz, nx)
        a1 = math.atan2(-ex / el, ez / el)
        while a1 < a0:
            a1 += 2 * math.pi
        for k in range(16):                                        # corner arc
            a = a0 + (a1 - a0) * k / 16
            pts.append((c1[0] + corner * math.cos(a), c1[1] + corner * math.sin(a)))
    # resample evenly by arc length
    seg = [math.dist(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
    total = sum(seg)
    out, i, acc = [], 0, 0.0
    for n in range(count):
        target = total * n / count
        while acc + seg[i] < target:
            acc += seg[i]
            i += 1
        t = (target - acc) / seg[i]
        p0, p1 = pts[i], pts[(i + 1) % len(pts)]
        out.append((p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t))
    return out


def _body_bmesh(width, height, thickness, corner, bulge, seed, around=180, levels=90, flatness=3.2,
                lumps: float = 1.0):
    """Pillow body: the outline is shrunk towards its centroid by a superellipse over the thickness."""
    outline = rounded_triangle(width, height, corner, bulge, around)
    cx = sum(p[0] for p in outline) / len(outline)
    cz = sum(p[1] for p in outline) / len(outline)
    rng = random.Random(seed)
    lump_offset = Vector((rng.uniform(0, 100), rng.uniform(0, 100), rng.uniform(0, 100)))
    bm = bmesh.new()
    rings = []
    # Cross-section: superellipse (s = radial scale, t = position through the thickness), resampled
    # evenly by arc length so the flat faces get as many rings as the rounded rim.
    mean_r = sum(math.hypot(x - cx, z - cz) for x, z in outline) / len(outline)
    dense = []
    for i in range(2001):
        phi = -math.pi / 2 + math.pi * i / 2000
        t = math.copysign(abs(math.sin(phi)) ** (2 / flatness), phi)
        sc = abs(math.cos(phi)) ** (2 / flatness)
        dense.append((sc, t))
    lengths = [0.0]
    for a, b in zip(dense, dense[1:]):
        lengths.append(lengths[-1] + math.hypot((a[0] - b[0]) * mean_r, (a[1] - b[1]) * thickness / 2))
    section, i = [], 0
    for j in range(1, levels):
        target = lengths[-1] * j / levels
        while lengths[i + 1] < target:
            i += 1
        f = (target - lengths[i]) / (lengths[i + 1] - lengths[i])
        section.append((dense[i][0] + (dense[i + 1][0] - dense[i][0]) * f,
                        dense[i][1] + (dense[i + 1][1] - dense[i][1]) * f))
    for s, t in section:
        y = t * thickness / 2
        ring = []
        for x, z in outline:
            ring.append(bm.verts.new(((cx + (x - cx) * s) * MM, y * MM, (cz + (z - cz) * s) * MM)))
        rings.append(ring)
    front = bm.verts.new((cx * MM, -thickness / 2 * MM, cz * MM))
    back = bm.verts.new((cx * MM, thickness / 2 * MM, cz * MM))
    n = len(outline)
    for a, b in zip(rings, rings[1:]):
        for k in range(n):
            bm.faces.new((a[k], a[(k + 1) % n], b[(k + 1) % n], b[k]))
    for k in range(n):
        bm.faces.new((front, rings[0][(k + 1) % n], rings[0][k]))
        bm.faces.new((back, rings[-1][k], rings[-1][(k + 1) % n]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.normal_update()
    # hand-pressed lumps (low frequency) and a slight overall asymmetry
    for v in bm.verts:
        p = v.co / MM
        lump = noise.fractal(p * 0.06 + lump_offset, 0.5, 2.0, 3) * 1.1
        lump += noise.noise(p * 0.18 + lump_offset) * 0.35
        v.co += v.normal * lump * lumps * MM
    bm.normal_update()
    return bm


def _grain_template(segments=10, rings=6):
    """Unit rice grain: ellipsoid, slightly flattened, long axis X. Returns (verts, faces)."""
    verts, faces = [], []
    for i in range(rings + 1):
        th = math.pi * i / rings
        for k in range(segments):
            ph = 2 * math.pi * k / segments
            x = math.cos(th)
            r = math.sin(th)
            # slightly pointed ends like a real grain
            x *= 1.0 + 0.08 * (1 - r)
            verts.append((x, r * math.cos(ph), r * math.sin(ph)))
    for i in range(rings):
        for k in range(segments):
            a, b = i * segments + k, i * segments + (k + 1) % segments
            c, d = (i + 1) * segments + (k + 1) % segments, (i + 1) * segments + k
            faces.append((a, b, c, d))
    return verts, faces


def _sample_surface(mesh, count: int, rng: random.Random, keep):
    """Area-weighted random points on the mesh with smooth normals; keep(point_mm) filters them."""
    mesh.calc_loop_triangles()
    tris = mesh.loop_triangles
    verts = mesh.vertices
    areas = [t.area for t in tris]
    total = sum(areas)
    cumulative, acc = [], 0.0
    for a in areas:
        acc += a
        cumulative.append(acc)
    import bisect
    out = []
    tries = 0
    while len(out) < count and tries < count * 6:
        tries += 1
        tri = tris[min(bisect.bisect_left(cumulative, rng.random() * total), len(tris) - 1)]
        r1, r2 = rng.random(), rng.random()
        if r1 + r2 > 1:
            r1, r2 = 1 - r1, 1 - r2
        a, b, c = (verts[i] for i in tri.vertices)
        p = a.co * (1 - r1 - r2) + b.co * r1 + c.co * r2
        nrm = (a.normal * (1 - r1 - r2) + b.normal * r1 + c.normal * r2).normalized()
        if keep(p / MM):
            out.append((p, nrm))
    return out


def create_onigiri(name: str = 'Onigiri', width: float = 80.0, height: float = 72.0, thickness: float = 33.0,
                   packaged: bool = True, sticker: StickerDesign | None = None,
                   nori: bool = True, nori_height: float | None = None, grain_density: float = 0.085,
                   seed: int = 1, location=(0.0, 0.0, 0.0), collection=None) -> bpy.types.Object:
    """Creates an onigiri and returns the rice object (nori, film, tabs and sticker are parented to it).

    Args:
        name: Object name.
        width / height / thickness: Size in mm.
        packaged: Convenience-store packaging: transparent film with tear strip, pull tabs (1-3) and a
            front sticker; the nori sits flat outside the inner film and covers most of the rice.
        sticker: Sticker text/colors (default design when omitted; set font_path for Japanese).
        nori: Add nori.
        nori_height: Nori top edge as a fraction of the height (default 0.88 packaged, 0.52 unwrapped).
        grain_density: Rice grains per mm² of visible surface.
        seed: Random seed (shape lumps, grain placement, nori edge).
        location: World position (m).
        collection: Collection to link to (default: scene collection).
    """
    rng = random.Random(seed)
    coll = collection or bpy.context.scene.collection
    corner = width * 0.19
    bm = _body_bmesh(width, height, thickness, corner, width * 0.045, seed)
    body = bpy.data.meshes.new(name)
    bm.to_mesh(body)
    bm.free()
    body.shade_smooth()

    if nori_height is None:
        nori_height = 0.88 if packaged else 0.52
    cut = height * nori_height
    edge_noise = Vector((rng.uniform(0, 50), 0, rng.uniform(0, 50)))

    def nori_edge(p):
        return cut + noise.noise(Vector((p.x * 0.08, p.y * 0.08, 0)) + edge_noise) * 1.2

    # rice grains on the visible part
    surface_area = sum(p.area for p in body.polygons) / (MM * MM)
    keep = (lambda p: p.z > nori_edge(p) - 1.0) if nori else (lambda p: True)
    candidates = _sample_surface(body, int(surface_area * grain_density * 3), rng, keep)
    samples, cells, spacing = [], {}, 2.0
    for p, nrm in candidates:                   # keep grains apart so they lie side by side, not in piles
        q = p / MM
        key = (int(q.x // spacing), int(q.y // spacing), int(q.z // spacing))
        near = [c for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
                for c in cells.get((key[0] + dx, key[1] + dy, key[2] + dz), ())]
        if any((c - q).length < spacing for c in near):
            continue
        cells.setdefault(key, []).append(q)
        samples.append((p, nrm))
        if len(samples) >= surface_area * grain_density:
            break
    tv, tf = _grain_template()
    verts, faces = [], []
    for p, nrm in samples:
        tangent = nrm.cross(Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1)))).normalized()
        if tangent.length < 0.5:
            continue
        bitangent = nrm.cross(tangent)
        tilt = math.radians(rng.uniform(-12, 12))
        axis_x = (tangent * math.cos(tilt) + nrm * math.sin(tilt)).normalized()
        axis_z = axis_x.cross(bitangent).normalized()
        length = rng.uniform(2.1, 2.8) * MM
        width_ = rng.uniform(1.3, 1.65) * MM
        depth = rng.uniform(0.75, 1.15) * MM            # pressed grains are flatter
        bend = rng.uniform(-0.25, 0.25)                 # slight banana curve
        taper = rng.uniform(-0.12, 0.12)                # one end fatter than the other
        center = p + nrm * rng.uniform(-0.35, 0.05) * MM
        base = len(verts)
        for x, y, z in tv:
            w = 1.0 + taper * x
            verts.append(center + axis_x * x * length + bitangent * (y * w + bend * (1 - x * x)) * width_
                         + axis_z * z * w * depth)
        faces += [tuple(base + i for i in f) for f in tf]
    grains = bpy.data.meshes.new(name + '_Grains')
    grains.from_pydata([tuple(v) for v in verts], [], faces)
    per_grain = len(tv)
    values = []
    for g in range(len(verts) // per_grain):
        values += [rng.random()] * per_grain
    attr = grains.attributes.new('grain_random', 'FLOAT', 'POINT')
    attr.data.foreach_set('value', values)
    for poly in grains.polygons:
        poly.use_smooth = True
        poly.material_index = 1

    bm = bmesh.new()
    bm.from_mesh(body)
    bm.from_mesh(grains)
    bm.to_mesh(body)
    bm.free()
    bpy.data.meshes.remove(grains)
    obj = bpy.data.objects.new(name, body)
    coll.objects.link(obj)
    obj.location = location
    body.materials.append(materials.rice(f'{name}_RiceBase', base=True))
    body.materials.append(materials.rice(f'{name}_Rice'))
    obj['generator'] = 'blender_toolkit.onigiri'

    children = []
    if nori:
        children.append(_nori(name, width, height, thickness, corner, seed, nori_edge,
                              offset=1.7 if packaged else 1.0, lumps=0.12 if packaged else 0.35))
    if packaged:
        children += _package(name, width, height, thickness, corner, seed, sticker or StickerDesign())
    for child in children:
        coll.objects.link(child)
        child.parent = obj
    return obj


def _nori(name, width, height, thickness, corner, seed, nori_edge, offset=1.0, lumps=0.35):
    """Nori sheet: an offset copy of the body below the (slightly irregular) edge, with wrinkles."""
    bm = _body_bmesh(width, height, thickness, corner, width * 0.045, seed, lumps=lumps)
    for v in bm.verts:
        v.co += v.normal * offset * MM
    bm.normal_update()
    # cut with a plane, then follow the irregular edge by deleting faces above it
    geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
    edge_z = nori_edge(Vector((0, 0, 0)))
    bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-7, plane_co=(0, 0, edge_z * MM), plane_no=(0, 0, 1),
                           clear_outer=True)
    for v in bm.verts:
        p = v.co / MM
        if abs(p.z - edge_z) < 0.05:                       # irregular torn edge
            v.co.z = nori_edge(p) * MM
    # wrinkles and slight looseness
    off = Vector((seed * 3.1, seed * 1.7, seed * 5.3))
    bm.normal_update()
    for v in bm.verts:
        p = v.co / MM
        wave = noise.noise(p * 0.05 + off) * 0.25
        crease = sum(max(0.0, 0.25 - abs((p.x * math.cos(a) + p.z * math.sin(a)) * 0.08 + o) * 2.0)
                     for a, o in ((0.4 + seed, 0.3), (2.1 + seed, -0.7)))   # two soft straight creases
        v.co += v.normal * (wave + crease) * MM
    mesh = bpy.data.meshes.new(name + '_Nori')
    bm.to_mesh(mesh)
    bm.free()
    mesh.shade_smooth()
    obj = bpy.data.objects.new(name + '_Nori', mesh)
    mod = obj.modifiers.new('Thickness', 'SOLIDIFY')
    mod.thickness = 0.18 * MM
    mod.offset = 1.0
    mesh.materials.append(materials.nori(f'{name}_Nori'))
    return obj


def _package(name, width, height, thickness, corner, seed, sticker: StickerDesign):
    """Outer film (taut on the faces, crinkled at the folded corners), pull tabs and the front sticker."""
    rng = random.Random(seed + 101)
    gap = 3.0
    bm = _body_bmesh(width, height, thickness, corner, width * 0.045, seed, lumps=0.0)
    for v in bm.verts:
        v.co += v.normal * gap * MM
    bm.normal_update()
    centers = [(-width / 2 + corner, corner), (width / 2 - corner, corner), (0.0, height - corner)]
    off = Vector((rng.uniform(0, 99), rng.uniform(0, 99), rng.uniform(0, 99)))
    sw, sh = 52.0, 26.0                                              # sticker size (mm)
    zc = height * 0.4
    for v in bm.verts:
        p = v.co / MM
        d = min(math.hypot(p.x - cx, p.z - cz) for cx, cz in centers)
        fold = max(0.0, 1.0 - d / (corner + 6.0)) ** 1.5             # film is gathered at the corners
        crinkle = (1.0 - abs(noise.noise(p * 0.35 + off))) ** 3 * 1.4  # sharp ridges
        # the stiff sticker keeps the film under it flat
        under = p.y < 0 and abs(p.x) < sw / 2 + 2 and abs(p.z - zc) < sh / 2 + 2
        v.co += v.normal * ((0.0 if under else fold * crinkle) + noise.noise(p * 0.08 + off) * 0.25) * MM
    bm.normal_update()
    from mathutils.bvhtree import BVHTree
    tree = BVHTree.FromBMesh(bm)
    film_mesh = bpy.data.meshes.new(name + '_Film')
    bm.to_mesh(film_mesh)
    bm.free()
    film_mesh.shade_smooth()
    film = bpy.data.objects.new(name + '_Film', film_mesh)
    film_mesh.materials.append(materials.wrap_film(f'{name}_Film'))
    parts = [film]

    # pull tabs: 1 at the apex (tear strip), 2 and 3 at the bottom corners
    cx = sum(c[0] for c in centers) / 3
    cz = sum(c[1] for c in centers) / 3
    tab_specs = [(centers[2], (0.55, 0.01, 0.02), '1'), (centers[0], (0.02, 0.12, 0.45), '2'),
                 (centers[1], (0.02, 0.12, 0.45), '3')]
    for (px, pz), color, label in tab_specs:
        dx, dz = px - cx, pz - cz
        ln = math.hypot(dx, dz)
        ux, uz = dx / ln, dz / ln                                    # outward
        vx, vz = -uz, ux                                             # across
        outline = []
        start, length = corner + gap - 1.5, 7.5
        for i in range(13):                                          # tapered tab with a rounded end
            a = math.pi * i / 12
            u = start + length + 3.5 * math.sin(a)
            v = 4.5 * math.cos(a)
            outline.append((u, v))
        outline += [(start, -7.0), (start, 7.0)]
        pts = [((px + ux * u + vx * v) * MM, (pz + uz * u + vz * v) * MM) for u, v in outline]
        mesh = mu.outline_mesh(f'{name}_Tab{label}', [pts], 0.15 * MM)
        mesh.transform(mathutils.Matrix.Rotation(math.radians(90), 4, 'X'))
        for vtx in mesh.vertices:                                    # slight wave
            vtx.co.y += noise.noise(Vector(vtx.co) * 400 + off) * 0.4 * MM
        mesh.materials.append(materials.tinted_film(f'{name}_Tab{label}', color))
        parts.append(bpy.data.objects.new(f'{name}_Tab{label}', mesh))

    # front sticker, projected onto the film
    nx, nz = 26, 13
    hits = {}
    for j in range(-1, nz + 2):
        for i in range(-1, nx + 2):
            x = (-sw / 2 + sw * i / nx) * MM
            z = (zc - sh / 2 + sh * j / nz) * MM
            hit = tree.ray_cast(Vector((x, -0.3, z)), Vector((0, 1, 0)))
            hits[i, j] = hit[0].y if hit[0] is not None else -(thickness / 2 + gap) * MM
    verts, faces, uvs = [], [], []
    for j in range(nz + 1):
        for i in range(nx + 1):
            # rest on the highest film point around (the film between samples must not poke through)
            y = min(hits[i + di, j + dj] for di in (-1, 0, 1) for dj in (-1, 0, 1)) - 0.3 * MM
            verts.append(((-sw / 2 + sw * i / nx) * MM, y, (zc - sh / 2 + sh * j / nz) * MM))
            uvs.append((i / nx, j / nz))
    for j in range(nz):
        for i in range(nx):
            a = j * (nx + 1) + i
            faces.append((a, a + 1, a + nx + 2, a + nx + 1))
    mesh = bpy.data.meshes.new(name + '_Sticker')
    mesh.from_pydata(verts, [], faces)
    uv = mesh.uv_layers.new(name='UVMap')
    for poly in mesh.polygons:
        poly.use_smooth = True
        for li in poly.loop_indices:
            uv.data[li].uv = uvs[mesh.loops[li].vertex_index]
    image = render_sticker(sticker, sw, sh, name=f'{name}_StickerImage')
    mesh.materials.append(materials.printed_paper(f'{name}_Sticker', image))
    parts.append(bpy.data.objects.new(name + '_Sticker', mesh))
    return parts
