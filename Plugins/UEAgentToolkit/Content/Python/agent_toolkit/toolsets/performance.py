"""PerformanceTools: static analysis of likely performance problems (read-only; never optimizes
automatically). Runtime GPU/draw-call statistics are not readable from Python; use the
editor's stat commands / Unreal Insights for those.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict

import unreal

from agent_toolkit.core import bp as bpu
from agent_toolkit.core import deps, editor, native, resolve
from agent_toolkit.core.serialize import to_jsonable
from agent_toolkit.core.tooling import agent_tool, ctx

_BYTES_PER_PIXEL = {'TC_DEFAULT': 1.0, 'TC_NORMALMAP': 1.0, 'TC_MASKS': 1.0, 'TC_GRAYSCALE': 1.0,
                    'TC_HDR': 8.0, 'TC_HDR_COMPRESSED': 1.0, 'TC_EDITOR_ICON': 4.0, 'TC_ALPHA': 1.0,
                    'TC_VECTOR_DISPLACEMENTMAP': 4.0, 'TC_BC7': 1.0}


def _tris(mesh: unreal.StaticMesh) -> int:
    try:
        return int(mesh.get_num_triangles(0))
    except Exception:  # pylint: disable=broad-exception-caught
        return 0


def _nanite(mesh: unreal.StaticMesh) -> bool:
    try:
        return bool(mesh.get_editor_property('nanite_settings').get_editor_property('enabled'))
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _texture_estimate(tex: unreal.Texture2D) -> tuple[int, int, float]:
    w, h = int(tex.blueprint_get_size_x()), int(tex.blueprint_get_size_y())
    compression = str(tex.get_editor_property('compression_settings')).split('.')[-1]
    bpp = _BYTES_PER_PIXEL.get(compression, 1.0)
    return w, h, w * h * bpp * 1.33 / (1024 * 1024)


@unreal.uclass()
class PerformanceTools(unreal.ToolsetDefinition):
    """Finds performance risks: level statistics (actors, triangles, lights, ticking, Niagara),
    Blueprints using Event Tick, high-poly non-Nanite meshes, oversized textures (memory
    estimate) and expensive materials. Reports candidates and warnings only."""

    @agent_tool()
    def get_level_performance_stats(top: int = 10) -> dict:
        """Static statistics for the open level with warnings: actor/component counts, triangle
        totals (LOD0, Nanite separated), heaviest meshes by triangles x instances, lights
        (movable / shadow casting), ticking actors and Niagara components.

        Args:
            top: Number of heaviest meshes listed.
        """
        actors = resolve.all_level_actors()
        mesh_usage: Counter = Counter()
        mesh_tris: dict[str, int] = {}
        nanite_meshes = set()
        comp_count = 0
        ticking = 0
        niagara = 0
        lights = {'total': 0, 'movable': 0, 'shadow_casting_movable': 0}
        for a in actors:
            comps = a.get_components_by_class(unreal.ActorComponent) or []
            comp_count += len(comps)
            if a.is_actor_tick_enabled():
                ticking += 1
            for c in comps:
                if isinstance(c, unreal.StaticMeshComponent):
                    mesh = c.get_editor_property('static_mesh')
                    if mesh is None:
                        continue
                    key = mesh.get_outermost().get_name()
                    count = c.get_instance_count() if isinstance(c, unreal.InstancedStaticMeshComponent) else 1
                    mesh_usage[key] += count
                    if key not in mesh_tris:
                        mesh_tris[key] = _tris(mesh)
                        if _nanite(mesh):
                            nanite_meshes.add(key)
                elif isinstance(c, unreal.LightComponent):
                    lights['total'] += 1
                    if c.get_editor_property('mobility') == unreal.ComponentMobility.MOVABLE:
                        lights['movable'] += 1
                        if c.get_editor_property('cast_shadows'):
                            lights['shadow_casting_movable'] += 1
                elif isinstance(c, unreal.NiagaraComponent):
                    niagara += 1
        totals = {'non_nanite': 0, 'nanite': 0}
        heavy = []
        for key, count in mesh_usage.items():
            t = mesh_tris.get(key, 0) * count
            totals['nanite' if key in nanite_meshes else 'non_nanite'] += t
            heavy.append({'mesh': key, 'triangles': mesh_tris.get(key, 0), 'instances': count, 'total': t,
                          'nanite': key in nanite_meshes})
        heavy.sort(key=lambda x: (not x['nanite'], x['total']), reverse=True)
        heavy = [h for h in heavy if not h['nanite']][:top] + [h for h in heavy if h['nanite']][:3]
        warnings = []
        if totals['non_nanite'] > 5_000_000:
            warnings.append(f"{totals['non_nanite']:,} non-Nanite triangles in view-independent total; consider Nanite/LODs.")
        if lights['shadow_casting_movable'] > 8:
            warnings.append(f"{lights['shadow_casting_movable']} movable shadow-casting lights (shadow cost).")
        if ticking > 200:
            warnings.append(f'{ticking} actors have tick enabled.')
        if niagara > 100:
            warnings.append(f'{niagara} Niagara components placed.')
        for w in warnings:
            ctx().warn(w, 'PERFORMANCE')
        world = editor.editor_world()
        ctx().set_target(world.get_outermost().get_name() if world else '')
        return {'actors': len(actors), 'components': comp_count, 'triangles_lod0': totals, 'unique_meshes': len(mesh_usage),
                'heaviest_meshes': heavy, 'lights': lights, 'ticking_actors': ticking, 'niagara_components': niagara,
                'warnings': warnings}

    @agent_tool(allow_during_pie=True)
    def get_frame_stats(samples: int = 1) -> dict:
        """Returns last-frame runtime timings: FPS, game/render/RHI thread ms, GPU ms, draw calls and
        primitives (requires the native plugin; meaningful while the editor renders, ideally during PIE).

        Args:
            samples: Number of reads (one per call tick; >1 returns min/avg/max of repeated reads).
        """
        native.require_graph('Runtime frame statistics')
        reads = [json.loads(unreal.AgentToolkitWorldLibrary.get_frame_stats()) for _ in range(max(1, samples))]
        stats = reads[-1]
        if len(reads) > 1:
            stats['samples'] = len(reads)
        if not stats.get('rendering') or stats.get('gpu_ms', 0) == 0:
            ctx().warn('No rendering data (null RHI or no frames rendered yet). Run with rendering / in PIE.',
                       'NO_RENDER_STATS')
        bound = max(('game_thread_ms', 'render_thread_ms', 'gpu_ms'), key=lambda k: stats.get(k, 0))
        stats['likely_bottleneck'] = bound.replace('_ms', '')
        return stats

    @agent_tool()
    def find_blueprint_tick_usage(path: str = '/Game', limit: int = 200) -> dict:
        """Lists Blueprints that implement Event Tick (connected or empty) — a common CPU cost.

        Args:
            path: Folder to scan.
            limit: Maximum Blueprints loaded.
        """
        results = []
        for data in deps.assets_in_path(path, True, ['Blueprint', 'WidgetBlueprint'])[:limit]:
            bp = data.get_asset()
            if not isinstance(bp, unreal.Blueprint):
                continue
            for graph in bpu.graphs(bp):
                for node in bpu.graph_nodes(graph):
                    if node.get_class().get_name() == 'K2Node_Event' and 'Tick' in str(node.get_node_title()):
                        connected = any(p.list_connected_pins() for p in node.list_all_pins() or [])
                        results.append({'blueprint': str(data.package_name), 'graph': graph.get_name(),
                                        'connected': connected})
        ctx().set_target(path)
        return {'count': len(results), 'blueprints': results,
                'hint': 'Prefer timers/events; disable tick (actor tick enabled=false) when unused.'}

    @agent_tool()
    def find_heavy_assets(path: str = '/Game', triangle_threshold: int = 100000, texture_size_threshold: int = 4096,
                          material_instruction_threshold: int = 500, limit: int = 300) -> dict:
        """Finds heavy asset candidates: non-Nanite static meshes above a triangle count, textures
        at/above a resolution (with memory estimate) and materials above a pixel-shader instruction count.

        Args:
            path: Folder to scan.
            triangle_threshold: LOD0 triangles for non-Nanite meshes.
            texture_size_threshold: Width or height in pixels.
            material_instruction_threshold: Pixel shader instructions.
            limit: Maximum assets loaded per category.
        """
        meshes, textures, materials = [], [], []
        texture_mb = 0.0
        for data in deps.assets_in_path(path, True, ['StaticMesh'])[:limit]:
            mesh = data.get_asset()
            if mesh and not _nanite(mesh) and _tris(mesh) >= triangle_threshold:
                meshes.append({'mesh': str(data.package_name), 'triangles': _tris(mesh),
                               'lods': int(mesh.get_num_lods())})
        for data in deps.assets_in_path(path, True, ['Texture2D'])[:limit]:
            tex = data.get_asset()
            if tex is None:
                continue
            w, h, mb = _texture_estimate(tex)
            texture_mb += mb
            if max(w, h) >= texture_size_threshold:
                textures.append({'texture': str(data.package_name), 'size': [w, h], 'estimated_mb': round(mb, 2)})
        for data in deps.assets_in_path(path, True, ['Material'])[:limit]:
            mat = data.get_asset()
            try:
                stats = unreal.MaterialEditingLibrary.get_statistics(mat)
                ps = int(stats.get_editor_property('num_pixel_shader_instructions'))
            except Exception:  # pylint: disable=broad-exception-caught
                continue
            if ps >= material_instruction_threshold:
                materials.append({'material': str(data.package_name), 'pixel_instructions': ps,
                                  'texture_samples': to_jsonable(stats.get_editor_property('num_pixel_texture_samples'))})
        ctx().set_target(path)
        return {'meshes': sorted(meshes, key=lambda m: -m['triangles']),
                'textures': sorted(textures, key=lambda t: -t['estimated_mb']),
                'materials': sorted(materials, key=lambda m: -m['pixel_instructions']),
                'texture_memory_estimate_mb': round(texture_mb, 1),
                'note': 'Estimates from asset settings (LOD0 tris, compression). Not runtime measurements.'}
