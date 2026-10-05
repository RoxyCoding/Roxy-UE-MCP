"""AITools: Blackboard keys, Behavior Tree graph authoring, AI Perception, pawn/controller
setup and navigation checks.

Behavior Tree and Blackboard editing use the UEAgentToolkitNative plugin. Complements Epic's
BehaviorTreeTools (runtime tree reading) and StateTreeTools.
"""

from __future__ import annotations

import json

import unreal

from agent_toolkit.core import bp as bpu
from agent_toolkit.core import editor, native, resolve
from agent_toolkit.core.errors import Code, ToolError
from agent_toolkit.core.serialize import from_jsonable, parse_json_arg, to_jsonable
from agent_toolkit.core.tooling import agent_tool, ctx

_POSSESS = {'disabled': 'DISABLED', 'placed_in_world': 'PLACED_IN_WORLD', 'spawned': 'SPAWNED',
            'placed_or_spawned': 'PLACED_IN_WORLD_OR_SPAWNED'}


def _bt(path: str) -> unreal.BehaviorTree:
    bt = resolve.load_asset(path, unreal.BehaviorTree)
    ctx().set_target(bt.get_outermost().get_name())
    return bt


def _node_class(name: str, prefix_hint: str) -> unreal.Class:
    """Resolves 'MoveTo', 'BTTask_MoveTo', 'Selector', a script path or a Blueprint task path."""
    if name.startswith('/'):
        return resolve.resolve_class(name)
    for candidate in (name, *(f'{p}{name}' for p in prefix_hint.split(','))):
        try:
            return resolve.resolve_class(candidate)
        except ToolError:
            continue
    raise ToolError(Code.CLASS_NOT_FOUND, f'Behavior Tree node class {name!r} not found',
                    likely_causes=['Examples: Selector, Sequence, MoveTo, Wait, RotateToFaceBBEntry, Blackboard '
                                   '(decorator), Cooldown, DefaultFocus (service), or a Blueprint task path.'])


def _describe_bt(bt: unreal.BehaviorTree) -> dict:
    lib = native.require_graph('Behavior Tree graph access')
    return json.loads(native.check(lib.bt_describe_graph(bt), bt.get_path_name()))


def _blackboard_compatible(main_bb, sub_bb) -> bool:
    """Run Behavior needs the subtree's blackboard to be the main tree's blackboard or one of its parents."""
    current, seen = main_bb, 0
    while current is not None and seen < 32:
        if current == sub_bb:
            return True
        current = current.get_editor_property('parent')
        seen += 1
    return False


@unreal.uclass()
class AITools(unreal.ToolsetDefinition):
    """AI authoring: Blackboard keys, Behavior Tree nodes/decorators/services with properties and
    blackboard key bindings, AI Perception (sight/hearing) on AI Controllers, pawn AI controller
    setup, navigation status, path tests and NavMesh rebuild."""

    # ------------------------------------------------------------------ blackboard
    @agent_tool(mutates=True)
    def add_blackboard_key(blackboard_path: str, key_name: str, key_type: str, base_class: str | None = None,
                           instance_synced: bool = False) -> dict:
        """Adds a key to a Blackboard asset.

        Args:
            blackboard_path: BlackboardData asset path.
            key_name: Key name, e.g. "TargetActor".
            key_type: bool, int, float, string, name, vector, rotator, object, class or enum.
            base_class: For object/class keys: base class (e.g. "Actor", "Pawn"); for enum keys: enum path.
            instance_synced: Share the value across all instances of this blackboard.
        """
        bb = resolve.load_asset(blackboard_path, unreal.BlackboardData)
        ctx().set_target(bb.get_outermost().get_name())
        lib = native.require_graph('Blackboard editing')
        extra = None
        if base_class:
            extra = unreal.load_object(None, base_class) if key_type.lower() == 'enum' else resolve.resolve_class(base_class)
            if extra is None:
                raise ToolError(Code.OBJECT_NOT_FOUND, f'{base_class} not found')
        native.check(lib.bb_add_key(bb, key_name, key_type, extra, instance_synced), bb.get_path_name())
        return json.loads(lib.bb_describe_keys(bb))

    @agent_tool(mutates=True)
    def remove_blackboard_key(blackboard_path: str, key_name: str) -> dict:
        """Removes a key declared in this Blackboard (Behavior Trees using it will report errors).

        Args:
            blackboard_path: BlackboardData asset path.
            key_name: Key to remove.
        """
        bb = resolve.load_asset(blackboard_path, unreal.BlackboardData)
        ctx().set_target(bb.get_outermost().get_name())
        lib = native.require_graph('Blackboard editing')
        native.check(lib.bb_remove_key(bb, key_name), bb.get_path_name())
        return json.loads(lib.bb_describe_keys(bb))

    # --------------------------------------------------------------- behavior tree
    @agent_tool(mutates=True)
    def set_behavior_tree_blackboard(behavior_tree_path: str, blackboard_path: str) -> dict:
        """Assigns the Blackboard asset used by a Behavior Tree.

        Args:
            behavior_tree_path: BehaviorTree asset path.
            blackboard_path: BlackboardData asset path.
        """
        bt = _bt(behavior_tree_path)
        bb = resolve.load_asset(blackboard_path, unreal.BlackboardData)
        bt.set_editor_property('blackboard_asset', bb)
        lib = native.graph_library()
        if lib is not None:
            native.check(lib.bt_update_asset(bt), bt.get_path_name())
        return {'blackboard': bb.get_outermost().get_name()}

    @agent_tool(mutates=True)
    def add_bt_node(behavior_tree_path: str, node_class: str, parent_node: str = 'Root',
                    properties_json: str | None = None, parent_output: int = 0) -> dict:
        """Adds a composite (Selector/Sequence) or task (MoveTo, Wait, ...) under a parent. Children
        execute left-to-right in insertion order. Returns the node id and the updated tree.

        Args:
            behavior_tree_path: BehaviorTree asset path.
            node_class: e.g. Selector, Sequence, SimpleParallel, MoveTo, Wait, RotateToFaceBBEntry, PlaySound, or a
                Blueprint task asset path.
            parent_node: "Root" or a composite node id from inspect_bt_graph.
            properties_json: Node properties; blackboard key selectors accept key names,
                e.g. {"blackboard_key": "TargetActor", "acceptable_radius": 80}.
            parent_output: Parent output pin. For a SimpleParallel parent: 0 = main task (exactly one
                task), 1 = background branch.
        """
        bt = _bt(behavior_tree_path)
        lib = native.require_graph('Behavior Tree editing')
        cls = _node_class(node_class, 'BTComposite_,BTTask_')
        node = native.check(lib.bt_add_node(bt, parent_node, cls, parent_output), bt.get_path_name())
        applied = _apply_bt_properties(bt, node, properties_json) if properties_json else []
        return {'node': node, 'class': cls.get_name(), 'properties_set': applied, 'tree': _describe_bt(bt)}

    @agent_tool(mutates=True)
    def add_bt_subtree(behavior_tree_path: str, subtree_path: str, parent_node: str = 'Root',
                       parent_output: int = 0) -> dict:
        """Adds a Run Behavior task that runs another Behavior Tree as a subtree (reusable AI logic,
        e.g. a shared "Patrol" tree).

        Args:
            behavior_tree_path: BehaviorTree asset to edit.
            subtree_path: BehaviorTree asset to run as the subtree (must not be the same tree).
            parent_node: Composite node id (or "Root" is not allowed: the root needs a composite first).
            parent_output: Parent output pin (SimpleParallel: 0 = main task, 1 = background).
        """
        bt = _bt(behavior_tree_path)
        sub = resolve.load_asset(subtree_path, unreal.BehaviorTree)
        if sub == bt:
            raise ToolError(Code.INVALID_ARGUMENT, 'A Behavior Tree cannot run itself as a subtree')
        lib = native.require_graph('Behavior Tree editing')
        main_bb = bt.get_editor_property('blackboard_asset')
        sub_bb = sub.get_editor_property('blackboard_asset')
        if sub_bb is not None and main_bb is not None and not _blackboard_compatible(main_bb, sub_bb):
            ctx().warn(f'Subtree blackboard {sub_bb.get_name()} is not {main_bb.get_name()} or one of its parents; '
                       'the subtree will fail to run', 'BLACKBOARD_MISMATCH')
        node = native.check(lib.bt_add_node(bt, parent_node, unreal.BTTask_RunBehavior.static_class(), parent_output),
                            bt.get_path_name())
        instance = lib.bt_get_node_instance(bt, node)
        instance.set_editor_property('behavior_asset', sub)
        native.check(lib.bt_update_asset(bt), bt.get_path_name())
        return {'node': node, 'subtree': sub.get_outermost().get_name(), 'tree': _describe_bt(bt)}

    @agent_tool(mutates=True)
    def add_bt_subnode(behavior_tree_path: str, owner_node: str, node_class: str,
                       properties_json: str | None = None) -> dict:
        """Adds a decorator (condition) or service (periodic update) to a composite/task node.

        Args:
            behavior_tree_path: BehaviorTree asset path.
            owner_node: Node id of the composite/task.
            node_class: e.g. Blackboard (decorator), Cooldown, Loop, TimeLimit, IsAtLocation,
                DefaultFocus (service), RunEQS, or a Blueprint decorator/service path.
            properties_json: e.g. {"blackboard_key": "TargetActor", "notify_observer": "ON_VALUE_CHANGE"}.
        """
        bt = _bt(behavior_tree_path)
        lib = native.require_graph('Behavior Tree editing')
        cls = _node_class(node_class, 'BTDecorator_,BTService_')
        node = native.check(lib.bt_add_sub_node(bt, owner_node, cls), bt.get_path_name())
        applied = _apply_bt_properties(bt, node, properties_json) if properties_json else []
        return {'node': node, 'class': cls.get_name(), 'properties_set': applied, 'tree': _describe_bt(bt)}

    @agent_tool(mutates=True)
    def set_bt_node_properties(behavior_tree_path: str, node: str, properties_json: str) -> dict:
        """Sets properties of a Behavior Tree node instance. FBlackboardKeySelector properties take
        a key name string.

        Args:
            behavior_tree_path: BehaviorTree asset path.
            node: Node id (composite, task, decorator or service).
            properties_json: e.g. {"wait_time": 2.0} or {"blackboard_key": "PatrolLocation"}.
        """
        bt = _bt(behavior_tree_path)
        native.require_graph('Behavior Tree editing')
        return {'node': node, 'properties_set': _apply_bt_properties(bt, node, properties_json)}

    @agent_tool(mutates=True)
    def remove_bt_node(behavior_tree_path: str, node: str) -> dict:
        """Removes a node (and its decorators/services; children become unconnected).

        Args:
            behavior_tree_path: BehaviorTree asset path.
            node: Node id.
        """
        bt = _bt(behavior_tree_path)
        lib = native.require_graph('Behavior Tree editing')
        native.check(lib.bt_remove_node(bt, node), bt.get_path_name())
        return {'removed': node, 'tree': _describe_bt(bt)}

    @agent_tool()
    def inspect_bt_graph(behavior_tree_path: str) -> dict:
        """Returns the editor graph of a Behavior Tree with node ids (needed by the editing tools),
        classes, children in execution order, decorators, services and unconnected nodes.

        Args:
            behavior_tree_path: BehaviorTree asset path.
        """
        bt = _bt(behavior_tree_path)
        tree = _describe_bt(bt)
        tree['blackboard'] = to_jsonable(bt.get_editor_property('blackboard_asset'))
        return tree

    # -------------------------------------------------------------------------- EQS
    @agent_tool(mutates=True)
    def add_eqs_generator(query_path: str, generator_class: str, properties_json: str | None = None) -> dict:
        """Adds an option (generator) to an Environment Query (create the query asset with
        AssetManagementTools.create_asset(asset_class="EnvQuery")). Returns its option index.

        Args:
            query_path: EnvQuery asset path.
            generator_class: e.g. ActorsOfClass, SimpleGrid, OnCircle, Donut, PathingGrid, CurrentLocation
                (EnvQueryGenerator_ prefix optional) or a Blueprint generator path.
            properties_json: Generator properties by C++ name (Epic JSON format), e.g.
                {"SearchedActorClass": "/Script/Engine.Pawn", "SearchRadius": {"DefaultValue": 3000}}.
        """
        query = _eqs(query_path)
        lib = _eqs_lib()
        cls = _node_class(generator_class, 'EnvQueryGenerator_')
        index = int(native.check(lib.eqs_add_option(query, cls), query.get_path_name()))
        applied = _set_eqs_props(query, index, -1, properties_json)
        return {'option': index, 'class': cls.get_name(), 'properties_set': applied,
                'query': json.loads(lib.eqs_describe(query))}

    @agent_tool(mutates=True)
    def add_eqs_test(query_path: str, option_index: int, test_class: str, purpose: str = 'filter_and_score',
                     properties_json: str | None = None) -> dict:
        """Adds a test to an EQS option.

        Args:
            query_path: EnvQuery asset path.
            option_index: Option index from add_eqs_generator / inspect_eqs_query.
            test_class: e.g. Distance, Trace, Pathfinding, Dot, GameplayTags, Overlap
                (EnvQueryTest_ prefix optional) or a Blueprint test path.
            purpose: filter, score or filter_and_score.
            properties_json: Test properties by C++ name, e.g. {"FilterType": "Maximum",
                "FloatValueMax": {"DefaultValue": 1500}, "ScoringEquation": "InverseLinear"}.
        """
        query = _eqs(query_path)
        lib = _eqs_lib()
        purposes = {'filter': 'Filter', 'score': 'Score', 'filter_and_score': 'FilterAndScore'}
        if purpose.lower() not in purposes:
            raise ToolError(Code.INVALID_ARGUMENT, f'purpose must be one of {sorted(purposes)}')
        cls = _node_class(test_class, 'EnvQueryTest_')
        index = int(native.check(lib.eqs_add_test(query, option_index, cls), query.get_path_name()))
        props = parse_json_arg(properties_json or '', 'properties_json', dict)
        props.setdefault('TestPurpose', purposes[purpose.lower()])
        applied = _set_eqs_props(query, option_index, index, json.dumps(props))
        return {'option': option_index, 'test': index, 'class': cls.get_name(), 'properties_set': applied,
                'query': json.loads(lib.eqs_describe(query))}

    @agent_tool(mutates=True)
    def set_eqs_properties(query_path: str, option_index: int, properties_json: str, test_index: int = -1) -> dict:
        """Sets properties of an EQS generator (test_index -1) or test, by C++ property name.

        Args:
            query_path: EnvQuery asset path.
            option_index: Option index.
            properties_json: JSON object, e.g. {"GridSize": {"DefaultValue": 1000}}.
            test_index: Test index, or -1 for the option's generator.
        """
        query = _eqs(query_path)
        return {'properties_set': _set_eqs_props(query, option_index, test_index, properties_json)}

    @agent_tool(mutates=True)
    def remove_eqs_item(query_path: str, option_index: int, test_index: int = -1) -> dict:
        """Removes an EQS option (test_index -1) or a single test.

        Args:
            query_path: EnvQuery asset path.
            option_index: Option index.
            test_index: Test index, or -1 to remove the whole option.
        """
        query = _eqs(query_path)
        lib = _eqs_lib()
        native.check(lib.eqs_remove(query, option_index, test_index), query.get_path_name())
        return {'query': json.loads(lib.eqs_describe(query))}

    @agent_tool()
    def inspect_eqs_query(query_path: str) -> dict:
        """Returns an Environment Query's options: generator class/description and tests with purpose.

        Args:
            query_path: EnvQuery asset path.
        """
        query = _eqs(query_path)
        return json.loads(_eqs_lib().eqs_describe(query))

    # ------------------------------------------------------------ perception/setup
    @agent_tool(mutates=True)
    def add_ai_perception(controller_blueprint: str, sight_radius: float = 1500.0, lose_sight_radius: float = 2000.0,
                          peripheral_vision_angle: float = 70.0, hearing_range: float = 0.0,
                          detect_enemies: bool = True, detect_neutrals: bool = True,
                          detect_friendlies: bool = True) -> dict:
        """Adds an AIPerceptionComponent with Sight (and optionally Hearing) to an AI Controller
        Blueprint. Bind OnTargetPerceptionUpdated with BlueprintAuthoringTools.add_blueprint_node
        (node_kind=component_event, identifier="AIPerception:OnTargetPerceptionUpdated").

        Args:
            controller_blueprint: AIController Blueprint path.
            sight_radius: Sight radius (cm).
            lose_sight_radius: Radius at which a seen target is lost (>= sight_radius).
            peripheral_vision_angle: Half-angle of the vision cone in degrees.
            hearing_range: Hearing range (cm); 0 disables hearing.
            detect_enemies: Detect hostile actors.
            detect_neutrals: Detect neutral actors (most actors without a team are neutral).
            detect_friendlies: Detect friendly actors.
        """
        bp = resolve.load_asset(controller_blueprint, unreal.Blueprint)
        ctx().set_target(bp.get_outermost().get_name())
        parent = unreal.BlueprintEditorLibrary.get_blueprint_parent_class(bp)
        if parent is None or not resolve.is_child_of(parent, unreal.AIController):
            raise ToolError(Code.WRONG_TYPE, 'Perception belongs on an AIController Blueprint', target=bp.get_path_name())
        if lose_sight_radius < sight_radius:
            raise ToolError(Code.INVALID_ARGUMENT, 'lose_sight_radius must be >= sight_radius')
        bpu.add_component(bp, unreal.AIPerceptionComponent.static_class(), 'AIPerception')
        template = bpu.component_template(bp, 'AIPerception')

        def affiliation():
            aff = unreal.AISenseAffiliationFilter()
            aff.set_editor_property('detect_enemies', detect_enemies)
            aff.set_editor_property('detect_neutrals', detect_neutrals)
            aff.set_editor_property('detect_friendlies', detect_friendlies)
            return aff

        sight = unreal.new_object(unreal.AISenseConfig_Sight, template)
        sight.set_editor_property('sight_radius', sight_radius)
        sight.set_editor_property('lose_sight_radius', lose_sight_radius)
        sight.set_editor_property('peripheral_vision_angle_degrees', peripheral_vision_angle)
        sight.set_editor_property('detection_by_affiliation', affiliation())
        configs = [sight]
        if hearing_range > 0:
            hearing = unreal.new_object(unreal.AISenseConfig_Hearing, template)
            hearing.set_editor_property('hearing_range', hearing_range)
            hearing.set_editor_property('detection_by_affiliation', affiliation())
            configs.append(hearing)
        template.modify()
        template.set_editor_property('senses_config', configs)
        try:
            template.set_editor_property('dominant_sense', unreal.AISense_Sight.static_class())
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        unreal.BlueprintEditorLibrary.compile_blueprint(bp)
        return {'component': 'AIPerception', 'senses': [c.get_class().get_name() for c in configs]}

    @agent_tool(mutates=True)
    def set_pawn_ai_controller(pawn_blueprint: str, controller_class: str, auto_possess: str = 'placed_or_spawned') -> dict:
        """Sets the AI Controller class and auto-possess mode of a Pawn/Character Blueprint.

        Args:
            pawn_blueprint: Pawn or Character Blueprint path.
            controller_class: AIController class or Blueprint path.
            auto_possess: disabled, placed_in_world, spawned or placed_or_spawned.
        """
        bp = resolve.load_asset(pawn_blueprint, unreal.Blueprint)
        ctx().set_target(bp.get_outermost().get_name())
        mode = _POSSESS.get(auto_possess.lower())
        if mode is None:
            raise ToolError(Code.INVALID_ARGUMENT, f'auto_possess must be one of {sorted(_POSSESS)}')
        cls = resolve.resolve_class(controller_class, unreal.AIController)
        unreal.BlueprintEditorLibrary.compile_blueprint(bp)
        cdo = unreal.get_default_object(bp.generated_class())
        if not isinstance(cdo, unreal.Pawn):
            raise ToolError(Code.WRONG_TYPE, 'Blueprint is not a Pawn/Character', target=bp.get_path_name())
        cdo.modify()
        cdo.set_editor_property('ai_controller_class', cls)
        cdo.set_editor_property('auto_possess_ai', getattr(unreal.AutoPossessAI, mode))
        bp.modify()
        return {'ai_controller_class': cls.get_path_name(), 'auto_possess_ai': mode}

    # ------------------------------------------------------------------ navigation
    @agent_tool()
    def get_navigation_status() -> dict:
        """Reports navigation setup of the open level: NavMeshBoundsVolumes (with extents),
        navigation data actors (RecastNavMesh) and whether a nav system exists."""
        world = editor.editor_world()
        actors = resolve.all_level_actors()
        bounds = [{'label': a.get_actor_label(), 'extent': to_jsonable(a.get_actor_bounds(False)[1])}
                  for a in actors if isinstance(a, unreal.NavMeshBoundsVolume)]
        data = [a.get_actor_label() for a in actors if isinstance(a, unreal.NavigationData)]
        nav = unreal.NavigationSystemV1.get_navigation_system(world) if world else None
        ctx().set_target(world.get_outermost().get_name() if world else '')
        if not bounds:
            ctx().warn('No NavMeshBoundsVolume: AI movement will fail. Add one with LevelTools.add_volume.', 'NO_NAV_BOUNDS')
        return {'nav_bounds_volumes': bounds, 'navigation_data': data, 'has_navigation_system': nav is not None}

    @agent_tool()
    def test_navigation_path(start: list[float], end: list[float]) -> dict:
        """Tests whether a navigation path exists between two points in the open level.

        Args:
            start: [x, y, z].
            end: [x, y, z].
        """
        world = editor.editor_world()
        path = unreal.NavigationSystemV1.find_path_to_location_synchronously(
            world, unreal.Vector(*start), unreal.Vector(*end))
        points = [to_jsonable(p) for p in (path.get_editor_property('path_points') if path else [])]
        valid = bool(path) and len(points) > 1 and not path.is_partial()
        if not valid:
            ctx().warn('No complete path. Check NavMeshBoundsVolume coverage and rebuild navigation.', 'NO_PATH')
        return {'valid': valid, 'partial': bool(path and path.is_partial()), 'points': points,
                'length': round(path.get_path_length(), 2) if path else None}

    @agent_tool()
    def rebuild_navigation() -> dict:
        """Rebuilds navigation data for the open level (editor command RebuildNavigation)."""
        world = editor.editor_world()
        unreal.SystemLibrary.execute_console_command(world, 'RebuildNavigation')
        return {'requested': True}


def _eqs(path: str) -> unreal.Object:
    query = resolve.load_asset(path, unreal.EnvQuery)
    ctx().set_target(query.get_outermost().get_name())
    return query


def _eqs_lib():
    native.require_graph('EQS editing')
    return unreal.AgentToolkitWorldLibrary


def _set_eqs_props(query, option_index: int, test_index: int, properties_json: str | None) -> list[str]:
    if not properties_json:
        return []
    props = parse_json_arg(properties_json, 'properties_json', dict)
    node = _eqs_lib().eqs_get_node(query, option_index, test_index)
    if node is None:
        raise ToolError(Code.OBJECT_NOT_FOUND, f'EQS option {option_index} / test {test_index} not found',
                        target=query.get_path_name())
    applied = native.set_properties_from_json(node, props, f'{query.get_path_name()}:{node.get_class().get_name()}')
    query.modify()
    return applied


def _apply_bt_properties(bt: unreal.BehaviorTree, node: str, properties_json: str) -> list[str]:
    lib = native.graph_library()
    instance = lib.bt_get_node_instance(bt, node)
    if instance is None:
        raise ToolError(Code.OBJECT_NOT_FOUND, f'Node {node!r} not found', target=bt.get_path_name(),
                        likely_causes=['Use inspect_bt_graph to get node ids.'])
    props = parse_json_arg(properties_json, 'properties_json', dict)
    applied = []
    instance.modify()
    for key, value in props.items():
        try:
            current = instance.get_editor_property(key)
        except Exception as e:  # pylint: disable=broad-exception-caught
            raise ToolError(Code.INVALID_ARGUMENT, f'{instance.get_class().get_name()} has no property {key!r}',
                            target=f'{bt.get_path_name()}:{node}') from e
        if isinstance(current, unreal.BlackboardKeySelector) and isinstance(value, str):
            cpp_name = ''.join(part.capitalize() for part in key.split('_'))
            native.check(lib.bt_set_blackboard_key(bt, node, cpp_name, value), bt.get_path_name())
        else:
            instance.set_editor_property(key, from_jsonable(value, current, key))
        applied.append(key)
    native.check(lib.bt_update_asset(bt), bt.get_path_name())
    return applied
