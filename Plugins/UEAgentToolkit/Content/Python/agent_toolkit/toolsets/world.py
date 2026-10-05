"""WorldTools: Landscape creation (flat or heightmap), landscape inspection/material, Foliage
types and instance placement (explicit transforms or ground-traced scattering).

Landscape creation uses the UEAgentToolkitNative plugin; foliage uses the engine Python API.
Sculpting/painting brushes are not automated.
"""

from __future__ import annotations

import json
import math
import random

import unreal

from agent_toolkit.core import editor, native, resolve
from agent_toolkit.core.errors import Code, ToolError
from agent_toolkit.core.serialize import parse_json_arg, split_csv, to_jsonable
from agent_toolkit.core.tooling import agent_tool, ctx


def _landscapes() -> list:
    """Top-level landscapes (World Partition streaming proxies are summarized by their landscape)."""
    actors = [a for a in resolve.all_level_actors() if isinstance(a, unreal.LandscapeProxy)]
    main = [a for a in actors if isinstance(a, unreal.Landscape)]
    return main or actors


def _find_landscape(name: str | None):
    items = _landscapes()
    if not items:
        raise ToolError(Code.OBJECT_NOT_FOUND, 'The level has no landscape', likely_causes=['Use create_landscape.'])
    if not name:
        return items[0]
    for a in items:
        if name in (a.get_actor_label(), a.get_name(), a.get_path_name()):
            return a
    raise ToolError(Code.ACTOR_NOT_FOUND, f'Landscape {name!r} not found',
                    likely_causes=[f'Landscapes: {[a.get_actor_label() for a in items]}'])


def _world_lib():
    native.require_graph('Landscape editing')
    return unreal.AgentToolkitWorldLibrary


def _brush_points(center: list[float], path_json: str | None, radius: float) -> list[tuple[float, float]]:
    """Brush stamp positions: the center, plus points every radius/2 along an optional polyline."""
    center = list(center or [])
    if len(center) < 2:
        raise ToolError(Code.INVALID_ARGUMENT, 'center must be [x, y]')
    pts = [(float(center[0]), float(center[1]))]
    for p in parse_json_arg(path_json, 'path_json', list) if path_json else []:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            raise ToolError(Code.INVALID_ARGUMENT, 'path_json must be [[x, y], ...]')
        pts.append((float(p[0]), float(p[1])))
    step = max(radius / 2.0, 1.0)
    stamps = [pts[0]]
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        n = max(1, int(math.ceil(math.hypot(bx - ax, by - ay) / step)))
        stamps += [(ax + (bx - ax) * i / n, ay + (by - ay) * i / n) for i in range(1, n + 1)]
    if len(stamps) > 2000:
        raise ToolError(Code.INVALID_ARGUMENT, f'Path needs {len(stamps)} brush stamps (max 2000); use a larger radius')
    return stamps


def _sample(lib, target, point, layer_name: str | None = None) -> dict:
    if not isinstance(point, (list, tuple)) or len(point) < 2:
        raise ToolError(Code.INVALID_ARGUMENT, 'points must be [x, y]')
    raw = native.check(lib.landscape_sample(target, unreal.Vector(float(point[0]), float(point[1]), 0),
                                            unreal.Name(layer_name or 'None')), target.get_actor_label())
    return json.loads(raw)


def _ground_z(world, x: float, y: float, top: float, bottom: float) -> tuple[float, unreal.Vector] | None:
    hit = unreal.SystemLibrary.line_trace_single(world, unreal.Vector(x, y, top), unreal.Vector(x, y, bottom),
                                                 unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, True, [],
                                                 unreal.DrawDebugTrace.NONE, True)
    if hit is None:
        return None
    t = hit.to_tuple()
    blocking = t[0]
    if not blocking:
        return None
    return t[4].z, t[7]  # impact point z, impact normal


@unreal.uclass()
class WorldTools(unreal.ToolsetDefinition):
    """Terrain and vegetation: create landscapes (flat or from a heightmap) with a material,
    inspect landscapes, create foliage types, place foliage instances explicitly or scattered on
    the ground, and remove/inspect foliage."""

    @agent_tool(mutates=True)
    def create_landscape(location: list[float], components_x: int = 8, components_y: int = 8,
                         quads_per_section: int = 63, sections_per_component: int = 1,
                         scale: str | None = None, heightmap_file: str | None = None,
                         material_path: str | None = None) -> dict:
        """Creates a landscape centered at location. Default 8x8 components of 63 quads = 505x505
        vertices (~504 m at scale 100).

        Args:
            location: Center [x, y, z].
            components_x: Components along X (1..32).
            components_y: Components along Y (1..32).
            quads_per_section: 7, 15, 31, 63, 127 or 255.
            sections_per_component: 1 or 2.
            scale: Actor scale "x,y,z" (default "100,100,100"; Z 100 = +-256 m height range).
            heightmap_file: Optional 16-bit .r16/.raw or grayscale .png (resampled to fit).
            material_path: Optional landscape material.
        """
        lib = native.require_graph('Landscape creation')
        mat = resolve.load_asset(material_path, unreal.MaterialInterface) if material_path else None
        s = [float(v) for v in split_csv(scale)] if scale else [100.0, 100.0, 100.0]
        if len(s) != 3:
            raise ToolError(Code.INVALID_ARGUMENT, 'scale must be "x,y,z"')
        landscape, error = unreal.AgentToolkitWorldLibrary.create_landscape(
            unreal.Vector(*location), unreal.Vector(*s), components_x, components_y, quads_per_section,
            sections_per_component, heightmap_file or '', mat)
        del lib
        if landscape is None:
            raise ToolError(Code.INVALID_ARGUMENT if 'must' in str(error) else Code.UE_OPERATION_FAILED, str(error))
        ctx().set_target(landscape.get_path_name())
        quads = quads_per_section * sections_per_component
        return {'landscape': landscape.get_actor_label(), 'vertices': [components_x * quads + 1, components_y * quads + 1],
                'size_meters': [components_x * quads * s[0] / 100, components_y * quads * s[1] / 100],
                'material': to_jsonable(mat)}

    @agent_tool()
    def inspect_landscapes() -> dict:
        """Lists landscapes in the open level with bounds, component count and material."""
        out = []
        lib = getattr(unreal, 'AgentToolkitWorldLibrary', None)
        for a in _landscapes():
            origin, extent = a.get_actor_bounds(False)
            if lib is not None:
                info = json.loads(lib.describe_landscape(a))
                info.update({'class': a.get_class().get_name(), 'bounds_center': to_jsonable(origin),
                             'bounds_extent': to_jsonable(extent)})
                out.append(info)
                continue
            out.append({'label': a.get_actor_label(), 'class': a.get_class().get_name(),
                        'bounds_center': to_jsonable(origin), 'bounds_extent': to_jsonable(extent),
                        'components': len(a.get_components_by_class(unreal.LandscapeComponent) or []),
                        'material': to_jsonable(a.get_editor_property('landscape_material'))})
        return {'landscapes': out}

    @agent_tool(mutates=True)
    def set_landscape_material(material_path: str, landscape: str | None = None) -> dict:
        """Assigns a material to a landscape.

        Args:
            material_path: Landscape material (or material instance).
            landscape: Landscape label (default: first landscape).
        """
        land = _find_landscape(landscape)
        mat = resolve.load_asset(material_path, unreal.MaterialInterface)
        land.modify()
        land.set_editor_property('landscape_material', mat)
        ctx().set_target(land.get_path_name())
        return {'landscape': land.get_actor_label(), 'material': mat.get_path_name()}

    @agent_tool(mutates=True)
    def sculpt_landscape(center: list[float], radius: float, mode: str = 'raise', strength: float = 100.0,
                         falloff: float = 0.5, target_height: float | None = None, path_json: str | None = None,
                         landscape: str | None = None) -> dict:
        """Sculpts the landscape with a circular brush (world units): raise/lower hills, flatten a plateau
        or road, smooth bumps. With path_json the brush is stamped along a polyline (roads, rivers).

        Args:
            center: [x, y] world position of the brush (z ignored). With path_json, the first point.
            radius: Brush radius in world units (cm).
            mode: raise, lower, flatten or smooth.
            strength: raise/lower: height change in world units at the brush center. flatten/smooth: 0..1 blend.
            falloff: 0..1 soft edge (0 = hard edge, 1 = fully soft).
            target_height: flatten: world Z to flatten to (default: the height at the first brush point).
            path_json: Optional [[x, y], ...] further points; brush stamps every radius/2 along the path.
            landscape: Landscape label (default: the first landscape).
        """
        lib = _world_lib()
        target = _find_landscape(landscape)
        mode = mode.lower()
        if mode not in ('raise', 'lower', 'flatten', 'smooth'):
            raise ToolError(Code.INVALID_ARGUMENT, 'mode must be raise, lower, flatten or smooth')
        if radius <= 0:
            raise ToolError(Code.INVALID_ARGUMENT, 'radius must be > 0')
        points = _brush_points(center, path_json, radius)
        if mode == 'flatten' and target_height is None:
            target_height = _sample(lib, target, points[0])['z']
            ctx().warn(f'target_height not given: flattening to {target_height:.1f} (height at the first point)',
                       'TARGET_HEIGHT_DEFAULTED')
        if mode in ('flatten', 'smooth') and not 0 < strength <= 1:
            strength = 1.0
        stamps = []
        for x, y in points:
            stamps.append(native.check(lib.landscape_sculpt(target, unreal.Vector(x, y, 0), radius, falloff, mode,
                                                            strength, target_height or 0.0), target.get_actor_label()))
        out = [json.loads(s) for s in stamps]
        zs = [s for s in out if 'min_z' in s]
        return {'landscape': target.get_actor_label(), 'mode': mode, 'stamps': len(out),
                'vertices': sum(s['vertices'] for s in out),
                'min_z': min((s['min_z'] for s in zs), default=None), 'max_z': max((s['max_z'] for s in zs), default=None)}

    @agent_tool(mutates=True)
    def paint_landscape_layer(layer_name: str, center: list[float], radius: float, strength: float = 1.0,
                              falloff: float = 0.5, path_json: str | None = None,
                              layer_info_folder: str = '/Game/Landscape/LayerInfos',
                              landscape: str | None = None) -> dict:
        """Paints a landscape material layer (grass, rock, sand...) with a circular brush. Creates the
        Layer Info asset and target layer when missing. The landscape material must contain a
        Landscape Layer Blend with the same layer name for the paint to be visible.

        Args:
            layer_name: Layer name as used in the landscape material, e.g. "Grass".
            center: [x, y] world position of the brush.
            radius: Brush radius in world units.
            strength: 0..1 paint amount at the center; negative values erase.
            falloff: 0..1 soft edge.
            path_json: Optional [[x, y], ...] further points to paint along.
            layer_info_folder: Content folder for new Layer Info assets.
            landscape: Landscape label (default: the first landscape).
        """
        lib = _world_lib()
        target = _find_landscape(landscape)
        if radius <= 0 or not -1 <= strength <= 1 or strength == 0:
            raise ToolError(Code.INVALID_ARGUMENT, 'radius must be > 0 and strength in -1..1 (not 0)')
        out = []
        for x, y in _brush_points(center, path_json, radius):
            out.append(json.loads(native.check(lib.landscape_paint_layer(
                target, unreal.Name(layer_name), unreal.Vector(x, y, 0), radius, falloff, strength,
                layer_info_folder), target.get_actor_label())))
        material = target.get_editor_property('landscape_material')
        if material is None:
            ctx().warn('The landscape has no material: painted weights are stored but not visible', 'NO_MATERIAL')
        return {'landscape': target.get_actor_label(), 'layer': layer_name, 'layer_info': out[0]['layer_info'],
                'created_layer_info': any(o['created_layer_info'] for o in out), 'stamps': len(out),
                'vertices': sum(o['vertices'] for o in out)}

    @agent_tool()
    def sample_landscape(points_json: str, layer_name: str | None = None, landscape: str | None = None) -> dict:
        """Reads the landscape height (world Z) and optionally a paint layer weight (0..1) at points.

        Args:
            points_json: [[x, y], ...] world positions.
            layer_name: Optional paint layer to read.
            landscape: Landscape label (default: the first landscape).
        """
        lib = _world_lib()
        target = _find_landscape(landscape)
        points = parse_json_arg(points_json, 'points_json', list)
        samples = []
        for p in points[:500]:
            s = _sample(lib, target, p, layer_name)
            s['point'] = list(p)[:2]
            samples.append(s)
        return {'landscape': target.get_actor_label(), 'samples': samples,
                'layers': json.loads(lib.landscape_list_target_layers(target) or '[]')}

    @agent_tool(mutates=True)
    def create_foliage_type(asset_path: str, mesh_path: str, density: float = 100.0, scale_min: float = 0.8,
                            scale_max: float = 1.2, align_to_normal: bool = True, collision: bool = False) -> dict:
        """Creates a static-mesh Foliage Type asset.

        Args:
            asset_path: New asset path, e.g. /Game/Foliage/FT_Tree.
            mesh_path: Static mesh.
            density: Instances per 1000x1000 units when painting.
            scale_min: Minimum uniform scale.
            scale_max: Maximum uniform scale.
            align_to_normal: Align instances to the surface normal.
            collision: Enable collision on instances.
        """
        package, _ = resolve.normalize_asset_path(asset_path)
        if resolve.asset_exists(package):
            raise ToolError(Code.ALREADY_EXISTS, f'{package} already exists', target=package)
        mesh = resolve.load_asset(mesh_path, unreal.StaticMesh)
        folder, name = package.rsplit('/', 1)
        ft = editor.asset_tools().create_asset(name, folder, unreal.FoliageType_InstancedStaticMesh,
                                               unreal.FoliageType_InstancedStaticMeshFactory())
        if ft is None:
            raise ToolError(Code.UE_OPERATION_FAILED, f'Could not create {package}', target=package)
        ft.set_editor_property('mesh', mesh)
        ft.set_editor_property('density', density)
        ft.set_editor_property('align_to_normal', align_to_normal)
        try:
            ft.set_editor_property('scale_x', unreal.FloatInterval(scale_min, scale_max))
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        if not collision:
            try:
                body = ft.get_editor_property('body_instance')
                body.set_editor_property('collision_enabled', unreal.CollisionEnabled.NO_COLLISION)
                ft.set_editor_property('body_instance', body)
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        ctx().set_target(package)
        return {'asset': package, 'mesh': mesh.get_path_name()}

    @agent_tool(mutates=True)
    def add_foliage_instances(foliage_type_path: str, transforms_json: str) -> dict:
        """Adds foliage instances at explicit transforms.

        Args:
            foliage_type_path: Foliage Type asset.
            transforms_json: JSON list of {"location": [x,y,z], "rotation": {"yaw": 0}, "scale": [1,1,1]}.
        """
        ft = resolve.load_asset(foliage_type_path, unreal.FoliageType)
        items = parse_json_arg(transforms_json, 'transforms_json', list)
        transforms = []
        for item in items:
            rot = item.get('rotation', {})
            transforms.append(unreal.Transform(
                location=unreal.Vector(*item['location']),
                rotation=unreal.Rotator(roll=rot.get('roll', 0), pitch=rot.get('pitch', 0), yaw=rot.get('yaw', 0)),
                scale=unreal.Vector(*item.get('scale', [1, 1, 1]))))
        if not transforms:
            raise ToolError(Code.INVALID_ARGUMENT, 'transforms_json is empty')
        unreal.InstancedFoliageActor.add_instances(editor.editor_world(), ft, transforms)
        ctx().set_target(ft.get_path_name())
        ctx().mark_modified(True)
        return {'added': len(transforms)}

    @agent_tool(mutates=True)
    def scatter_foliage(foliage_type_path: str, center: list[float], radius: float, count: int,
                        random_yaw: bool = True, scale_min: float = 0.8, scale_max: float = 1.2,
                        min_spacing: float = 0.0, seed: int = 0) -> dict:
        """Scatters foliage instances in a circle, snapping each to the ground with a downward trace
        (points with no ground hit are skipped).

        Args:
            foliage_type_path: Foliage Type asset.
            center: Circle center [x, y, z] (z is the trace start height reference).
            radius: Circle radius (cm).
            count: Instances to attempt.
            random_yaw: Random rotation around Z.
            scale_min: Minimum uniform scale.
            scale_max: Maximum uniform scale.
            min_spacing: Minimum distance between instances (cm).
            seed: Random seed (0 = random).
        """
        ft = resolve.load_asset(foliage_type_path, unreal.FoliageType)
        if count < 1 or radius <= 0:
            raise ToolError(Code.INVALID_ARGUMENT, 'count must be >= 1 and radius > 0')
        rng = random.Random(seed or None)
        world = editor.editor_world()
        placed, points, misses = [], [], 0
        align = bool(ft.get_editor_property('align_to_normal')) if hasattr(ft, 'get_editor_property') else False
        for _ in range(count * 3):
            if len(placed) >= count:
                break
            ang, dist = rng.uniform(0, 2 * math.pi), radius * math.sqrt(rng.random())
            x, y = center[0] + dist * math.cos(ang), center[1] + dist * math.sin(ang)
            if min_spacing and any((x - px) ** 2 + (y - py) ** 2 < min_spacing ** 2 for px, py in points):
                continue
            ground = _ground_z(world, x, y, center[2] + 100000, center[2] - 100000)
            if ground is None:
                misses += 1
                continue
            z, normal = ground
            yaw = rng.uniform(0, 360) if random_yaw else 0
            rot = unreal.Rotator(roll=0, pitch=0, yaw=yaw)
            if align and normal is not None:
                rot = unreal.MathLibrary.make_rot_from_zx(normal, unreal.MathLibrary.get_forward_vector(rot))
            sc = rng.uniform(scale_min, scale_max)
            placed.append(unreal.Transform(location=unreal.Vector(x, y, z), rotation=rot, scale=unreal.Vector(sc, sc, sc)))
            points.append((x, y))
        if not placed:
            raise ToolError(Code.UE_OPERATION_FAILED, 'No ground was hit inside the area',
                            likely_causes=['Check center/radius; the area needs a landscape or colliding floor.'])
        unreal.InstancedFoliageActor.add_instances(world, ft, placed)
        if len(placed) < count:
            ctx().warn(f'Placed {len(placed)} of {count} (spacing/ground misses: {misses}).', 'PARTIAL_SCATTER')
        ctx().set_target(ft.get_path_name())
        ctx().mark_modified(True)
        return {'placed': len(placed), 'requested': count}

    @agent_tool(mutates=True)
    def remove_all_foliage_instances(foliage_type_path: str) -> dict:
        """Removes every instance of a Foliage Type in the current level.

        Args:
            foliage_type_path: Foliage Type asset.
        """
        ft = resolve.load_asset(foliage_type_path, unreal.FoliageType)
        unreal.InstancedFoliageActor.remove_all_instances(editor.editor_world(), ft)
        ctx().set_target(ft.get_path_name())
        ctx().mark_modified(True)
        return {'foliage_type': ft.get_path_name(), 'removed': True}

    @agent_tool()
    def inspect_foliage() -> dict:
        """Counts foliage instances per mesh in the open level."""
        counts: dict[str, int] = {}
        for a in resolve.all_level_actors():
            if isinstance(a, unreal.InstancedFoliageActor):
                for c in a.get_components_by_class(unreal.InstancedStaticMeshComponent) or []:
                    mesh = c.get_editor_property('static_mesh')
                    key = mesh.get_outermost().get_name() if mesh else 'None'
                    counts[key] = counts.get(key, 0) + c.get_instance_count()
        return {'instances_by_mesh': counts, 'total': sum(counts.values())}
