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
