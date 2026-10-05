"""Preview renders for checking generated assets: a procedural studio (gradient world, three area
lights, ground) and cameras framed from the objects' bounds. No external HDRIs.
"""

from __future__ import annotations

import math
import os

import bpy
import mathutils

from . import materials

VIEWS = {
    # name: (azimuth deg around Z from -Y, elevation deg, zoom factor, target: 'center'|'top'|'bottom')
    'front': (0.0, 8.0, 1.0, 'center'),
    'three_quarter': (35.0, 22.0, 1.0, 'center'),
    'back': (180.0, 8.0, 1.0, 'center'),
    'top': (20.0, 62.0, 0.42, 'top'),
    'top_closeup': (10.0, 48.0, 0.3, 'top'),
    'bottom': (20.0, -55.0, 0.5, 'bottom'),
}


def _bounds(objects):
    pts = [o.matrix_world @ mathutils.Vector(c) for o in objects for c in o.bound_box]
    lo = mathutils.Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = mathutils.Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def _world(scene):
    world = bpy.data.worlds.get('_preview_world') or bpy.data.worlds.new('_preview_world')
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputWorld')
    bg = nt.nodes.new('ShaderNodeBackground')
    tc = nt.nodes.new('ShaderNodeTexCoord')
    sep = nt.nodes.new('ShaderNodeSeparateXYZ')
    ramp = nt.nodes.new('ShaderNodeValToRGB')
    nt.links.new(tc.outputs['Generated'], sep.inputs['Vector'])
    nt.links.new(sep.outputs['Z'], ramp.inputs['Fac'])
    ramp.color_ramp.elements[0].position = 0.35
    ramp.color_ramp.elements[0].color = (0.12, 0.12, 0.125, 1)
    ramp.color_ramp.elements[1].position = 0.75
    ramp.color_ramp.elements[1].color = (0.55, 0.57, 0.6, 1)
    nt.links.new(ramp.outputs['Color'], bg.inputs['Color'])
    bg.inputs['Strength'].default_value = 0.8
    nt.links.new(bg.outputs['Background'], out.inputs['Surface'])
    scene.world = world


def _light(scene, name, location, target, size, energy, collection):
    data = bpy.data.lights.new(name, 'AREA')
    data.size = size
    data.energy = energy
    obj = bpy.data.objects.new(name, data)
    collection.objects.link(obj)
    obj.location = location
    direction = mathutils.Vector(target) - mathutils.Vector(location)
    obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    return obj


def setup_studio(objects, scene=None):
    """Adds world, lights and a ground plane sized to the objects. Returns the studio collection."""
    scene = scene or bpy.context.scene
    coll = bpy.data.collections.get('_PreviewStudio')
    if coll is None:
        coll = bpy.data.collections.new('_PreviewStudio')
        scene.collection.children.link(coll)
    for obj in list(coll.objects):
        bpy.data.objects.remove(obj)
    lo, hi = _bounds(objects)
    center = (lo + hi) / 2
    size = max((hi - lo).length, 0.01)
    _world(scene)
    _light(scene, '_key', center + mathutils.Vector((-1.6, -2.0, 1.8)) * size, center, size * 1.6, 120 * size ** 2, coll)
    _light(scene, '_fill', center + mathutils.Vector((2.2, -1.2, 0.6)) * size, center, size * 2.5, 40 * size ** 2, coll)
    _light(scene, '_rim', center + mathutils.Vector((0.4, 2.4, 1.6)) * size, center, size * 1.2, 90 * size ** 2, coll)
    ground = bpy.data.meshes.new('_ground')
    s = size * 20
    ground.from_pydata([(-s, -s, 0), (s, -s, 0), (s, s, 0), (-s, s, 0)], [], [(0, 1, 2, 3)])
    ground.materials.append(materials.matte('_preview_ground', (0.18, 0.18, 0.19), 0.6))
    gobj = bpy.data.objects.new('_ground', ground)
    gobj.location.z = lo.z - 0.0001
    coll.objects.link(gobj)
    return coll


def render_views(objects, out_dir: str, views=('front', 'three_quarter', 'top_closeup', 'bottom'),
                 resolution: int = 900, samples: int = 64, scene=None) -> list[str]:
    """Renders the given views to PNG files in out_dir and returns their paths."""
    scene = scene or bpy.context.scene
    os.makedirs(out_dir, exist_ok=True)
    coll = setup_studio(objects, scene)
    ground = coll.objects.get('_ground')
    lo, hi = _bounds(objects)
    center = (lo + hi) / 2
    radius = (hi - lo).length / 2
    cam_data = bpy.data.cameras.new('_preview_cam')
    cam_data.lens = 85
    cam = bpy.data.objects.new('_preview_cam', cam_data)
    coll.objects.link(cam)
    scene.camera = cam
    r = scene.render
    r.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    r.resolution_x = r.resolution_y = resolution
    r.resolution_percentage = 100
    r.image_settings.file_format = 'PNG'
    scene.view_settings.view_transform = 'AgX'
    scene.view_settings.look = 'AgX - Punchy'
    paths = []
    for name in views:
        az, el, zoom, target = VIEWS[name]
        aim = mathutils.Vector(center)
        if target == 'top':
            aim.z = hi.z - (hi.z - lo.z) * 0.03
        elif target == 'bottom':
            aim.z = lo.z + (hi.z - lo.z) * 0.03
        ground.hide_render = el < 0
        low = scene.world.node_tree.nodes['Color Ramp'].color_ramp.elements[0]
        low.color = (0.45, 0.45, 0.47, 1) if el < 0 else (0.12, 0.12, 0.125, 1)  # light the underside
        dist = radius * zoom / math.tan(math.atan(18 / cam_data.lens)) * 1.15
        a, e = math.radians(az), math.radians(el)
        offset = mathutils.Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e))) * dist
        cam.location = aim + offset
        cam.rotation_euler = (-offset).to_track_quat('-Z', 'Y').to_euler()
        cam_data.clip_start, cam_data.clip_end = dist * 0.01, dist * 10
        path = os.path.join(out_dir, f'{name}.png')
        r.filepath = path
        bpy.ops.render.render(write_still=True, scene=scene.name)
        paths.append(path)
    ground.hide_render = False
    return paths
