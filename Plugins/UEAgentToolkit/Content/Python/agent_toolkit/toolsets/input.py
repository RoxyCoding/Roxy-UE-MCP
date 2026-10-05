"""InputTools: Enhanced Input actions, mapping contexts, key mappings (with triggers and
modifiers), project default contexts and Blueprint wiring (mapping context registration,
input action events).

Trigger specs: pressed, released, down, hold[:seconds], hold_and_release[:seconds],
tap[:seconds], pulse[:interval], chord:<InputActionPath>.
Modifier specs: negate[:xyz axes, e.g. negate:x], swizzle[:YXZ|ZYX|XZY|YZX|ZXY], deadzone[:lower],
scalar:<x>[,y,z], smooth, fov_scaling.
"""

from __future__ import annotations

import json

import unreal

from agent_toolkit.core import bp as bpu
from agent_toolkit.core import config, editor, resolve
from agent_toolkit.core.errors import Code, ToolError
from agent_toolkit.core.serialize import split_csv, to_jsonable
from agent_toolkit.core.tooling import agent_tool, ctx

_VALUE_TYPES = {'bool': 'BOOLEAN', 'boolean': 'BOOLEAN', 'axis1d': 'AXIS1D', 'float': 'AXIS1D',
                'axis2d': 'AXIS2D', 'vector2d': 'AXIS2D', 'axis3d': 'AXIS3D', 'vector': 'AXIS3D'}
_EI_SETTINGS_SECTION = '/Script/EnhancedInput.EnhancedInputDeveloperSettings'


def _make_trigger(spec: str, outer: unreal.Object) -> unreal.Object:
    name, _, arg = spec.strip().partition(':')
    name = name.lower()
    table = {'pressed': unreal.InputTriggerPressed, 'released': unreal.InputTriggerReleased,
             'down': unreal.InputTriggerDown, 'hold': unreal.InputTriggerHold,
             'hold_and_release': unreal.InputTriggerHoldAndRelease, 'tap': unreal.InputTriggerTap,
             'pulse': unreal.InputTriggerPulse, 'chord': unreal.InputTriggerChordAction}
    cls = table.get(name)
    if cls is None:
        raise ToolError(Code.INVALID_ARGUMENT, f'Unknown trigger {spec!r}', likely_causes=[f'Valid: {sorted(table)}'])
    trig = unreal.new_object(cls, outer) if outer is not None else unreal.new_object(cls)
    if arg:
        if name in ('hold', 'hold_and_release'):
            trig.set_editor_property('hold_time_threshold', float(arg))
        elif name == 'tap':
            trig.set_editor_property('tap_release_time_threshold', float(arg))
        elif name == 'pulse':
            trig.set_editor_property('interval', float(arg))
        elif name == 'chord':
            trig.set_editor_property('chord_action', resolve.load_asset(arg, unreal.InputAction))
    elif name == 'chord':
        raise ToolError(Code.INVALID_ARGUMENT, 'chord trigger needs an action: chord:/Game/Input/IA_Modifier')
    return trig


def _make_modifier(spec: str, outer: unreal.Object) -> unreal.Object:
    name, _, arg = spec.strip().partition(':')
    name = name.lower()
    table = {'negate': unreal.InputModifierNegate, 'swizzle': unreal.InputModifierSwizzleAxis,
             'deadzone': unreal.InputModifierDeadZone, 'scalar': unreal.InputModifierScalar,
             'smooth': unreal.InputModifierSmooth, 'fov_scaling': unreal.InputModifierFOVScaling}
    cls = table.get(name)
    if cls is None:
        raise ToolError(Code.INVALID_ARGUMENT, f'Unknown modifier {spec!r}', likely_causes=[f'Valid: {sorted(table)}'])
    mod = unreal.new_object(cls, outer) if outer is not None else unreal.new_object(cls)
    if name == 'negate' and arg:
        for axis in 'xyz':
            mod.set_editor_property(axis, axis in arg.lower())
    elif name == 'swizzle' and arg:
        mod.set_editor_property('order', getattr(unreal.InputAxisSwizzle, arg.upper()))
    elif name == 'deadzone' and arg:
        mod.set_editor_property('lower_threshold', float(arg))
    elif name == 'scalar':
        vals = [float(v) for v in (arg or '1').split(',')]
        vals += [vals[-1]] * (3 - len(vals))
        mod.set_editor_property('scalar', unreal.Vector(*vals[:3]))
    return mod


def _key(name: str) -> unreal.Key:
    key = unreal.Key()
    key.set_editor_property('key_name', unreal.Name(name))
    valid = True
    try:
        valid = bool(unreal.InputLibrary.key_is_valid(key))
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    if not valid:
        raise ToolError(Code.INVALID_ARGUMENT, f'Unknown key {name!r}',
                        likely_causes=['Use Unreal key names: W, A, S, D, SpaceBar, LeftShift, LeftMouseButton, '
                                       'RightMouseButton, Mouse2D, MouseX, MouseY, Gamepad_LeftX, Gamepad_Left2D, '
                                       'Gamepad_Right2D, Gamepad_FaceButton_Bottom, E, Escape, Tab'])
    return key


def _mappings(imc: unreal.InputMappingContext) -> tuple[list, object]:
    """Returns (mappings list, container) supporting the 5.7+ DefaultKeyMappings struct."""
    try:
        data = imc.get_editor_property('default_key_mappings')
        return list(data.get_editor_property('mappings')), data
    except Exception:  # pylint: disable=broad-exception-caught
        return list(imc.get_editor_property('mappings')), None


def _store_mappings(imc: unreal.InputMappingContext, mappings: list, container) -> None:
    imc.modify()
    if container is not None:
        container.set_editor_property('mappings', mappings)
        imc.set_editor_property('default_key_mappings', container)
    else:
        imc.set_editor_property('mappings', mappings)


def _mapping_info(m) -> dict:
    return {'action': to_jsonable(m.get_editor_property('action')),
            'key': str(m.get_editor_property('key').get_editor_property('key_name')),
            'triggers': [t.get_class().get_name().replace('InputTrigger', '') for t in m.get_editor_property('triggers') or [] if t],
            'modifiers': [x.get_class().get_name().replace('InputModifier', '') for x in m.get_editor_property('modifiers') or [] if x]}


def _find_mapping_indices(mappings: list, action: unreal.InputAction, key_name: str | None) -> list[int]:
    out = []
    for i, m in enumerate(mappings):
        if m.get_editor_property('action') != action:
            continue
        if key_name and str(m.get_editor_property('key').get_editor_property('key_name')).lower() != key_name.lower():
            continue
        out.append(i)
    return out


@unreal.uclass()
class InputTools(unreal.ToolsetDefinition):
    """Enhanced Input authoring: Input Actions, Mapping Contexts, key mappings with triggers and
    modifiers, project default mapping contexts, and Blueprint wiring (AddMappingContext on
    BeginPlay, Enhanced Input Action events)."""

    @agent_tool(mutates=True)
    def create_input_action(asset_path: str, value_type: str = 'bool', triggers: str | None = None,
                            modifiers: str | None = None, consume_input: bool = True) -> dict:
        """Creates an Input Action asset.

        Args:
            asset_path: e.g. /Game/Input/IA_Jump.
            value_type: bool, axis1d, axis2d (move/look) or axis3d.
            triggers: Comma-separated action-level triggers (see module doc), e.g. "pressed".
            modifiers: Comma-separated action-level modifiers, e.g. "deadzone:0.2".
            consume_input: Consume lower-priority mappings of the same key.
        """
        package, _ = resolve.normalize_asset_path(asset_path)
        ctx().set_target(package)
        if resolve.asset_exists(package):
            raise ToolError(Code.ALREADY_EXISTS, f'{package} already exists', target=package)
        vt = _VALUE_TYPES.get(value_type.lower())
        if vt is None:
            raise ToolError(Code.INVALID_ARGUMENT, f'value_type must be one of {sorted(_VALUE_TYPES)}')
        scratch = None  # transient outer
        for spec in split_csv(triggers):  # validate specs before creating the asset
            _make_trigger(spec, scratch)
        for spec in split_csv(modifiers):
            _make_modifier(spec, scratch)
        folder, name = package.rsplit('/', 1)
        ia = editor.asset_tools().create_asset(name, folder, unreal.InputAction, unreal.InputAction_Factory())
        ia.set_editor_property('value_type', getattr(unreal.InputActionValueType, vt))
        ia.set_editor_property('consume_input', consume_input)
        if triggers:
            ia.set_editor_property('triggers', [_make_trigger(t, ia) for t in split_csv(triggers)])
        if modifiers:
            ia.set_editor_property('modifiers', [_make_modifier(m, ia) for m in split_csv(modifiers)])
        return {'asset': package, 'value_type': vt}

    @agent_tool(mutates=True)
    def create_input_mapping_context(asset_path: str, description: str | None = None) -> dict:
        """Creates an Input Mapping Context asset.

        Args:
            asset_path: e.g. /Game/Input/IMC_Default.
            description: Optional context description.
        """
        package, _ = resolve.normalize_asset_path(asset_path)
        ctx().set_target(package)
        if resolve.asset_exists(package):
            raise ToolError(Code.ALREADY_EXISTS, f'{package} already exists', target=package)
        folder, name = package.rsplit('/', 1)
        imc = editor.asset_tools().create_asset(name, folder, unreal.InputMappingContext, unreal.InputMappingContext_Factory())
        if description:
            imc.set_editor_property('context_description', unreal.Text(description))
        return {'asset': package}

    @agent_tool(mutates=True)
    def add_key_mapping(context_path: str, action_path: str, key: str, triggers: str | None = None,
                        modifiers: str | None = None) -> dict:
        """Maps a key to an Input Action inside a Mapping Context, with optional triggers/modifiers.
        WASD for a 2D move action: W -> "swizzle", S -> "swizzle,negate", A -> "negate", D -> (none).

        Args:
            context_path: Input Mapping Context path.
            action_path: Input Action path.
            key: Unreal key name, e.g. W, SpaceBar, Mouse2D, Gamepad_Left2D, LeftMouseButton.
            triggers: Comma-separated trigger specs, e.g. "hold:0.5".
            modifiers: Comma-separated modifier specs, e.g. "swizzle,negate".
        """
        imc = resolve.load_asset(context_path, unreal.InputMappingContext)
        ia = resolve.load_asset(action_path, unreal.InputAction)
        ctx().set_target(imc.get_outermost().get_name())
        k = _key(key)
        mappings, _ = _mappings(imc)
        if _find_mapping_indices(mappings, ia, key):
            raise ToolError(Code.ALREADY_EXISTS, f'{key} is already mapped to {ia.get_name()}',
                            likely_causes=['Use update_key_mapping to change triggers/modifiers.'])
        # Validate/construct everything before mutating so a bad spec leaves the context untouched.
        new_triggers = [_make_trigger(t, imc) for t in split_csv(triggers)]
        new_modifiers = [_make_modifier(x, imc) for x in split_csv(modifiers)]
        imc.map_key(ia, k)
        mappings, container = _mappings(imc)
        idx = _find_mapping_indices(mappings, ia, key)[-1]
        m = mappings[idx]
        if new_triggers:
            m.set_editor_property('triggers', new_triggers)
        if new_modifiers:
            m.set_editor_property('modifiers', new_modifiers)
        mappings[idx] = m
        _store_mappings(imc, mappings, container)
        return _mapping_info(_mappings(imc)[0][idx])

    @agent_tool(mutates=True)
    def update_key_mapping(context_path: str, action_path: str, key: str, triggers: str | None = None,
                           modifiers: str | None = None) -> dict:
        """Replaces the triggers and/or modifiers of an existing mapping ("none" clears the list).

        Args:
            context_path: Input Mapping Context path.
            action_path: Input Action path.
            key: Mapped key name.
            triggers: Trigger specs, or "none".
            modifiers: Modifier specs, or "none".
        """
        imc = resolve.load_asset(context_path, unreal.InputMappingContext)
        ia = resolve.load_asset(action_path, unreal.InputAction)
        ctx().set_target(imc.get_outermost().get_name())
        mappings, container = _mappings(imc)
        indices = _find_mapping_indices(mappings, ia, key)
        if not indices:
            raise ToolError(Code.OBJECT_NOT_FOUND, f'{key} is not mapped to {ia.get_name()}')
        if triggers is None and modifiers is None:
            raise ToolError(Code.INVALID_ARGUMENT, 'Pass triggers and/or modifiers.')
        m = mappings[indices[0]]
        if triggers is not None:
            m.set_editor_property('triggers', [] if triggers.lower() == 'none' else
                                  [_make_trigger(t, imc) for t in split_csv(triggers)])
        if modifiers is not None:
            m.set_editor_property('modifiers', [] if modifiers.lower() == 'none' else
                                  [_make_modifier(x, imc) for x in split_csv(modifiers)])
        mappings[indices[0]] = m
        _store_mappings(imc, mappings, container)
        return _mapping_info(_mappings(imc)[0][indices[0]])

    @agent_tool(mutates=True)
    def remove_key_mapping(context_path: str, action_path: str, key: str | None = None) -> dict:
        """Removes the mapping of a key (or all keys when key is omitted) for an action.

        Args:
            context_path: Input Mapping Context path.
            action_path: Input Action path.
            key: Key name; omit to remove every mapping of the action.
        """
        imc = resolve.load_asset(context_path, unreal.InputMappingContext)
        ia = resolve.load_asset(action_path, unreal.InputAction)
        ctx().set_target(imc.get_outermost().get_name())
        mappings, container = _mappings(imc)
        indices = set(_find_mapping_indices(mappings, ia, key))
        if not indices:
            raise ToolError(Code.OBJECT_NOT_FOUND, f'No mapping of {ia.get_name()}' + (f' to {key}' if key else ''))
        removed = [_mapping_info(m) for i, m in enumerate(mappings) if i in indices]
        _store_mappings(imc, [m for i, m in enumerate(mappings) if i not in indices], container)
        return {'removed': removed}

    @agent_tool()
    def list_key_mappings(context_path: str) -> dict:
        """Lists all mappings of a Mapping Context with keys, triggers, modifiers and action value types.

        Args:
            context_path: Input Mapping Context path.
        """
        imc = resolve.load_asset(context_path, unreal.InputMappingContext)
        ctx().set_target(imc.get_outermost().get_name())
        mappings, _ = _mappings(imc)
        actions = {}
        for m in mappings:
            a = m.get_editor_property('action')
            if a is not None:
                actions[a.get_outermost().get_name()] = to_jsonable(a.get_editor_property('value_type'))
        return {'context': imc.get_outermost().get_name(), 'mappings': [_mapping_info(m) for m in mappings],
                'action_value_types': actions}

    @agent_tool(mutates=True, transaction=False)
    def register_default_mapping_context(context_path: str, priority: int = 0, dry_run: bool = False) -> dict:
        """Adds a Mapping Context to Enhanced Input's project-wide Default Mapping Contexts
        (Config/DefaultInput.ini, backed up first), so it is applied to every local player
        without Blueprint wiring. Existing defaults are kept.

        Args:
            context_path: Input Mapping Context path (must be saved to take effect in packaged builds).
            priority: Mapping priority (higher wins).
            dry_run: Show the ini change without writing.
        """
        imc = resolve.load_asset(context_path, unreal.InputMappingContext)
        package = imc.get_outermost().get_name()
        object_path = f'{package}.{imc.get_name()}'
        entries = []
        for line in config.read_section('DefaultInput', _EI_SETTINGS_SECTION):
            key, _, value = line.partition('=')
            if key.lstrip('+').strip() == 'DefaultMappingContexts' and object_path not in value:
                entries.append(value)
        entries.append(f'(InputMappingContext="{object_path}",Priority={int(priority)},bAddImmediately=True,'
                       'bRegisterWithUserSettings=False)')
        change = config.set_values('DefaultInput', _EI_SETTINGS_SECTION,
                                   {'bEnableDefaultMappingContexts': 'True', 'DefaultMappingContexts': entries}, dry_run)
        ctx().set_target(package)
        if dry_run:
            ctx().mark_modified(False)
            return {'dry_run': True, 'change': change}
        live = False
        try:
            settings_cls = unreal.load_class(None, '/Script/EnhancedInput.EnhancedInputDeveloperSettings')
            cdo = unreal.get_default_object(settings_cls)
            ctx_list = [{'InputMappingContext': e.split('"')[1], 'Priority': int(e.split('Priority=')[1].split(',')[0])}
                        for e in entries]
            live = bool(unreal.ToolsetLibrary.set_object_properties(
                cdo, json.dumps({'bEnableDefaultMappingContexts': True, 'DefaultMappingContexts': ctx_list})))
        except Exception:  # pylint: disable=broad-exception-caught
            live = False
        if not live:
            ctx().warn('Written to DefaultInput.ini; restart the editor (or PIE after restart) to apply.', 'NOT_APPLIED_LIVE')
        if package in editor.dirty_package_names():
            ctx().warn(f'{package} is unsaved; save it so the soft reference resolves.', 'UNSAVED_CONTEXT')
        ctx().mark_modified(True)
        return {'change': change, 'applied_live': live}

    @agent_tool(mutates=True)
    def add_mapping_context_to_blueprint(blueprint_path: str, context_path: str, priority: int = 0) -> dict:
        """Wires "Event BeginPlay -> Get Player Controller(0) -> Enhanced Input Local Player Subsystem
        -> Add Mapping Context" into a Pawn/Character/PlayerController Blueprint (existing BeginPlay
        logic is preserved after the new node). Single-player helper; for multiplayer register the
        context in the PlayerController or use register_default_mapping_context.

        Args:
            blueprint_path: Blueprint asset path.
            context_path: Input Mapping Context path.
            priority: Mapping priority.
        """
        bp = resolve.load_asset(blueprint_path, unreal.Blueprint)
        imc = resolve.load_asset(context_path, unreal.InputMappingContext)
        ctx().set_target(bp.get_outermost().get_name())
        graph = unreal.BlueprintEditorLibrary.find_event_graph(bp)
        if graph is None:
            raise ToolError(Code.WRONG_TYPE, 'Blueprint has no event graph', target=bp.get_path_name())
        ed = bpu.graph_editor(graph)
        begin = unreal.BlueprintEditorLibrary.add_event_override(bp, 'ReceiveBeginPlay', unreal.IntPoint(0, 0))
        if begin is None:
            raise ToolError(Code.WRONG_TYPE, 'BeginPlay is not available on this Blueprint', target=bp.get_path_name())
        pos = begin.get_node_pos()
        then_pin = begin.find_then_pin()
        previous = list(then_pin.list_connected_pins() or [])
        def first_node(*paths):
            for path in paths:
                node = ed.add_call_function_node(path)
                if node is not None:
                    return node
            raise ToolError(Code.UE_OPERATION_FAILED, f'Could not create a node for any of {paths}',
                            likely_causes=['EnhancedInput plugin must be enabled.'])

        get_pc = first_node('/Script/Engine.GameplayStatics:GetPlayerController')
        add_ctx = first_node('/Script/EnhancedInput.EnhancedInputSubsystemInterface:AddMappingContext',
                             '/Script/EnhancedInput.EnhancedInputLocalPlayerSubsystem:AddMappingContext')
        # "Get EnhancedInputLocalPlayerSubsystem" (from PlayerController). Action strings are localized,
        # so match on the class name and the untranslated "PlayerController" category segment.
        actions = [a for a in ed.list_available_nodes([]) or []
                   if 'EnhancedInputLocalPlayerSubsystem' in a.split('|')[-1]]
        actions.sort(key=lambda a: 0 if a.split('|')[0] == 'PlayerController' else 1)
        get_sub = ed.create_node_from_name(actions[0], unreal.Vector2D(pos.x + 500, pos.y + 150), []) if actions else None
        if get_sub is None:
            raise ToolError(Code.UE_OPERATION_FAILED, 'Could not create "Get EnhancedInputLocalPlayerSubsystem" node',
                            likely_causes=['EnhancedInput plugin must be enabled.'])
        bpu.set_node_pos(get_pc, pos.x + 250, pos.y + 150)
        bpu.set_node_pos(add_ctx, pos.x + 800, pos.y)
        pc_in = [p for p in get_sub.list_input_pins() if 'Controller' in str(p.get_pin_name())]
        if pc_in:
            bpu.find_pin(graph, f'{get_pc.get_name()}.ReturnValue', 'out').try_create_connection(pc_in[0])
        sub_out = [p for p in get_sub.list_output_pins()][0]
        if not sub_out.try_create_connection(add_ctx.find_self_pin()):
            raise ToolError(Code.UE_OPERATION_FAILED, 'Could not connect the subsystem to AddMappingContext',
                            likely_causes=[f'Subsystem output type: {sub_out.get_pin_type_display_string()}'])
        then_pin.try_create_connection(add_ctx.find_execute_pin())
        bpu.find_pin(graph, f'{add_ctx.get_name()}.MappingContext', 'in').set_pin_value(
            f'{imc.get_outermost().get_name()}.{imc.get_name()}')
        bpu.find_pin(graph, f'{add_ctx.get_name()}.Priority', 'in').set_pin_value(str(int(priority)))
        for p in previous:
            add_ctx.find_then_pin().try_create_connection(p)
        report = bpu.compile_report(bp, compile_first=True)
        if report['status'] == 'error':
            raise ToolError(Code.COMPILE_FAILED, 'Blueprint failed to compile after wiring', target=bp.get_path_name(),
                            likely_causes=[e['message'] for e in report['errors'][:5]], details=report)
        return {'nodes': [bpu.node_info(n, include_pins=False) for n in (begin, get_pc, get_sub, add_ctx)],
                'status': report['status']}

    @agent_tool(mutates=True)
    def bind_input_action_event(blueprint_path: str, action_path: str, x: int = 0, y: int = 400) -> dict:
        """Adds an Enhanced Input Action event node (Triggered/Started/Ongoing/Canceled/Completed
        exec pins + ActionValue) for an Input Action to a Pawn/Character/PlayerController Blueprint.
        Connect its pins with BlueprintAuthoringTools.connect_blueprint_pins.

        Args:
            blueprint_path: Blueprint asset path.
            action_path: Input Action asset path.
            x: Node X.
            y: Node Y.
        """
        bp = resolve.load_asset(blueprint_path, unreal.Blueprint)
        ia = resolve.load_asset(action_path, unreal.InputAction)
        ctx().set_target(bp.get_outermost().get_name())
        graph = unreal.BlueprintEditorLibrary.find_event_graph(bp)
        ed = bpu.graph_editor(graph)
        for node in bpu.graph_nodes(graph):
            if node.get_class().get_name() == 'K2Node_EnhancedInputAction' and ia.get_name() in str(node.get_node_title()):
                raise ToolError(Code.ALREADY_EXISTS, f'An event for {ia.get_name()} already exists: {node.get_name()}',
                                target=bp.get_path_name())
        matches = [a for a in ed.list_available_nodes([]) or [] if a.split('|')[-1] == ia.get_name()]
        if not matches:
            raise ToolError(Code.UE_OPERATION_FAILED, f'No input action event available for {ia.get_name()}',
                            likely_causes=['Save the Input Action asset so it is visible to the node menu.',
                                           'The Blueprint must be an Actor (Pawn/Character/PlayerController) Blueprint.'])
        node = ed.create_node_from_name(matches[0], unreal.Vector2D(x, y), [])
        if node is None:
            raise ToolError(Code.UE_OPERATION_FAILED, f'Could not create the event node for {ia.get_name()}')
        return bpu.node_info(node)
