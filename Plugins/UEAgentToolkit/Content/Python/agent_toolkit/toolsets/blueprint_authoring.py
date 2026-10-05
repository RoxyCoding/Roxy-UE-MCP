"""BlueprintAuthoringTools: build and edit Blueprints with locale-independent, string-addressed
operations that match inspect_blueprint_graph output ("NodeId.PinName").

Complements Epic's editor_toolset BlueprintTools (create, set_parent, add_function_graph,
graph DSL, PinID-based node tools). Node creation here avoids localized category names:
functions are addressed by path ("KismetSystemLibrary:PrintString"), events by function
name ("ReceiveBeginPlay"), variables by name, macros by name/path.
"""

from __future__ import annotations

import unreal

from agent_toolkit.core import bp as bpu
from agent_toolkit.core import native, resolve
from agent_toolkit.core.errors import Code, ToolError
from agent_toolkit.core.serialize import from_jsonable, parse_json_arg, to_jsonable
from agent_toolkit.core.tooling import agent_tool, ctx

BEL = unreal.BlueprintEditorLibrary
BGE = unreal.BlueprintGraphEditor
_STANDARD_MACROS = '/Engine/EditorBlueprintResources/StandardMacros.StandardMacros'
_REPLICATION = {'none': 'NONE', 'replicated': 'REPLICATED', 'repnotify': 'REP_NOTIFY'}
_NET_MODES = {'none': 0, 'server': 1, 'client': 2, 'multicast': 3}


def _bp(asset_path: str) -> unreal.Blueprint:
    bp = resolve.load_asset(asset_path, unreal.Blueprint)
    ctx().set_target(bp.get_outermost().get_name())
    return bp


def _var_exists(bp: unreal.Blueprint, name: str) -> bool:
    return name in [str(n) for n in BEL.list_member_variable_names(bp, False) or []]


def _require_var(bp: unreal.Blueprint, name: str) -> None:
    if not _var_exists(bp, name):
        raise ToolError(Code.OBJECT_NOT_FOUND, f'Variable {name!r} not found', target=bp.get_path_name(),
                        likely_causes=[f'Variables: {[str(n) for n in BEL.list_member_variable_names(bp, False)]}'])


def _replication_enum(mode: str):
    key = _REPLICATION.get(mode.lower())
    enum = getattr(unreal, 'BlueprintVariableReplication', None)
    if key is None or enum is None:
        raise ToolError(Code.INVALID_ARGUMENT, f'replication must be one of {sorted(_REPLICATION)}')
    for name in (key, key.replace('_', '')):
        if hasattr(enum, name):
            return getattr(enum, name)
    return getattr(enum, [m for m in dir(enum) if m.replace('_', '') == key.replace('_', '')][0])


def _set_replication_condition(bp: unreal.Blueprint, name: str, condition: str) -> None:
    lib = native.require('Replication conditions')
    cond = condition.upper()
    cond = cond if cond.startswith('COND_') else 'COND_' + cond
    enum = unreal.LifetimeCondition
    if not hasattr(enum, cond):
        raise ToolError(Code.INVALID_ARGUMENT, f'Unknown replication condition {condition!r}',
                        likely_causes=[f'Valid: {[m for m in dir(enum) if m.startswith("COND_")]}'])
    if not lib.set_variable_replication_condition(bp, name, getattr(enum, cond).value):
        raise ToolError(Code.UE_OPERATION_FAILED, f'Could not set replication condition on {name}')


def _cdo(bp: unreal.Blueprint) -> unreal.Object:
    BEL.compile_blueprint(bp)
    gen = bp.generated_class()
    if gen is None:
        raise ToolError(Code.COMPILE_FAILED, 'Blueprint has no generated class (compile failed?)',
                        target=bp.get_path_name())
    return unreal.get_default_object(gen)


def _set_default(bp: unreal.Blueprint, name: str, value_json: str) -> object:
    cdo = _cdo(bp)
    value = parse_json_arg(value_json, 'value_json', (dict, list, str, int, float, bool, type(None)))
    current = cdo.get_editor_property(name)
    converted = from_jsonable(value, current, name)
    cdo.modify()
    cdo.set_editor_property(name, converted)
    bp.modify()
    return to_jsonable(cdo.get_editor_property(name))


def _function_path(identifier: str) -> str:
    """'Character:Jump' / 'KismetSystemLibrary:PrintString' / full '/Script/Engine.X:Fn' -> function path."""
    if ':' not in identifier:
        raise ToolError(Code.INVALID_ARGUMENT, f'Function identifier must be "Class:Function", got {identifier!r}',
                        likely_causes=['e.g. "KismetSystemLibrary:PrintString", "Character:Jump", '
                                       '"/Script/Engine.Pawn:AddMovementInput"'])
    cls_name, fn = identifier.rsplit(':', 1)
    if cls_name.startswith('/'):
        return f'{cls_name}:{fn}'
    cls = resolve.resolve_class(cls_name)
    return f'{cls.get_path_name()}:{fn}'


def _find_event_node(graph: unreal.EdGraph, identifier: str):
    """Existing K2Node_Event for an overridable event ("ReceiveTick" or "Tick"), or None."""
    wanted = {identifier.lower(), ('receive' + identifier).lower()}
    for node in bpu.graph_nodes(graph):
        if node.get_class().get_name() != 'K2Node_Event':
            continue
        try:
            name = str(node.get_editor_property('event_reference').get_editor_property('member_name'))
        except Exception:  # pylint: disable=broad-exception-caught
            continue
        if name.lower() in wanted:
            return node
    return None


def _widget_variable(bp: unreal.Blueprint, name: str):
    """Designer widget (a widget-variable in a Widget Blueprint) by name, else None."""
    if not isinstance(bp, unreal.WidgetBlueprint):
        return None
    lib = getattr(unreal, 'AgentToolkitWorldLibrary', None)
    if lib is None:
        return None
    try:
        return lib.find_widget_in_blueprint(bp, name)
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def _graph_editor(bp: unreal.Blueprint, graph_name: str | None):
    graph = bpu.find_graph(bp, graph_name or '')
    return graph, bpu.graph_editor(graph)


def _function_graph_editor(bp: unreal.Blueprint, function_name: str):
    graph = BEL.find_graph(bp, function_name)
    if graph is None:
        raise ToolError(Code.OBJECT_NOT_FOUND, f'Function graph {function_name!r} not found', target=bp.get_path_name(),
                        likely_causes=[f'Functions: {[f["name"] for f in bpu.function_infos(bp)]}'])
    return graph, bpu.graph_editor(graph)


@unreal.uclass()
class BlueprintAuthoringTools(unreal.ToolsetDefinition):
    """Blueprint editing: typed variables with defaults/replication, components (SCS),
    functions with typed signatures, custom events (incl. RPC), macros, interfaces, and
    name-addressed graph editing (add node, connect/disconnect pins, pin defaults, remove/move
    nodes). Pair with InspectorTools.inspect_blueprint(_graph) and BuildDebugTools.compile_blueprint."""

    # ------------------------------------------------------------------ variables
    @agent_tool(mutates=True)
    def add_blueprint_variable(asset_path: str, variable_name: str, variable_type: str,
                               default_value_json: str | None = None, category: str | None = None,
                               instance_editable: bool = False, expose_on_spawn: bool = False,
                               replication: str = 'none', replication_condition: str | None = None) -> dict:
        """Adds a member variable with type, default value, category, editability and replication in one call.

        Args:
            asset_path: Blueprint asset path.
            variable_name: New variable name.
            variable_type: bool, int, int64, float, name, string, text, byte, vector, rotator, transform,
                linearcolor, object:<Class>, class:<Class>, softobject:<Class>, struct:<Path>, enum:<Path>,
                array:<type>, set:<type>. Example: "object:Actor", "array:vector".
            default_value_json: JSON default, e.g. 100, true, [0,0,1], "text", "/Game/Path/Asset".
            category: Details-panel category.
            instance_editable: Editable on placed instances ("eye" icon).
            expose_on_spawn: Show on SpawnActor nodes (requires instance_editable).
            replication: none, replicated or repnotify (creates OnRep_<Name>).
            replication_condition: Optional lifetime condition, e.g. OWNER_ONLY, SKIP_OWNER, INITIAL_ONLY (native module).
        """
        bp = _bp(asset_path)
        if _var_exists(bp, variable_name):
            raise ToolError(Code.ALREADY_EXISTS, f'Variable {variable_name!r} already exists', target=bp.get_path_name())
        pin_type = bpu.parse_pin_type(variable_type)
        if not BEL.add_member_variable(bp, variable_name, pin_type):
            raise ToolError(Code.UE_OPERATION_FAILED, f'Could not add variable {variable_name!r}',
                            likely_causes=['The name may collide with an inherited variable or function.'])
        if category:
            BEL.set_blueprint_variable_category(bp, variable_name, unreal.Text(category))
        if instance_editable or expose_on_spawn:
            BEL.set_blueprint_variable_instance_editable(bp, variable_name, True)
        if expose_on_spawn:
            BEL.set_blueprint_variable_expose_on_spawn(bp, variable_name, True)
        if replication.lower() != 'none':
            BEL.set_blueprint_variable_replication(bp, variable_name, _replication_enum(replication))
        if replication_condition:
            _set_replication_condition(bp, variable_name, replication_condition)
        result = {'variable': variable_name, 'type': bpu.pin_type_to_string(BEL.get_member_variable_type(bp, variable_name))}
        if default_value_json is not None:
            try:
                result['default'] = _set_default(bp, variable_name, default_value_json)
            except Exception as e:  # roll back so a bad default does not leave a half-configured variable
                bpu.graph_editor(BEL.find_event_graph(bp) or bpu.graphs(bp)[0]).remove_member_variable(variable_name)
                raise ToolError(Code.INVALID_ARGUMENT, f'Invalid default for {variable_name} ({variable_type}): {e}; '
                                'variable was not added', target=bp.get_path_name()) from e
        return result

    @agent_tool(mutates=True)
    def set_blueprint_variable_default(asset_path: str, variable_name: str, value_json: str) -> dict:
        """Sets the default value of a Blueprint variable (or any inherited CDO property).

        Args:
            asset_path: Blueprint asset path.
            variable_name: Variable or property name (inherited native properties work too,
                e.g. "JumpMaxCount" on a Character Blueprint).
            value_json: JSON value, e.g. 600, true, [1,2,3], {"r":1,"g":0,"b":0,"a":1}, "/Game/Mesh".
        """
        bp = _bp(asset_path)
        try:
            value = _set_default(bp, variable_name, value_json)
        except ToolError:
            raise
        except Exception as e:  # pylint: disable=broad-exception-caught
            raise ToolError(Code.INVALID_ARGUMENT, f'Could not set {variable_name}: {e}', target=bp.get_path_name(),
                            likely_causes=['Check the name with inspect_blueprint (variables) or ObjectTools.list_properties.',
                                           'Value shape must match the type (vector = [x,y,z]).']) from e
        return {'variable': variable_name, 'default': value}

    @agent_tool(mutates=True)
    def change_blueprint_variable_type(asset_path: str, variable_name: str, new_type: str) -> dict:
        """Changes the type of a Blueprint variable (connected pins of incompatible type will break;
        recompile and check errors afterwards).

        Args:
            asset_path: Blueprint asset path.
            variable_name: Existing variable declared in this Blueprint.
            new_type: Type string (see add_blueprint_variable).
        """
        bp = _bp(asset_path)
        _require_var(bp, variable_name)
        before = bpu.pin_type_to_string(BEL.get_member_variable_type(bp, variable_name))
        BEL.change_member_variable_type(bp, variable_name, bpu.parse_pin_type(new_type))
        after = bpu.pin_type_to_string(BEL.get_member_variable_type(bp, variable_name))
        return {'variable': variable_name, 'old_type': before, 'new_type': after}

    @agent_tool(mutates=True)
    def set_blueprint_variable_flags(asset_path: str, variable_name: str, category: str | None = None,
                                     instance_editable: bool | None = None, expose_on_spawn: bool | None = None,
                                     replication: str | None = None, replication_condition: str | None = None) -> dict:
        """Updates variable metadata. Only provided arguments are changed.

        Args:
            asset_path: Blueprint asset path.
            variable_name: Variable name.
            category: New category.
            instance_editable: Editable per instance.
            expose_on_spawn: Expose on spawn.
            replication: none, replicated or repnotify.
            replication_condition: Lifetime condition, e.g. NONE, OWNER_ONLY, SKIP_OWNER (native module).
        """
        bp = _bp(asset_path)
        _require_var(bp, variable_name)
        changed = []
        if category is not None:
            BEL.set_blueprint_variable_category(bp, variable_name, unreal.Text(category))
            changed.append('category')
        if instance_editable is not None:
            BEL.set_blueprint_variable_instance_editable(bp, variable_name, instance_editable)
            changed.append('instance_editable')
        if expose_on_spawn is not None:
            BEL.set_blueprint_variable_expose_on_spawn(bp, variable_name, expose_on_spawn)
            changed.append('expose_on_spawn')
        if replication is not None:
            BEL.set_blueprint_variable_replication(bp, variable_name, _replication_enum(replication))
            changed.append('replication')
        if replication_condition is not None:
            _set_replication_condition(bp, variable_name, replication_condition)
            changed.append('replication_condition')
        if not changed:
            raise ToolError(Code.INVALID_ARGUMENT, 'No flag argument was provided.')
        return {'variable': variable_name, 'changed': changed}

    # ----------------------------------------------------------------- components
    @agent_tool(mutates=True)
    def add_blueprint_component(asset_path: str, component_class: str, component_name: str,
                                parent_component: str | None = None, properties_json: str | None = None) -> dict:
        """Adds a component to a Blueprint's component tree (SCS), optionally under a parent
        and with initial properties (e.g. {"relative_location": [0,0,60], "static_mesh": "/Engine/BasicShapes/Cube"}).

        Args:
            asset_path: Blueprint asset path.
            component_class: Component class, e.g. StaticMeshComponent, SpringArmComponent, CameraComponent,
                SphereComponent, AudioComponent, NiagaraComponent, or a Blueprint component path.
            component_name: Variable name of the new component.
            parent_component: Existing component to attach to (default: root).
            properties_json: JSON object of component properties.
        """
        bp = _bp(asset_path)
        cls = resolve.resolve_class(component_class, unreal.ActorComponent)
        sds = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
        existing = [c['name'] for c in bpu.component_tree(bp)]
        if component_name in existing:
            raise ToolError(Code.ALREADY_EXISTS, f'Component {component_name!r} already exists', target=bp.get_path_name())
        handles = sds.k2_gather_subobject_data_for_blueprint(bp) or []
        if not handles:
            raise ToolError(Code.WRONG_TYPE, 'This Blueprint has no component tree (not an Actor Blueprint?)',
                            target=bp.get_path_name())
        parent_handle = bpu.find_subobject(bp, parent_component)[0] if parent_component else handles[0]
        params = unreal.AddNewSubobjectParams(parent_handle=parent_handle, new_class=cls, blueprint_context=bp)
        handle, fail_reason = sds.add_new_subobject(params)
        if fail_reason and str(fail_reason):
            raise ToolError(Code.UE_OPERATION_FAILED, f'Could not add component: {fail_reason}', target=bp.get_path_name())
        sds.rename_subobject(handle, unreal.Text(component_name))
        applied = []
        if properties_json:
            try:
                applied = _apply_template_properties(bp, component_name, properties_json)
            except Exception:  # roll back the new component on invalid properties
                sds.delete_subobject(handles[0], handle, bp)
                raise
        BEL.compile_blueprint(bp)
        return {'component': component_name, 'class': cls.get_name(), 'parent': parent_component or 'root',
                'properties_set': applied}

    @agent_tool(mutates=True)
    def remove_blueprint_component(asset_path: str, component_name: str) -> dict:
        """Removes a component added in this Blueprint (inherited/native components cannot be removed).

        Args:
            asset_path: Blueprint asset path.
            component_name: Component variable name.
        """
        bp = _bp(asset_path)
        handle, data = bpu.find_subobject(bp, component_name)
        lib = unreal.SubobjectDataBlueprintFunctionLibrary
        if lib.is_inherited_component(data) or lib.is_native_component(data):
            raise ToolError(Code.NOT_SUPPORTED, f'{component_name} is inherited/native and cannot be removed here',
                            target=bp.get_path_name(), likely_causes=['Edit the parent class instead, or hide/disable it.'])
        sds = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
        root = sds.k2_gather_subobject_data_for_blueprint(bp)[0]
        removed = sds.delete_subobject(root, handle, bp)
        if not removed:
            raise ToolError(Code.UE_OPERATION_FAILED, f'Could not remove {component_name}', target=bp.get_path_name())
        BEL.compile_blueprint(bp)
        return {'removed': component_name}

    @agent_tool(mutates=True)
    def set_blueprint_component_properties(asset_path: str, component_name: str, properties_json: str) -> dict:
        """Sets default properties of a component template in a Blueprint, e.g.
        {"static_mesh": "/Engine/BasicShapes/Sphere", "relative_scale3d": [2,2,2], "target_arm_length": 400}.
        Inherited components (e.g. CharacterMovement) are supported.

        Args:
            asset_path: Blueprint asset path.
            component_name: Component variable name.
            properties_json: JSON object of property names to values.
        """
        bp = _bp(asset_path)
        applied = _apply_template_properties(bp, component_name, properties_json)
        BEL.compile_blueprint(bp)
        return {'component': component_name, 'properties_set': applied}

    @agent_tool(mutates=True)
    def attach_blueprint_component(asset_path: str, component_name: str, new_parent: str) -> dict:
        """Re-parents a scene component within the Blueprint's component tree.

        Args:
            asset_path: Blueprint asset path.
            component_name: Component to move.
            new_parent: Scene component to attach to.
        """
        bp = _bp(asset_path)
        child, _ = bpu.find_subobject(bp, component_name)
        parent, _ = bpu.find_subobject(bp, new_parent)
        sds = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
        if not sds.attach_subobject(parent, child):
            raise ToolError(Code.UE_OPERATION_FAILED, f'Could not attach {component_name} to {new_parent}',
                            likely_causes=['Only scene components can be parents; inherited components cannot be moved.'])
        BEL.compile_blueprint(bp)
        return {'component': component_name, 'parent': new_parent}

    # ------------------------------------------------------------ functions/events
    @agent_tool(mutates=True)
    def create_blueprint_function(asset_path: str, function_name: str, inputs_json: str | None = None,
                                  outputs_json: str | None = None, pure: bool = False, const: bool = False,
                                  access: str = 'public', category: str | None = None) -> dict:
        """Creates a function graph with a typed signature.

        Args:
            asset_path: Blueprint asset path.
            function_name: New function name.
            inputs_json: JSON list of inputs, e.g. [{"name": "Damage", "type": "float"}].
            outputs_json: JSON list of outputs, e.g. [{"name": "IsDead", "type": "bool"}].
            pure: Pure function (no exec pins; must not change state).
            const: Const function.
            access: public, protected or private.
            category: Optional function category (unused if the API is unavailable).
        """
        bp = _bp(asset_path)
        if BEL.find_graph(bp, function_name) is not None:
            raise ToolError(Code.ALREADY_EXISTS, f'Graph {function_name!r} already exists', target=bp.get_path_name())
        inputs = parse_json_arg(inputs_json or '', 'inputs_json', list)
        outputs = parse_json_arg(outputs_json or '', 'outputs_json', list)
        ed = BGE.create_and_edit_function_graph(bp, function_name)
        if ed is None:
            raise ToolError(Code.UE_OPERATION_FAILED, f'Could not create function {function_name}')
        for item in inputs:
            ed.add_graph_input_parameter(item['name'], bpu.parse_pin_type(item['type']), str(item.get('default', '')))
        for item in outputs:
            ed.add_graph_output_parameter(item['name'], bpu.parse_pin_type(item['type']))
        _apply_function_flags(ed, pure, const, access, None)
        del category
        graph = BEL.find_graph(bp, function_name)
        return {'function': function_name, 'nodes': [bpu.node_info(n) for n in bpu.graph_nodes(graph)]}

    @agent_tool(mutates=True)
    def set_blueprint_function_flags(asset_path: str, function_name: str, pure: bool | None = None,
                                     const: bool | None = None, access: str | None = None,
                                     call_in_editor: bool | None = None) -> dict:
        """Changes function specifiers. Only provided arguments are changed.

        Args:
            asset_path: Blueprint asset path.
            function_name: Function graph name.
            pure: Pure (no exec pins) or impure.
            const: Const function.
            access: public, protected or private.
            call_in_editor: Show as a button in the Details panel.
        """
        bp = _bp(asset_path)
        _, ed = _function_graph_editor(bp, function_name)
        changed = _apply_function_flags(ed, pure, const, access, call_in_editor)
        if not changed:
            raise ToolError(Code.INVALID_ARGUMENT, 'No flag argument was provided.')
        return {'function': function_name, 'changed': changed}

    @agent_tool(mutates=True)
    def add_custom_event(asset_path: str, event_name: str, inputs_json: str | None = None,
                         replication: str = 'none', reliable: bool = False, graph_name: str | None = None,
                         x: int = 0, y: int = 0) -> dict:
        """Adds a Custom Event, optionally with inputs and RPC replication (Server/Client/Multicast).

        Args:
            asset_path: Blueprint asset path.
            event_name: Event name.
            inputs_json: JSON list, e.g. [{"name": "Amount", "type": "float"}] (needs native module).
            replication: none, server (Run on Server), client (Run on owning Client) or multicast (needs native module).
            reliable: Reliable RPC.
            graph_name: Event graph name (default EventGraph).
            x: Node X position.
            y: Node Y position.
        """
        bp = _bp(asset_path)
        graph, ed = _graph_editor(bp, graph_name)
        node = ed.add_custom_event_node(event_name)
        if node is None:
            raise ToolError(Code.UE_OPERATION_FAILED, f'Could not add custom event {event_name!r}',
                            likely_causes=['Custom events can only be added to event graphs.',
                                           'The name may already be used by a function or event.'])
        bpu.set_node_pos(node, x, y)
        inputs = parse_json_arg(inputs_json or '', 'inputs_json', list)
        if inputs or replication.lower() != 'none':
            lib = native.require('Custom event inputs / RPC replication')
            for item in inputs:
                if not lib.add_custom_event_input(node, item['name'], bpu.parse_pin_type(item['type'])):
                    raise ToolError(Code.UE_OPERATION_FAILED, f"Could not add input {item['name']!r}")
            mode = replication.lower()
            if mode not in _NET_MODES:
                raise ToolError(Code.INVALID_ARGUMENT, f'replication must be one of {sorted(_NET_MODES)}')
            if mode != 'none':
                lib.set_custom_event_replication(node, _NET_MODES[mode], reliable)
        del graph
        info = bpu.node_info(node)
        lib = native.library()
        if lib is not None:
            info['replication'] = str(lib.get_custom_event_replication(node))
        return info

    @agent_tool(mutates=True)
    def create_blueprint_macro(asset_path: str, macro_name: str) -> dict:
        """Creates an empty macro graph (requires the native module). Add tunnel pins and
        nodes with add_blueprint_node / Epic BlueprintTools afterwards.

        Args:
            asset_path: Blueprint asset path.
            macro_name: Macro name.
        """
        bp = _bp(asset_path)
        lib = native.require('Macro graph creation')
        graph = lib.add_macro_graph(bp, macro_name)
        if graph is None:
            raise ToolError(Code.ALREADY_EXISTS, f'A graph named {macro_name!r} already exists', target=bp.get_path_name())
        return {'macro': macro_name, 'graph': graph.get_path_name()}

    @agent_tool(mutates=True)
    def implement_blueprint_interface(asset_path: str, interface_class: str) -> dict:
        """Adds an interface (Blueprint Interface asset or native UInterface) to a Blueprint and
        lists the interface functions now available (requires the native module).

        Args:
            asset_path: Blueprint asset path.
            interface_class: Interface Blueprint path (e.g. /Game/BPI_Interact) or native interface class name.
        """
        bp = _bp(asset_path)
        lib = native.require('Interface implementation')
        iface = resolve.resolve_class(interface_class)
        if iface in list(lib.get_implemented_interfaces(bp) or []):
            raise ToolError(Code.ALREADY_EXISTS, f'{iface.get_name()} is already implemented', target=bp.get_path_name())
        if not lib.add_interface_to_blueprint(bp, iface):
            raise ToolError(Code.WRONG_TYPE, f'{iface.get_name()} is not an interface class', target=bp.get_path_name())
        BEL.compile_blueprint(bp)
        return {'interface': iface.get_path_name(), 'interfaces': bpu.implemented_interfaces(bp),
                'functions': bpu.function_infos(bp, implemented_only=False),
                'events': [e for e in bpu.event_infos(bp) if not e['implemented']][:30]}

    @agent_tool(mutates=True)
    def remove_blueprint_interface(asset_path: str, interface_class: str, preserve_functions: bool = False) -> dict:
        """Removes an implemented interface (requires the native module).

        Args:
            asset_path: Blueprint asset path.
            interface_class: Interface class or Blueprint Interface path.
            preserve_functions: Keep implemented interface graphs as regular functions.
        """
        bp = _bp(asset_path)
        lib = native.require('Interface removal')
        iface = resolve.resolve_class(interface_class)
        if not lib.remove_interface_from_blueprint(bp, iface, preserve_functions):
            raise ToolError(Code.OBJECT_NOT_FOUND, f'{iface.get_name()} is not implemented by this Blueprint',
                            target=bp.get_path_name())
        BEL.compile_blueprint(bp)
        return {'removed': iface.get_path_name(), 'interfaces': bpu.implemented_interfaces(bp)}

    # ------------------------------------------------------------------- graph ops
    @agent_tool(mutates=True)
    def add_blueprint_node(asset_path: str, node_kind: str, identifier: str | None = None, graph_name: str | None = None,
                           x: int = 0, y: int = 0) -> dict:
        """Adds a node using locale-independent identifiers and returns its id and pins.

        Args:
            asset_path: Blueprint asset path.
            node_kind: function, event, custom_event, variable_get, variable_set, branch, macro,
                component_event or action.
            identifier: Depends on node_kind:
                function -> "Class:Function" (e.g. "KismetSystemLibrary:PrintString", "Character:Jump",
                    "/Script/Engine.Pawn:AddMovementInput");
                event -> overridable event function, e.g. "ReceiveBeginPlay", "ReceiveTick", "ReceiveActorBeginOverlap"
                    (an already existing event node, such as a widget's default Tick, is returned, not duplicated);
                custom_event -> event name; variable_get/variable_set -> variable name;
                macro -> standard macro name (ForEachLoop, DoOnce, Gate, IsValid, FlipFlop, WhileLoop) or macro path;
                component_event -> "ComponentName:DelegateName" (e.g. "Box:OnComponentBeginOverlap");
                    in a Widget Blueprint ComponentName may be a widget variable (e.g. "StartButton:OnClicked");
                action -> action string from search_blueprint_node_actions (localized, last resort; needed for
                    nodes function cannot create, e.g. "UserWidget:GetOwningPlayer" or property setters such as
                    PlayerController SetShowMouseCursor in UE 5.8).
            graph_name: Target graph (default EventGraph).
            x: Node X position.
            y: Node Y position.
        """
        bp = _bp(asset_path)
        graph, ed = _graph_editor(bp, graph_name)
        kind = node_kind.lower()
        identifier = identifier or ''
        if kind != 'branch' and not identifier:
            raise ToolError(Code.INVALID_ARGUMENT, f'node_kind={kind} requires an identifier')
        node = None
        if kind == 'function':
            node = ed.add_call_function_node(_function_path(identifier))
        elif kind == 'event':
            existing = _find_event_node(graph, identifier)
            if existing is not None:
                # Widget Blueprints and others already ship default Tick/BeginPlay nodes; a second
                # one would be a duplicate event that fails to compile.
                ctx().warn(f'Event {identifier!r} already exists in the graph; returning the existing node.',
                           'EVENT_EXISTS')
                return bpu.node_info(existing)
            node = BEL.add_event_override(bp, identifier, unreal.IntPoint(x, y))
            if node is None and not identifier.startswith('Receive'):
                node = BEL.add_event_override(bp, 'Receive' + identifier, unreal.IntPoint(x, y))
        elif kind == 'custom_event':
            node = ed.add_custom_event_node(identifier)
        elif kind == 'variable_get':
            node = ed.add_get_member_variable_node(identifier)
        elif kind == 'variable_set':
            node = ed.add_set_member_variable_node(identifier)
        elif kind == 'branch':
            node = ed.add_branch_node()
        elif kind == 'macro':
            path = identifier if identifier.startswith('/') else f'{_STANDARD_MACROS}:{identifier}'
            node = ed.add_macro_node(path)
        elif kind == 'component_event':
            comp_name, _, delegate = identifier.partition(':')
            template = _widget_variable(bp, comp_name)
            if template is None:
                _, data = bpu.find_subobject(bp, comp_name)
                template = unreal.SubobjectDataBlueprintFunctionLibrary.get_object_for_blueprint(data, bp)
            node = ed.add_component_bound_event_node(template, delegate)
        elif kind == 'action':
            actions = list(ed.list_available_nodes([]) or [])
            match = [a for a in actions if a == identifier] or [a for a in actions if a.split('|')[-1] == identifier] \
                or [a for a in actions if a.split('|')[-1].lower() == identifier.lower()]
            if not match:
                raise ToolError(Code.OBJECT_NOT_FOUND, f'No node action matches {identifier!r}',
                                likely_causes=['Use search_blueprint_node_actions to find the exact action string.'])
            if len(match) > 1 and match[0] != identifier:
                ctx().warn(f'{len(match)} actions matched; used {match[0]!r}', 'AMBIGUOUS_ACTION')
            node = ed.create_node_from_name(match[0], unreal.Vector2D(x, y), [])
        else:
            raise ToolError(Code.INVALID_ARGUMENT, f'Unknown node_kind {node_kind!r}',
                            likely_causes=['function, event, custom_event, variable_get, variable_set, branch, '
                                           'macro, component_event, action'])
        if node is None:
            raise ToolError(Code.UE_OPERATION_FAILED, f'Could not create {kind} node for {identifier!r}',
                            target=graph.get_path_name(),
                            likely_causes=['Function/variable/event name is wrong or not available in this Blueprint '
                                           'context (check inspect_blueprint).',
                                           'Events and custom events require an event graph.'])
        if kind != 'event':
            bpu.set_node_pos(node, x, y)
        return bpu.node_info(node)

    @agent_tool(mutates=True)
    def connect_blueprint_pins(asset_path: str, connections: list[str], graph_name: str | None = None) -> dict:
        """Connects pins. Each connection is "FromNode.OutPin->ToNode.InPin" using ids from
        inspect_blueprint_graph / add_blueprint_node (aliases: exec/then). Type conversion nodes
        are inserted automatically when Unreal allows it.

        Args:
            asset_path: Blueprint asset path.
            connections: e.g. ["K2Node_Event_0.then->K2Node_CallFunction_0.execute",
                "K2Node_VariableGet_0.Speed->K2Node_CallFunction_0.ScaleValue"].
            graph_name: Graph name (default EventGraph).
        """
        bp = _bp(asset_path)
        graph = bpu.find_graph(bp, graph_name or '')
        made, failed = [], []
        for conn in connections:
            if '->' not in conn:
                raise ToolError(Code.INVALID_ARGUMENT, f'Connection must be "A.Pin->B.Pin", got {conn!r}')
            src_text, dst_text = [c.strip() for c in conn.split('->', 1)]
            src = bpu.find_pin(graph, src_text, 'out')
            dst = bpu.find_pin(graph, dst_text, 'in')
            if src.try_create_connection(dst):
                made.append(conn)
            else:
                failed.append({'connection': conn, 'from_type': str(src.get_pin_type_display_string()),
                               'to_type': str(dst.get_pin_type_display_string())})
        if failed and not made:
            raise ToolError(Code.UE_OPERATION_FAILED, f'{len(failed)} connections were rejected by the schema',
                            target=graph.get_path_name(),
                            likely_causes=[f"{f['connection']}: {f['from_type']} -> {f['to_type']}" for f in failed[:5]],
                            details={'failed': failed})
        for f in failed:
            ctx().warn(f"rejected {f['connection']} ({f['from_type']} -> {f['to_type']})", 'CONNECTION_REJECTED')
        return {'connected': made, 'failed': failed}

    @agent_tool(mutates=True)
    def disconnect_blueprint_pins(asset_path: str, pin: str, other_pin: str | None = None,
                                  graph_name: str | None = None) -> dict:
        """Breaks links of a pin (all links, or only the link to other_pin).

        Args:
            asset_path: Blueprint asset path.
            pin: "NodeId.PinName".
            other_pin: Optional "NodeId.PinName" to break only that link.
            graph_name: Graph name (default EventGraph).
        """
        bp = _bp(asset_path)
        graph = bpu.find_graph(bp, graph_name or '')
        p = bpu.find_pin(graph, pin)
        before = [bpu.pin_ref(x) for x in p.list_connected_pins() or []]
        if other_pin:
            o = bpu.find_pin(graph, other_pin)
            p.break_single_pin_link(o)
        else:
            p.break_pin_links()
        after = [bpu.pin_ref(x) for x in p.list_connected_pins() or []]
        return {'pin': pin, 'removed_links': [l for l in before if l not in after]}

    @agent_tool(mutates=True)
    def set_blueprint_pin_defaults(asset_path: str, values_json: str, graph_name: str | None = None) -> dict:
        """Sets default values of unconnected input pins, e.g. {"K2Node_CallFunction_0.InString": "Hello",
        "K2Node_CallFunction_1.Duration": "2.0"}. Values use Unreal's pin text format
        (vectors "X=0,Y=0,Z=1" or "0,0,1"; objects as asset paths).

        Args:
            asset_path: Blueprint asset path.
            values_json: JSON object mapping "NodeId.Pin" to value strings.
            graph_name: Graph name (default EventGraph).
        """
        bp = _bp(asset_path)
        graph = bpu.find_graph(bp, graph_name or '')
        values = parse_json_arg(values_json, 'values_json', dict)
        result = {}
        for ref, value in values.items():
            pin = bpu.find_pin(graph, ref, 'in')
            pin.set_pin_value(str(value).lower() if isinstance(value, bool) else str(value))
            result[ref] = str(pin.get_pin_value())
        return {'values': result}

    @agent_tool(mutates=True)
    def remove_blueprint_nodes(asset_path: str, node_ids: list[str], graph_name: str | None = None) -> dict:
        """Deletes nodes (and their links) from a graph.

        Args:
            asset_path: Blueprint asset path.
            node_ids: Node ids, e.g. ["K2Node_CallFunction_2"].
            graph_name: Graph name (default EventGraph).
        """
        bp = _bp(asset_path)
        graph, ed = _graph_editor(bp, graph_name)
        nodes = [bpu.find_node(graph, n) for n in node_ids]
        ed.remove_nodes(nodes)
        remaining = {n.get_name() for n in bpu.graph_nodes(graph)}
        return {'removed': [n for n in node_ids if n not in remaining],
                'not_removed': [n for n in node_ids if n in remaining]}

    @agent_tool(mutates=True)
    def move_blueprint_node(asset_path: str, node_id: str, x: int, y: int, graph_name: str | None = None) -> dict:
        """Moves a node to a new graph position.

        Args:
            asset_path: Blueprint asset path.
            node_id: Node id.
            x: New X.
            y: New Y.
            graph_name: Graph name (default EventGraph).
        """
        bp = _bp(asset_path)
        node = bpu.find_node(bpu.find_graph(bp, graph_name or ''), node_id)
        bpu.set_node_pos(node, x, y)
        return {'node': node_id, 'pos': [x, y]}

    @agent_tool()
    def search_blueprint_node_actions(asset_path: str, query: str, graph_name: str | None = None,
                                      limit: int = 50) -> dict:
        """Searches the node actions available in a graph (the editor's right-click menu). Strings
        are localized to the editor language; prefer node_kind=function/event/... when possible.

        Args:
            asset_path: Blueprint asset path.
            query: Case-insensitive substring, e.g. "Jump" or "PrintString".
            graph_name: Graph name (default EventGraph).
            limit: Maximum results.
        """
        bp = _bp(asset_path)
        _, ed = _graph_editor(bp, graph_name)
        q = query.lower()
        matches = [a for a in ed.list_available_nodes([]) or [] if q in a.lower()]
        return {'total': len(matches), 'actions': matches[:limit]}


def _apply_function_flags(ed, pure, const, access, call_in_editor) -> list[str]:
    changed = []
    if pure is not None:
        ed.set_is_pure_function(pure)
        changed.append('pure')
    if const is not None:
        ed.set_is_const_function(const)
        changed.append('const')
    if access is not None:
        fn = {'public': ed.set_function_is_public, 'protected': ed.set_function_is_protected,
              'private': ed.set_function_is_private}.get(access.lower())
        if fn is None:
            raise ToolError(Code.INVALID_ARGUMENT, 'access must be public, protected or private')
        fn()
        changed.append('access')
    if call_in_editor is not None:
        ed.set_is_call_in_editor_function(call_in_editor)
        changed.append('call_in_editor')
    return changed


def _apply_template_properties(bp: unreal.Blueprint, component_name: str, properties_json: str) -> list[str]:
    _, data = bpu.find_subobject(bp, component_name)
    template = unreal.SubobjectDataBlueprintFunctionLibrary.get_object_for_blueprint(data, bp)
    if template is None:
        raise ToolError(Code.UE_OPERATION_FAILED, f'No editable template for {component_name}', target=bp.get_path_name())
    props = parse_json_arg(properties_json, 'properties_json', dict)
    template.modify()
    applied = []
    for key, value in props.items():
        try:
            current = template.get_editor_property(key)
        except Exception as e:  # pylint: disable=broad-exception-caught
            raise ToolError(Code.INVALID_ARGUMENT, f'{template.get_class().get_name()} has no property {key!r}',
                            target=f'{bp.get_path_name()}:{component_name}',
                            likely_causes=['Use ObjectTools.list_properties on the component class for exact names.']) from e
        template.set_editor_property(key, from_jsonable(value, current, key))
        applied.append(key)
    bp.modify()
    return applied
