"""Procedural materials (no external textures).

Each material adds the small variations that keep surfaces from looking "flat CG": roughness
variation from noise, micro bump, and layered coats where real objects have them.
"""

from __future__ import annotations

import bpy


def _new(name: str):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    out.location = (400, 0)
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.location = (100, 0)
    nt.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
    return mat, nt, bsdf


def _noise(nt, scale: float, detail: float = 4.0, coord: str = 'Object', loc=(-700, 0)):
    tc = nt.nodes.new('ShaderNodeTexCoord')
    tc.location = (loc[0] - 250, loc[1])
    noise = nt.nodes.new('ShaderNodeTexNoise')
    noise.location = loc
    noise.inputs['Scale'].default_value = scale
    noise.inputs['Detail'].default_value = detail
    nt.links.new(tc.outputs[coord], noise.inputs['Vector'])
    return noise


def _map_range(nt, src, lo: float, hi: float, loc):
    mr = nt.nodes.new('ShaderNodeMapRange')
    mr.location = loc
    mr.inputs['To Min'].default_value = lo
    mr.inputs['To Max'].default_value = hi
    nt.links.new(src, mr.inputs['Value'])
    return mr.outputs['Result']


def _micro_bump(nt, bsdf, scale: float, strength: float, distance: float = 0.0002, loc=(-450, -350)):
    noise = _noise(nt, scale, 8.0, loc=(loc[0] - 250, loc[1]))
    bump = nt.nodes.new('ShaderNodeBump')
    bump.location = loc
    bump.inputs['Strength'].default_value = strength
    bump.inputs['Distance'].default_value = distance
    nt.links.new(noise.outputs['Fac'], bump.inputs['Height'])
    nt.links.new(bump.outputs['Normal'], bsdf.inputs['Normal'])


def aluminum(name: str, tint: tuple | None = None) -> bpy.types.Material:
    """Formed aluminum: bright metal, slightly anisotropic, with roughness variation and micro bump."""
    mat, nt, bsdf = _new(name)
    color = tint or (0.91, 0.92, 0.93)
    bsdf.inputs['Base Color'].default_value = (*color, 1.0)
    bsdf.inputs['Metallic'].default_value = 1.0
    noise = _noise(nt, 180.0, 6.0)
    nt.links.new(_map_range(nt, noise.outputs['Fac'], 0.14, 0.30, (-400, 0)), bsdf.inputs['Roughness'])
    bsdf.inputs['Anisotropic'].default_value = 0.25
    _micro_bump(nt, bsdf, 2500.0, 0.02)
    return mat


def printed_label(name: str, image) -> bpy.types.Material:
    """Ink printed on aluminum under a glossy lacquer: image color, partly metallic, clear coat."""
    mat, nt, bsdf = _new(name)
    tex = nt.nodes.new('ShaderNodeTexImage')
    tex.location = (-450, 250)
    tex.image = image
    tex.interpolation = 'Cubic'
    nt.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
    bsdf.inputs['Metallic'].default_value = 0.15
    noise = _noise(nt, 120.0, 5.0, loc=(-700, -100))
    nt.links.new(_map_range(nt, noise.outputs['Fac'], 0.22, 0.34, (-400, -100)), bsdf.inputs['Roughness'])
    bsdf.inputs['Coat Weight'].default_value = 0.7
    bsdf.inputs['Coat Roughness'].default_value = 0.04
    _micro_bump(nt, bsdf, 1500.0, 0.012)
    return mat


def emission(name: str, color: tuple, strength: float = 1.0) -> bpy.types.Material:
    """Flat unlit color (used when rendering labels)."""
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    em = nt.nodes.new('ShaderNodeEmission')
    em.inputs['Color'].default_value = (*color[:3], 1.0)
    em.inputs['Strength'].default_value = strength
    nt.links.new(em.outputs['Emission'], out.inputs['Surface'])
    return mat


def matte(name: str, color: tuple, roughness: float = 0.8) -> bpy.types.Material:
    mat, nt, bsdf = _new(name)
    bsdf.inputs['Base Color'].default_value = (*color[:3], 1.0)
    bsdf.inputs['Roughness'].default_value = roughness
    return mat


def rice(name: str, base: bool = False) -> bpy.types.Material:
    """Cooked rice: translucent (subsurface), slightly glossy from moisture. base=True is the packed
    rice seen between grains (darker, less glossy)."""
    mat, nt, bsdf = _new(name)
    color = (0.72, 0.7, 0.64) if base else (0.94, 0.92, 0.87)
    noise = _noise(nt, 900.0, 3.0, loc=(-700, 200))
    ramp = nt.nodes.new('ShaderNodeValToRGB')
    ramp.location = (-450, 200)
    ramp.color_ramp.elements[0].color = (color[0] * 0.92, color[1] * 0.9, color[2] * 0.85, 1)
    ramp.color_ramp.elements[1].color = (*color, 1)
    if base:
        nt.links.new(noise.outputs['Fac'], ramp.inputs['Fac'])
    else:                                   # each grain slightly different (attribute set per grain)
        attr = nt.nodes.new('ShaderNodeAttribute')
        attr.location = (-700, 450)
        attr.attribute_name = 'grain_random'
        mix = nt.nodes.new('ShaderNodeMath')
        mix.location = (-600, 300)
        mix.operation = 'MULTIPLY_ADD'
        mix.inputs[1].default_value = 0.7
        nt.links.new(attr.outputs['Fac'], mix.inputs[0])
        nt.links.new(noise.outputs['Fac'], mix.inputs[2])
        clamp = _map_range(nt, mix.outputs['Value'], 0.0, 1.0, (-520, 450))
        nt.links.new(clamp, ramp.inputs['Fac'])
    nt.links.new(ramp.outputs['Color'], bsdf.inputs['Base Color'])
    bsdf.inputs['Subsurface Weight'].default_value = 0.35 if base else 1.0
    bsdf.inputs['Subsurface Radius'].default_value = (1.0, 0.85, 0.6)
    bsdf.inputs['Subsurface Scale'].default_value = 0.0025
    bsdf.inputs['Roughness'].default_value = 0.6 if base else 0.3
    bsdf.inputs['Coat Weight'].default_value = 0.0 if base else 0.45   # moist, sticky surface
    bsdf.inputs['Coat Roughness'].default_value = 0.08
    return mat


def nori(name: str) -> bpy.types.Material:
    """Roasted nori: near-black green with a fibrous sheet texture, faint sheen and lighter specks."""
    mat, nt, bsdf = _new(name)
    tc = nt.nodes.new('ShaderNodeTexCoord')
    tc.location = (-1200, 0)
    mapping = nt.nodes.new('ShaderNodeMapping')
    mapping.location = (-1000, 0)
    mapping.inputs['Scale'].default_value = (1.0, 1.0, 6.0)          # stretched fibres
    nt.links.new(tc.outputs['Object'], mapping.inputs['Vector'])
    fibres = nt.nodes.new('ShaderNodeTexNoise')
    fibres.location = (-800, 0)
    fibres.inputs['Scale'].default_value = 600.0
    fibres.inputs['Detail'].default_value = 10.0
    nt.links.new(mapping.outputs['Vector'], fibres.inputs['Vector'])
    specks = nt.nodes.new('ShaderNodeTexVoronoi')
    specks.location = (-800, 300)
    specks.inputs['Scale'].default_value = 900.0
    nt.links.new(tc.outputs['Object'], specks.inputs['Vector'])
    speck_mask = _map_range(nt, specks.outputs['Distance'], 1.0, 0.0, (-600, 300))
    ramp = nt.nodes.new('ShaderNodeValToRGB')
    ramp.location = (-400, 300)
    ramp.color_ramp.elements[0].position = 0.9
    ramp.color_ramp.elements[0].color = (0.012, 0.02, 0.01, 1)
    ramp.color_ramp.elements[1].color = (0.05, 0.08, 0.035, 1)
    nt.links.new(speck_mask, ramp.inputs['Fac'])
    nt.links.new(ramp.outputs['Color'], bsdf.inputs['Base Color'])
    nt.links.new(_map_range(nt, fibres.outputs['Fac'], 0.35, 0.6, (-400, 0)), bsdf.inputs['Roughness'])
    bsdf.inputs['Sheen Weight'].default_value = 0.3
    bsdf.inputs['Sheen Tint'].default_value = (0.4, 0.5, 0.35, 1)
    bump = nt.nodes.new('ShaderNodeBump')
    bump.location = (-200, -300)
    bump.inputs['Strength'].default_value = 0.25
    bump.inputs['Distance'].default_value = 0.0003
    nt.links.new(fibres.outputs['Fac'], bump.inputs['Height'])
    nt.links.new(bump.outputs['Normal'], bsdf.inputs['Normal'])
    return mat
