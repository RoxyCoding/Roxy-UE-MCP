"""InspectorTools: structured, read-only views of project/editor state.

Complements Epic's toolsets (ObjectTools.get_properties, BlueprintTools.get_graph,
NiagaraToolset_System.GetSystemSummary, UMGToolSet.GetWidgets, ...) with one-call
structured summaries designed to minimise the number of tool calls an agent needs.
"""

from __future__ import annotations

import json
import os
from collections import Counter

import unreal

from agent_toolkit import VERSION
from agent_toolkit.core import bp as bpu
from agent_toolkit.core import deps, editor, resolve
from agent_toolkit.core.errors import Code, ToolError
from agent_toolkit.core.serialize import split_csv, to_jsonable
from agent_toolkit.core.tooling import agent_tool, ctx


def _safe_prop(obj: unreal.Object, name: str, default=None):
    try:
        return to_jsonable(obj.get_editor_property(name))
    except Exception:  # pylint: disable=broad-exception-caught
        return default


def _component_summary(comp: unreal.ActorComponent) -> dict:
    info = {'name': comp.get_name(), 'class': comp.get_class().get_name()}
    if isinstance(comp, unreal.SceneComponent):
        parent = comp.get_attach_parent()
        info['attach_parent'] = parent.get_name() if parent else None
        socket = comp.get_attach_socket_name()
        if socket and str(socket) != 'None':
            info['socket'] = str(socket)
        info['relative_transform'] = to_jsonable(comp.get_relative_transform())
        info['mobility'] = _safe_prop(comp, 'mobility')
        info['visible'] = _safe_prop(comp, 'visible')
    if isinstance(comp, unreal.PrimitiveComponent):
        info['collision_profile'] = str(comp.get_collision_profile_name())
        info['collision_enabled'] = to_jsonable(comp.get_collision_enabled())
        info['simulate_physics'] = bool(comp.is_simulating_physics())
        try:
            info['materials'] = [to_jsonable(m) for m in comp.get_materials()]
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    if isinstance(comp, unreal.StaticMeshComponent):
        info['static_mesh'] = to_jsonable(comp.get_editor_property('static_mesh'))
    elif isinstance(comp, unreal.SkeletalMeshComponent):
        info['skeletal_mesh'] = _safe_prop(comp, 'skeletal_mesh_asset') or _safe_prop(comp, 'skeletal_mesh')
        info['anim_class'] = _safe_prop(comp, 'anim_class')
    elif isinstance(comp, unreal.LightComponent):
        info['intensity'] = _safe_prop(comp, 'intensity')
        info['light_color'] = _safe_prop(comp, 'light_color')
    elif isinstance(comp, unreal.AudioComponent):
        info['sound'] = _safe_prop(comp, 'sound')
    elif isinstance(comp, unreal.NiagaraComponent):
        info['niagara_system'] = _safe_prop(comp, 'asset')
    if not comp.is_component_tick_enabled() if hasattr(comp, 'is_component_tick_enabled') else False:
        info['tick_enabled'] = False
    return info


def _properties_json(obj: unreal.Object, names: list[str]) -> dict:
    """Uses Epic's ToolsetLibrary property serializer (same format as ObjectTools)."""
    try:
        return json.loads(unreal.ToolsetLibrary.get_object_properties(obj, names))
    except Exception as e:  # pylint: disable=broad-exception-caught
        return {'error': f'property listing failed: {e}'}


@unreal.uclass()
class InspectorTools(unreal.ToolsetDefinition):
    """Read-only inspection of editor, level, actors, assets, Blueprints, materials,
    AI assets and project settings, returned as compact structured JSON."""

    @agent_tool()
    def get_editor_state() -> dict:
        """Returns a one-call overview of the editor: project, engine version, current
        level, PIE state, selection and unsaved packages. Call this first in a session.

        Returns:
            details: project, engine_version, current_level, is_pie_running,
            selected_actors, selected_assets, dirty_packages, toolkit_version.
        """
        world = editor.editor_world()
        selected_actors = [a.get_actor_label() for a in (editor.actor_subsystem().get_selected_level_actors() or [])]
        selected_assets = [str(a.package_name) for a in (unreal.EditorUtilityLibrary.get_selected_asset_data() or [])]
        dirty = sorted(editor.dirty_package_names())
        return {
            'project': unreal.Paths.get_project_file_path(),
            'engine_version': unreal.SystemLibrary.get_engine_version(),
            'current_level': world.get_outermost().get_name() if world else None,
            'is_pie_running': editor.is_pie_running(),
            'selected_actors': selected_actors,
            'selected_assets': selected_assets,
            'dirty_packages': dirty[:100],
            'dirty_package_count': len(dirty),
            'toolkit_version': VERSION,
        }

    @agent_tool()
    def inspect_actor(actor: str, include_properties: bool = False, property_names: str | None = None) -> dict:
        """Returns a structured description of a level actor: class (and Blueprint),
        transform, attachment, folder, tags, replication settings and all components.

        Args:
            actor: Actor label, object name or full object path in the current level.
            include_properties: Also return reflected property values (can be large).
            property_names: Comma-separated property names to restrict include_properties to.

        Returns:
            details: label, name, path, class, blueprint, transform, components[], ...
        """
        a = resolve.find_actor(actor)
        ctx().set_target(a.get_path_name())
        cls = a.get_class()
        gen_bp = unreal.BlueprintEditorLibrary.get_blueprint_for_class(cls)
        if isinstance(gen_bp, tuple):  # (Blueprint, does_not_have_blueprint) out-param form
            gen_bp = gen_bp[0]
        parent = a.get_attach_parent_actor()
        details = {
            'label': a.get_actor_label(),
            'name': a.get_name(),
            'path': a.get_path_name(),
            'class': cls.get_path_name(),
            'blueprint': gen_bp.get_path_name() if gen_bp else None,
            'folder': str(a.get_folder_path()),
            'tags': [str(t) for t in a.tags],
            'transform': to_jsonable(a.get_actor_transform()),
            'hidden_in_game': bool(a.get_editor_property('hidden')),
            'attach_parent': parent.get_actor_label() if parent else None,
            'attached_actors': [c.get_actor_label() for c in a.get_attached_actors()],
            'replicates': _safe_prop(a, 'replicates'),
            'net_dormancy': _safe_prop(a, 'net_dormancy'),
            'tick_enabled': bool(a.is_actor_tick_enabled()),
            'components': [_component_summary(c) for c in a.get_components_by_class(unreal.ActorComponent)],
        }
        if include_properties:
            details['properties'] = _properties_json(a, split_csv(property_names))
        return details

    @agent_tool()
    def inspect_component(actor: str, component: str, include_properties: bool = True,
                          property_names: str | None = None) -> dict:
        """Returns details of one component on a level actor, optionally with reflected properties.

        Args:
            actor: Actor label, object name or path.
            component: Component object name (see inspect_actor components[].name).
            include_properties: Return reflected property values.
            property_names: Comma-separated property names (omit = all user-visible).
        """
        a = resolve.find_actor(actor)
        comp = resolve.find_component(a, component)
        ctx().set_target(comp.get_path_name())
        details = _component_summary(comp)
        details['path'] = comp.get_path_name()
        if include_properties:
            details['properties'] = _properties_json(comp, split_csv(property_names))
        return details

    @agent_tool()
    def inspect_asset(asset_path: str, max_references: int = 25) -> dict:
        """Returns asset metadata: class, package, on-disk file and size, dirty state,
        metadata tags, and the first dependencies / referencers.

        Args:
            asset_path: Asset path, e.g. /Game/Characters/BP_Player.
            max_references: Maximum dependencies/referencers listed (counts are always full).
        """
        data = resolve.asset_data(asset_path)
        package = str(data.package_name)
        ctx().set_target(package)
        filename = editor.package_filename(package)
        dep_list = deps.dependencies(package)
        ref_list = deps.referencers(package)
        metadata = {}
        if data.is_asset_loaded():
            try:
                metadata = {str(k): str(v) for k, v in
                            editor.asset_subsystem().get_metadata_tag_values(data.get_asset()).items()}
            except Exception:  # pylint: disable=broad-exception-caught
                metadata = {}
        return {
            'package': package,
            'object_path': str(data.get_editor_property('package_name')) + '.' + str(data.asset_name),
            'class': deps.asset_class_name(data),
            'is_redirector': bool(data.is_redirector()),
            'is_loaded': bool(data.is_asset_loaded()),
            'is_dirty': package in editor.dirty_package_names(),
            'file': filename,
            'file_size_bytes': os.path.getsize(filename) if filename else None,
            'metadata': metadata,
            'dependency_count': len(dep_list),
            'dependencies': dep_list[:max_references],
            'referencer_count': len(ref_list),
            'referencers': ref_list[:max_references],
        }

    @agent_tool()
    def inspect_blueprint(asset_path: str, include_defaults: bool = True) -> dict:
        """Returns the full structure of a Blueprint without reading graphs node-by-node:
        parent class chain, compile status, variables (type/category/replication/default),
        implemented functions, events, graphs with node counts, components hierarchy,
        interfaces and event dispatchers. Works for Actor, Widget and Animation Blueprints.

        Args:
            asset_path: Blueprint asset path.
            include_defaults: Include CDO default values of variables (requires a compiled BP).
        """
        bp = resolve.load_asset(asset_path, unreal.Blueprint)
        ctx().set_target(bp.get_outermost().get_name())
        parent = unreal.BlueprintEditorLibrary.get_blueprint_parent_class(bp)
        chain = resolve.class_parent_chain(parent) if parent else []
        if parent:
            chain.insert(0, parent.get_name())
        graph_list = []
        for g in bpu.graphs(bp):
            graph_list.append({'name': g.get_name(), 'class': g.get_class().get_name(),
                               'node_count': len(bpu.graph_nodes(g))})
        details = {
            'asset': bp.get_outermost().get_name(),
            'blueprint_class': bp.get_class().get_name(),
            'parent_class': parent.get_path_name() if parent else None,
            'parent_chain': chain,
            'generated_class': bp.generated_class().get_path_name() if bp.generated_class() else None,
            'status': bpu.status_name(bp),
            'variables': bpu.variable_infos(bp, include_defaults),
            'functions': bpu.function_infos(bp, implemented_only=True),
            'events': bpu.event_infos(bp, implemented_only=True),
            'graphs': graph_list,
            'components': bpu.component_tree(bp),
            'interfaces': bpu.implemented_interfaces(bp),
            'event_dispatchers': [str(d) for d in (unreal.BlueprintEditorLibrary.list_event_dispatchers(bp) or [])],
        }
        if isinstance(bp, unreal.AnimBlueprint):
            details['target_skeleton'] = _safe_prop(bp, 'target_skeleton')
        return details

    @agent_tool()
    def inspect_blueprint_graph(asset_path: str, graph_name: str | None = None, include_pins: bool = True,
                                only_nodes_with_messages: bool = False, max_nodes: int = 300) -> dict:
        """Returns a Blueprint graph as structured data: nodes (id, title, class, position,
        compiler message) and pins (name, direction, type, default value, links as 'NodeId.Pin').

        Args:
            asset_path: Blueprint asset path.
            graph_name: Graph name (EventGraph, a function name, ...). Omit for the Event Graph.
            include_pins: Include pin details and connections.
            only_nodes_with_messages: Only return nodes that carry compiler errors/warnings.
            max_nodes: Truncate after this many nodes.
        """
        bp = resolve.load_asset(asset_path, unreal.Blueprint)
        graph = bpu.find_graph(bp, graph_name or '')
        ctx().set_target(graph.get_path_name())
        nodes = bpu.graph_nodes(graph)
        infos = []
        for n in nodes:
            info = bpu.node_info(n, include_pins=include_pins)
            if only_nodes_with_messages and 'message' not in info:
                continue
            infos.append(info)
            if len(infos) >= max_nodes:
                break
        return {'asset': bp.get_outermost().get_name(), 'graph': graph.get_name(),
                'node_count': len(nodes), 'truncated': len(infos) >= max_nodes, 'nodes': infos}

    @agent_tool()
    def inspect_material(asset_path: str, include_expressions: bool = True) -> dict:
        """Returns a Material or Material Instance as structured data: domain, blend mode,
        shading model, parameters with defaults/overrides, expressions with their
        input connections, material output connections, statistics and used textures.

        Args:
            asset_path: Material, MaterialInstance or MaterialFunction asset path.
            include_expressions: Include the expression graph (Materials only).
        """
        mel = unreal.MaterialEditingLibrary
        asset = resolve.load_asset(asset_path, (unreal.MaterialInterface, unreal.MaterialFunction))
        ctx().set_target(asset.get_outermost().get_name())
        details: dict = {'asset': asset.get_outermost().get_name(), 'class': asset.get_class().get_name()}
        if isinstance(asset, unreal.MaterialInstance):
            details['parent'] = to_jsonable(asset.get_editor_property('parent'))
            base = asset.get_base_material()
            details['base_material'] = to_jsonable(base)
            params = {}
            for kind, names_fn, value_fn in (
                    ('scalar', mel.get_scalar_parameter_names, mel.get_material_instance_scalar_parameter_value),
                    ('vector', mel.get_vector_parameter_names, mel.get_material_instance_vector_parameter_value),
                    ('texture', mel.get_texture_parameter_names, mel.get_material_instance_texture_parameter_value),
                    ('static_switch', mel.get_static_switch_parameter_names,
                     mel.get_material_instance_static_switch_parameter_value)):
                params[kind] = {str(n): to_jsonable(value_fn(asset, n)) for n in (names_fn(asset) or [])}
            details['parameters'] = params
            return details
        if isinstance(asset, unreal.MaterialFunction):
            exprs = mel.get_material_function_expressions(asset) or []
            details['expressions'] = [{'name': e.get_name(), 'class': e.get_class().get_name()} for e in exprs]
            return details
        mat = asset
        details.update({
            'domain': _safe_prop(mat, 'material_domain'),
            'blend_mode': _safe_prop(mat, 'blend_mode'),
            'shading_model': _safe_prop(mat, 'shading_model'),
            'two_sided': _safe_prop(mat, 'two_sided'),
            'used_textures': [to_jsonable(t) for t in (mel.get_used_textures(mat) or [])],
        })
        params = {}
        for kind, names_fn, value_fn in (
                ('scalar', mel.get_scalar_parameter_names, mel.get_material_default_scalar_parameter_value),
                ('vector', mel.get_vector_parameter_names, mel.get_material_default_vector_parameter_value),
                ('texture', mel.get_texture_parameter_names, mel.get_material_default_texture_parameter_value),
                ('static_switch', mel.get_static_switch_parameter_names,
                 mel.get_material_default_static_switch_parameter_value)):
            params[kind] = {str(n): to_jsonable(value_fn(mat, n)) for n in (names_fn(mat) or [])}
        details['parameters'] = params
        try:
            details['statistics'] = to_jsonable(mel.get_statistics(mat))
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        if include_expressions:
            exprs = mel.get_material_expressions(mat) or []
            expr_infos = []
            for e in exprs:
                pos = mel.get_material_expression_node_position(e)
                inputs = []
                try:
                    names = mel.get_material_expression_input_names(e) or []
                    sources = mel.get_inputs_for_material_expression(mat, e) or []
                    for i, src in enumerate(sources):
                        if src is None:
                            continue
                        out_name = mel.get_input_node_output_name_for_material_expression(e, src) or ''
                        inputs.append({'input': str(names[i]) if i < len(names) else str(i),
                                       'from': src.get_name(), 'from_output': str(out_name)})
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
                info = {'name': e.get_name(), 'class': e.get_class().get_name(),
                        'pos': to_jsonable(pos), 'inputs': inputs}
                pname = _safe_prop(e, 'parameter_name')
                if pname:
                    info['parameter_name'] = pname
                expr_infos.append(info)
            details['expressions'] = expr_infos
            outputs = {}
            for prop in ('MP_BASE_COLOR', 'MP_METALLIC', 'MP_SPECULAR', 'MP_ROUGHNESS', 'MP_EMISSIVE_COLOR',
                         'MP_OPACITY', 'MP_OPACITY_MASK', 'MP_NORMAL', 'MP_WORLD_POSITION_OFFSET',
                         'MP_AMBIENT_OCCLUSION'):
                try:
                    node = mel.get_material_property_input_node(mat, getattr(unreal.MaterialProperty, prop))
                    if node:
                        outputs[prop[3:].lower()] = node.get_name()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
            details['output_connections'] = outputs
        return details

    @agent_tool()
    def inspect_behavior_tree(asset_path: str) -> dict:
        """Returns a Behavior Tree as a nested structure: composites, tasks, decorators and
        services per node, plus the blackboard asset and its keys.

        Args:
            asset_path: BehaviorTree asset path.
        """
        bt = resolve.load_asset(asset_path, unreal.BehaviorTree)
        ctx().set_target(bt.get_outermost().get_name())

        def node_entry(node) -> dict | None:
            if node is None:
                return None
            entry = {'name': str(_safe_prop(node, 'node_name') or node.get_name()),
                     'class': node.get_class().get_name()}
            services = _safe_prop(node, 'services')
            if services:
                entry['services'] = services
            children = []
            try:
                for child in node.get_editor_property('children') or []:
                    c = child.get_editor_property('child_composite') or child.get_editor_property('child_task')
                    ce = node_entry(c) or {}
                    decorators = child.get_editor_property('decorators') or []
                    if decorators:
                        ce['decorators'] = [d.get_class().get_name() + ':' + str(_safe_prop(d, 'node_name') or '')
                                            for d in decorators]
                    children.append(ce)
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            if children:
                entry['children'] = children
            return entry

        bb = bt.get_editor_property('blackboard_asset')
        return {
            'asset': bt.get_outermost().get_name(),
            'blackboard': bb.get_outermost().get_name() if bb else None,
            'blackboard_keys': _blackboard_keys(bb) if bb else [],
            'root_decorators': [d.get_class().get_name() for d in (bt.get_editor_property('root_decorators') or [])],
            'root': node_entry(bt.get_editor_property('root_node')),
        }

    @agent_tool()
    def inspect_blackboard(asset_path: str) -> dict:
        """Returns Blackboard keys (name, key type, base class/enum, instance synced) and parent.

        Args:
            asset_path: BlackboardData asset path.
        """
        bb = resolve.load_asset(asset_path, unreal.BlackboardData)
        ctx().set_target(bb.get_outermost().get_name())
        return {'asset': bb.get_outermost().get_name(), 'parent': _safe_prop(bb, 'parent'),
                'keys': _blackboard_keys(bb)}

    @agent_tool()
    def inspect_skeleton(asset_path: str, max_bones: int = 500) -> dict:
        """Returns the bone hierarchy (name, parent) and sockets of a Skeleton or Skeletal Mesh.

        Args:
            asset_path: Skeleton or SkeletalMesh asset path.
            max_bones: Truncate the bone list after this many bones.
        """
        asset = resolve.load_asset(asset_path, (unreal.Skeleton, unreal.SkeletalMesh))
        ctx().set_target(asset.get_outermost().get_name())
        mesh = asset if isinstance(asset, unreal.SkeletalMesh) else asset.get_skeleton_preview_mesh()
        skeleton = asset if isinstance(asset, unreal.Skeleton) else asset.get_editor_property('skeleton')
        details: dict = {'asset': asset.get_outermost().get_name(), 'skeleton': to_jsonable(skeleton),
                         'preview_mesh': to_jsonable(mesh)}
        if mesh is None:
            details['warning'] = 'Skeleton has no preview mesh; bone hierarchy unavailable from Python.'
            return details
        smes = unreal.get_editor_subsystem(unreal.SkeletalMeshEditorSubsystem)
        names = [str(n) for n in skeleton.get_reference_pose().get_bone_names()] if skeleton else []
        bones = []
        for name in names[:max_bones]:
            parent = str(smes.get_bone_parent(mesh, name)) if smes else ''
            bones.append({'name': name, 'parent': parent if parent != 'None' else None})
        details['bone_count'] = len(names)
        details['bones'] = bones
        try:
            details['sockets'] = [{'name': str(s.get_editor_property('socket_name')),
                                   'bone': str(s.get_editor_property('bone_name'))}
                                  for s in mesh.get_editor_property('sockets') or []]
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        return details

    @agent_tool()
    def inspect_level(include_actor_list: bool = False, class_filter: str | None = None, max_actors: int = 300) -> dict:
        """Returns a summary of the currently loaded level: actor counts by class, gameplay
        essentials (PlayerStart, lights, cameras, volumes), streaming sublevels, World
        Partition usage, game mode override and folders.

        Args:
            include_actor_list: Also list actors (label, class, location, folder).
            class_filter: Only list actors whose class name contains this text.
            max_actors: Truncate the actor list.
        """
        world = editor.editor_world()
        if world is None:
            raise ToolError(Code.EDITOR_STATE, 'No editor world is loaded.')
        ctx().set_target(world.get_outermost().get_name())
        actors = resolve.all_level_actors()
        counts = Counter(a.get_class().get_name() for a in actors)
        ws = world.get_world_settings()

        def count_of(base: type) -> int:
            return sum(1 for a in actors if isinstance(a, base))

        streaming = []
        try:
            for lvl in unreal.EditorLevelUtils.get_levels(world) or []:
                streaming.append(lvl.get_outermost().get_name())
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        details = {
            'level': world.get_outermost().get_name(),
            'actor_count': len(actors),
            'actor_counts_by_class': dict(counts.most_common(40)),
            'player_starts': count_of(unreal.PlayerStart),
            'lights': count_of(unreal.Light),
            'cameras': count_of(unreal.CameraActor),
            'volumes': count_of(unreal.Volume),
            'has_nav_mesh_bounds': count_of(unreal.NavMeshBoundsVolume) > 0,
            'levels': streaming,
            'world_partition': _safe_prop(ws, 'enable_world_partition', None) if ws else None,
            'game_mode_override': _safe_prop(ws, 'default_game_mode') if ws else None,
            'kill_z': _safe_prop(ws, 'kill_z') if ws else None,
            'folders': sorted({str(a.get_folder_path()) for a in actors if str(a.get_folder_path()) not in ('', 'None')}),
        }
        if include_actor_list:
            listed = []
            for a in actors:
                if class_filter and class_filter.lower() not in a.get_class().get_name().lower():
                    continue
                listed.append({'label': a.get_actor_label(), 'name': a.get_name(),
                               'class': a.get_class().get_name(),
                               'location': to_jsonable(a.get_actor_location()),
                               'folder': str(a.get_folder_path())})
                if len(listed) >= max_actors:
                    break
            details['actors'] = listed
        return details

    @agent_tool()
    def inspect_world_settings() -> dict:
        """Returns the current level's World Settings: game mode override, kill Z, gravity,
        navigation/AI system, world partition, time dilation and lightmass basics."""
        world = editor.editor_world()
        if world is None:
            raise ToolError(Code.EDITOR_STATE, 'No editor world is loaded.')
        ws = world.get_world_settings()
        ctx().set_target(ws.get_path_name())
        keys = ['default_game_mode', 'kill_z', 'global_gravity_set', 'global_gravity_z', 'enable_world_bounds_checks',
                'enable_navigation_system', 'enable_ai_system', 'enable_world_composition',
                'enable_world_origin_rebasing', 'enable_large_worlds', 'default_physics_volume_class',
                'time_dilation', 'min_global_time_dilation', 'max_global_time_dilation', 'force_no_precomputed_lighting']
        return {k: _safe_prop(ws, k) for k in keys}

    @agent_tool()
    def inspect_project_settings() -> dict:
        """Returns key project settings: project file/name, engine version, Maps & Modes
        (default maps, global default GameMode, game instance), enabled plugins from the
        .uproject, and the Python/MCP toolset environment.

        For arbitrary config sections use Epic's ConfigSettingsToolset."""
        gms = unreal.GameMapsSettings.get_game_maps_settings()
        maps = {k: _safe_prop(gms, k) for k in ('editor_startup_map', 'game_default_map', 'server_default_map',
                                                 'transition_map', 'global_default_game_mode',
                                                 'global_default_server_game_mode', 'game_instance_class')}
        uproject_path = unreal.Paths.get_project_file_path()
        plugins = []
        try:
            with open(uproject_path, encoding='utf-8') as f:
                uproject = json.load(f)
            plugins = [{'name': p.get('Name'), 'enabled': p.get('Enabled', False)} for p in uproject.get('Plugins', [])]
            modules = [m.get('Name') for m in uproject.get('Modules', [])]
        except Exception:  # pylint: disable=broad-exception-caught
            uproject, modules = {}, []
        ctx().set_target(uproject_path)
        return {
            'project_file': uproject_path,
            'project_name': os.path.splitext(os.path.basename(uproject_path))[0],
            'engine_version': unreal.SystemLibrary.get_engine_version(),
            'has_cpp_modules': bool(modules),
            'modules': modules,
            'maps_and_modes': maps,
            'uproject_plugins': plugins,
        }

    @agent_tool()
    def get_asset_dependencies(asset_path: str, depth: int = 1, max_nodes: int = 200) -> dict:
        """Returns what an asset depends on (hard + soft), as a tree up to `depth` levels,
        with each node's class and whether it exists (missing = broken reference).

        Args:
            asset_path: Asset path.
            depth: Levels to traverse (1 = direct dependencies).
            max_nodes: Stop after this many unique nodes.
        """
        package = resolve.package_of(asset_path)
        ctx().set_target(package)
        return deps.tree(package, 'dependencies', depth, max_nodes)

    @agent_tool()
    def get_asset_referencers(asset_path: str, depth: int = 1, max_nodes: int = 200) -> dict:
        """Returns which assets reference an asset (hard + soft), as a tree up to `depth` levels.

        Args:
            asset_path: Asset path.
            depth: Levels to traverse (1 = direct referencers).
            max_nodes: Stop after this many unique nodes.
        """
        package = resolve.package_of(asset_path)
        ctx().set_target(package)
        return deps.tree(package, 'referencers', depth, max_nodes)

    @agent_tool()
    def get_class_hierarchy(class_name: str, include_children: bool = True, include_blueprint_children: bool = True,
                            max_children: int = 200) -> dict:
        """Returns the parent chain of a class and (optionally) its native and Blueprint subclasses.

        Args:
            class_name: Native class name (Character), script path or Blueprint asset path.
            include_children: Include native derived classes.
            include_blueprint_children: Include Blueprint assets deriving from the class.
            max_children: Truncate child lists.
        """
        cls = resolve.resolve_class(class_name)
        ctx().set_target(cls.get_path_name())
        parents = resolve.class_parent_chain(cls)
        details: dict = {'class': cls.get_path_name(), 'parents': parents}
        if include_children:
            derived = unreal.ToolsetLibrary.get_derived_classes(cls) or []
            details['native_children'] = [str(c.export_text()) if hasattr(c, 'export_text') else str(c)
                                          for c in list(derived)[:max_children]]
        if include_blueprint_children:
            path = unreal.TopLevelAssetPath(cls.get_outermost().get_name(), cls.get_name())
            names = editor.asset_registry().get_derived_class_names([path], []) or []
            details['derived_class_paths'] = sorted(str(n) for n in names)[:max_children]
        return details


def _blackboard_keys(bb: unreal.BlackboardData) -> list[dict]:
    lib = getattr(unreal, 'AgentToolkitGraphLibrary', None)
    if lib is not None:
        return json.loads(lib.bb_describe_keys(bb)).get('keys', [])
    keys = []
    try:
        for entry in bb.get_editor_property('keys') or []:
            key_type = entry.get_editor_property('key_type')
            info = {'name': str(entry.get_editor_property('entry_name')),
                    'type': key_type.get_class().get_name().replace('BlackboardKeyType_', '') if key_type else None,
                    'instance_synced': bool(entry.get_editor_property('instance_synced'))}
            if key_type is not None:
                for attr in ('base_class', 'enum_type'):
                    try:
                        v = key_type.get_editor_property(attr)
                        if v:
                            info[attr] = to_jsonable(v)
                    except Exception:  # pylint: disable=broad-exception-caught
                        pass
            keys.append(info)
    except Exception as e:  # pylint: disable=broad-exception-caught
        keys.append({'error': f'could not read keys: {e}'})
    return keys
