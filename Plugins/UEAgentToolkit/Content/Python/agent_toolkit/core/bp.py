"""Blueprint introspection helpers shared by inspector, authoring, build and validation toolsets."""

from __future__ import annotations

import re
from typing import Any

import unreal

from . import native
from .errors import Code, ToolError
from .serialize import to_jsonable

BEL = unreal.BlueprintEditorLibrary

_STATUS_NAMES = {
    'BS_UNKNOWN': 'unknown', 'BS_DIRTY': 'dirty', 'BS_ERROR': 'error',
    'BS_UP_TO_DATE': 'up_to_date', 'BS_BEING_CREATED': 'being_created',
    'BS_UP_TO_DATE_WITH_WARNINGS': 'up_to_date_with_warnings',
}
_FIELD = re.compile(r'(\w+)=("[^"]*"|\([^()]*(?:\([^()]*\)[^()]*)*\)|[^,()]*)')


def status_name(bp: unreal.Blueprint) -> str:
    try:
        return _STATUS_NAMES.get(bp.get_editor_property('status').name, 'unknown')
    except Exception:  # pylint: disable=broad-exception-caught
        return 'unknown'


def pin_type_to_string(pin_type: unreal.EdGraphPinType | None) -> str:
    """Human/AI readable type, e.g. 'real', 'object<Actor>', 'array<struct<Vector>>'."""
    if pin_type is None:
        return 'unknown'
    text = pin_type.export_text()
    fields = {m.group(1): m.group(2).strip('"') for m in _FIELD.finditer(text)}
    cat = fields.get('PinCategory', '')
    sub = fields.get('PinSubCategory', '')
    obj = fields.get('PinSubCategoryObject', 'None')
    base = cat
    if cat == 'real':
        base = sub or 'double'
    if obj and obj != 'None':
        name = obj.rsplit('.', 1)[-1].strip("'\"")
        base = f'{cat}<{name}>'
    container = fields.get('ContainerType', 'None')
    if container == 'Array':
        return f'array<{base}>'
    if container == 'Set':
        return f'set<{base}>'
    if container == 'Map':
        return f'map<{base},...>'
    return base


_BASIC = {
    'bool': 'bool', 'boolean': 'bool', 'byte': 'byte', 'int': 'int', 'integer': 'int',
    'int64': 'int64', 'name': 'name', 'string': 'string', 'str': 'string', 'text': 'text',
}
_REAL = {'float', 'double', 'real'}
_STRUCTS = {
    'vector': 'Vector', 'vector2d': 'Vector2D', 'rotator': 'Rotator', 'transform': 'Transform',
    'linearcolor': 'LinearColor', 'color': 'LinearColor', 'quat': 'Quat', 'intpoint': 'IntPoint',
}


def parse_pin_type(type_name: str) -> unreal.EdGraphPinType:
    """Builds an EdGraphPinType from strings such as 'float', 'vector', 'object:Actor',
    'class:Pawn', 'struct:/Script/Engine.HitResult', 'array:float', 'array:object:Actor'.

    Note: BlueprintEditorLibrary.get_basic_type_by_name silently returns int for unknown
    names (including 'float'), so all names are mapped explicitly here.
    """
    if not type_name:
        raise ToolError(Code.INVALID_ARGUMENT, 'variable type is empty')
    t = type_name.strip()
    low = t.lower()
    if low.startswith('array:'):
        return BEL.get_array_type(parse_pin_type(t[6:]))
    if low.startswith('set:'):
        return BEL.get_set_type(parse_pin_type(t[4:]))
    if low in _BASIC:
        return BEL.get_basic_type_by_name(_BASIC[low])
    if low in _REAL:
        return BEL.get_basic_type_by_name('real')
    if low in _STRUCTS:
        return BEL.get_struct_type(getattr(unreal, _STRUCTS[low]).static_struct())
    kind, _, rest = t.partition(':')
    kind = kind.lower()
    if kind in ('object', 'class', 'softobject', 'softclass') and rest:
        # pylint: disable-next=import-outside-toplevel
        from .resolve import resolve_class
        cls = resolve_class(rest)
        if kind == 'object':
            return BEL.get_object_reference_type(cls)
        if kind == 'class':
            return BEL.get_class_reference_type(cls)
        getter = getattr(BEL, 'get_soft_object_reference_type' if kind == 'softobject'
                         else 'get_soft_class_reference_type', None)
        if getter is None:
            raise ToolError(Code.NOT_SUPPORTED, f'{kind} references are not supported by this engine Python API')
        return getter(cls)
    if kind == 'struct' and rest:
        struct = unreal.load_object(None, rest) if rest.startswith('/') else None
        if struct is None:
            py = getattr(unreal, rest, None)
            struct = py.static_struct() if py is not None and hasattr(py, 'static_struct') else None
        if struct is None:
            raise ToolError(Code.CLASS_NOT_FOUND, f'Struct not found: {rest}',
                            likely_causes=['Use a script path like /Script/Engine.HitResult or a Python name like HitResult'])
        return BEL.get_struct_type(struct)
    if kind == 'enum' and rest:
        enum = unreal.load_object(None, rest) if rest.startswith('/') else None
        py = getattr(unreal, rest, None) if enum is None else None
        if py is not None and hasattr(py, 'static_enum'):
            enum = py.static_enum()
        getter = getattr(BEL, 'get_enum_type', None)
        if enum is None or getter is None:
            raise ToolError(Code.NOT_SUPPORTED, f'Enum type {rest!r} could not be resolved')
        return getter(enum)
    raise ToolError(Code.INVALID_ARGUMENT, f'Unknown variable type {type_name!r}',
                    likely_causes=['Use bool, int, int64, float, name, string, text, byte, vector, rotator, '
                                   'transform, linearcolor, object:<Class>, class:<Class>, struct:<Path>, '
                                   'enum:<Path>, array:<type>, set:<type>'])


def set_node_pos(node: unreal.EdGraphNode, x: int, y: int) -> None:
    node.set_node_pos(unreal.IntPoint(int(x), int(y)))


def graphs(bp: unreal.Blueprint) -> list[unreal.EdGraph]:
    return list(BEL.list_graphs(bp) or [])


def find_graph(bp: unreal.Blueprint, graph_name: str) -> unreal.EdGraph:
    graph = BEL.find_graph(bp, graph_name) if graph_name else BEL.find_event_graph(bp)
    if graph is None:
        names = [g.get_name() for g in graphs(bp)]
        raise ToolError(Code.OBJECT_NOT_FOUND, f'Graph {graph_name!r} not found in {bp.get_path_name()}',
                        target=bp.get_path_name(), likely_causes=[f'Available graphs: {names}'])
    return graph


def graph_editor(graph: unreal.EdGraph) -> unreal.BlueprintGraphEditor:
    return unreal.BlueprintGraphEditor.get_graph_editor(graph)


def graph_nodes(graph: unreal.EdGraph) -> list[unreal.EdGraphNode]:
    try:
        return list(graph_editor(graph).list_all_nodes() or [])
    except Exception:  # pylint: disable=broad-exception-caught
        return []


def node_id(node: unreal.EdGraphNode) -> str:
    """Stable id of a node inside its graph (object name, e.g. K2Node_CallFunction_3)."""
    return node.get_name()


def pin_ref(pin: Any) -> str:
    return f'{node_id(pin.get_owning_node())}.{pin.get_pin_name()}'


def node_info(node: unreal.EdGraphNode, include_pins: bool = True, include_values: bool = True) -> dict:
    pos = node.get_node_pos()
    info: dict[str, Any] = {
        'id': node_id(node),
        'title': str(node.get_node_title()),
        'class': node.get_class().get_name(),
        'category': str(node.get_node_category()),
        'pos': [pos.x, pos.y] if pos is not None else None,
    }
    try:
        msg = node.get_editor_property('error_msg')
        if msg:
            info['message'] = str(msg)
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    if include_pins:
        pins = []
        for pin in node.list_all_pins() or []:
            entry: dict[str, Any] = {
                'name': str(pin.get_pin_name()),
                'dir': 'in' if pin.get_pin_direction() == unreal.EdGraphPinDirection.EGPD_INPUT else 'out',
                'type': str(pin.get_pin_type_display_string()),
            }
            links = [pin_ref(p) for p in (pin.list_connected_pins() or [])]
            if links:
                entry['links'] = links
            if include_values and entry['dir'] == 'in' and not links:
                value = pin.get_pin_value()
                if value not in (None, ''):
                    entry['value'] = str(value)
            pins.append(entry)
        info['pins'] = pins
    return info


def find_node(graph: unreal.EdGraph, node_identifier: str) -> unreal.EdGraphNode:
    nodes = graph_nodes(graph)
    for n in nodes:
        if n.get_name() == node_identifier or n.get_path_name() == node_identifier:
            return n
    titled = [n for n in nodes if str(n.get_node_title()) == node_identifier]
    if len(titled) == 1:
        return titled[0]
    raise ToolError(Code.OBJECT_NOT_FOUND, f'Node {node_identifier!r} not found in graph {graph.get_name()}',
                    target=graph.get_path_name(),
                    likely_causes=['Use node ids from inspect_blueprint_graph (e.g. K2Node_CallFunction_0).',
                                   f'Ambiguous title matches: {len(titled)}' if titled else
                                   f'Node ids: {[n.get_name() for n in nodes][:30]}'])


def variable_infos(bp: unreal.Blueprint, include_defaults: bool = True) -> list[dict]:
    cdo = None
    if include_defaults:
        try:
            gen = bp.generated_class()
            cdo = unreal.get_default_object(gen) if gen else None
        except Exception:  # pylint: disable=broad-exception-caught
            cdo = None
    result = []
    for name in BEL.list_member_variable_names(bp, False) or []:
        entry: dict[str, Any] = {'name': str(name)}
        try:
            entry['type'] = pin_type_to_string(BEL.get_member_variable_type(bp, name))
        except Exception:  # pylint: disable=broad-exception-caught
            entry['type'] = 'unknown'
        for key, getter in (('category', 'get_blueprint_variable_category'),
                            ('replication', 'get_blueprint_variable_replication')):
            try:
                entry[key] = to_jsonable(getattr(BEL, getter)(bp, name))
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        if cdo is not None:
            try:
                entry['default'] = to_jsonable(cdo.get_editor_property(str(name)))
            except Exception:  # pylint: disable=broad-exception-caught
                entry['default'] = '<not compiled>'
        result.append(entry)
    return result


def function_infos(bp: unreal.Blueprint, implemented_only: bool = True) -> list[dict]:
    out = []
    for fi in BEL.list_functions(bp) or []:
        if implemented_only and not fi.is_implemented:
            continue
        out.append({'name': str(fi.name), 'implemented': bool(fi.is_implemented)})
    return out


def event_infos(bp: unreal.Blueprint, implemented_only: bool = False) -> list[dict]:
    out = []
    for fi in BEL.list_events(bp) or []:
        if implemented_only and not fi.is_implemented:
            continue
        out.append({'name': str(fi.name), 'implemented': bool(fi.is_implemented)})
    return out


def component_tree(bp: unreal.Blueprint) -> list[dict]:
    """Flat list of components with parent links (SCS + inherited)."""
    sds = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    lib = unreal.SubobjectDataBlueprintFunctionLibrary
    out = []
    try:
        handles = sds.k2_gather_subobject_data_for_blueprint(bp) or []
    except Exception:  # pylint: disable=broad-exception-caught
        return out
    datas = []
    for h in handles:
        data = sds.k2_find_subobject_data_from_handle(h)
        if data is not None:
            datas.append((h, data))
    for h, data in datas:
        if lib.is_actor(data):
            continue
        obj = lib.get_object(data)
        parent_handle = lib.get_parent_handle(data)
        parent = None
        if lib.is_handle_valid(parent_handle):
            parent_data = sds.k2_find_subobject_data_from_handle(parent_handle)
            if parent_data is not None and not lib.is_actor(parent_data):
                parent = str(lib.get_variable_name(parent_data))
        entry = {
            'name': str(lib.get_variable_name(data)),
            'class': obj.get_class().get_name() if obj else None,
            'parent': parent if parent and not parent.startswith('None') else None,
            'inherited': bool(lib.is_inherited_component(data)),
            'native': bool(lib.is_native_component(data)),
            'root': bool(lib.is_root_component(data)),
        }
        if isinstance(obj, unreal.SceneComponent):
            entry['relative_location'] = to_jsonable(obj.get_editor_property('relative_location'))
        out.append(entry)
    return out


def find_subobject(bp: unreal.Blueprint, component_name: str) -> tuple[Any, Any]:
    """Returns (handle, data) for a component in the Blueprint's SCS."""
    sds = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    lib = unreal.SubobjectDataBlueprintFunctionLibrary
    names = []
    for h in sds.k2_gather_subobject_data_for_blueprint(bp) or []:
        data = sds.k2_find_subobject_data_from_handle(h)
        if data is None:
            continue
        name = str(lib.get_variable_name(data))
        names.append(name)
        if name == component_name or (lib.is_actor(data) and component_name in ('', 'root_actor')):
            return h, data
    raise ToolError(Code.OBJECT_NOT_FOUND, f'Component {component_name!r} not found in {bp.get_path_name()}',
                    target=bp.get_path_name(), likely_causes=[f'Components: {names}'])


def implemented_interfaces(bp: unreal.Blueprint) -> list[str]:
    lib = native.library()
    if lib is not None:
        try:
            return [str(c.get_path_name()) for c in lib.get_implemented_interfaces(bp) or []]
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    try:
        return [to_jsonable(i) for i in bp.get_editor_property('implemented_interfaces')]
    except Exception:  # pylint: disable=broad-exception-caught
        return []


def compile_report(bp: unreal.Blueprint, compile_first: bool = True) -> dict:
    """Compiles (optionally) and returns status plus per-node errors/warnings."""
    lib = native.library()
    compiler_log = None
    if compile_first:
        if lib is not None:
            compiler_log = [str(m) for m in lib.compile_blueprint_with_log(bp) or []]
        else:
            BEL.compile_blueprint(bp)
    errors, warnings = [], []
    for graph in graphs(bp):
        try:
            ed = graph_editor(graph)
        except Exception:  # pylint: disable=broad-exception-caught
            continue
        for bucket, getter in ((errors, 'list_nodes_with_errors'), (warnings, 'list_nodes_with_warnings')):
            try:
                for node in getattr(ed, getter)() or []:
                    bucket.append({
                        'graph': graph.get_name(), 'node': node_id(node),
                        'title': str(node.get_node_title()),
                        'message': str(node.get_editor_property('error_msg') or ''),
                    })
            except Exception:  # pylint: disable=broad-exception-caught
                pass
    report = {'asset': bp.get_outermost().get_name(), 'status': status_name(bp),
              'errors': errors, 'warnings': warnings}
    if compiler_log is not None:
        report['compiler_log'] = compiler_log
    if report['status'] == 'error' and not errors:
        report['hint'] = ('Status is error but no node carries a message; check the Output Log '
                          '(BuildDebugTools.get_log_errors) for LogBlueprint lines.')
    return report


_PIN_ALIASES = {'exec': 'execute', 'in': 'execute', 'out': 'then'}


def find_pin(graph: unreal.EdGraph, pin_ref_text: str, direction: str | None = None):
    """Resolves 'NodeId.PinName' (as printed by inspect_blueprint_graph) to a pin.

    direction: 'in' / 'out' to disambiguate pins that share a name.
    """
    if '.' not in pin_ref_text:
        raise ToolError(Code.INVALID_ARGUMENT, f'Pin reference must be "NodeId.PinName", got {pin_ref_text!r}')
    node_name, pin_name = pin_ref_text.split('.', 1)
    node = find_node(graph, node_name)
    wanted = _PIN_ALIASES.get(pin_name.lower(), pin_name)
    pins = list(node.list_all_pins() or [])
    if direction:
        want_dir = unreal.EdGraphPinDirection.EGPD_INPUT if direction == 'in' else unreal.EdGraphPinDirection.EGPD_OUTPUT
        pins = [p for p in pins if p.get_pin_direction() == want_dir] or pins
    for match in (lambda p: str(p.get_pin_name()) == wanted,
                  lambda p: str(p.get_pin_name()).lower() == wanted.lower(),
                  lambda p: str(p.get_pin_name()).replace(' ', '').lower() == wanted.replace(' ', '').lower()):
        found = [p for p in pins if match(p)]
        if found:
            return found[0]
    raise ToolError(Code.OBJECT_NOT_FOUND, f'Pin {pin_name!r} not found on node {node_name}',
                    target=pin_ref_text,
                    likely_causes=[f'Pins: {[str(p.get_pin_name()) for p in node.list_all_pins() or []]}'])


def add_component(bp: unreal.Blueprint, cls: unreal.Class, name: str, parent_name: str | None = None):
    """Adds a component to the Blueprint's SCS and returns its handle."""
    sds = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    if name in [c['name'] for c in component_tree(bp)]:
        raise ToolError(Code.ALREADY_EXISTS, f'Component {name!r} already exists', target=bp.get_path_name())
    handles = sds.k2_gather_subobject_data_for_blueprint(bp) or []
    if not handles:
        raise ToolError(Code.WRONG_TYPE, 'This Blueprint has no component tree (not an Actor Blueprint?)',
                        target=bp.get_path_name())
    parent_handle = find_subobject(bp, parent_name)[0] if parent_name else handles[0]
    params = unreal.AddNewSubobjectParams(parent_handle=parent_handle, new_class=cls, blueprint_context=bp)
    handle, fail_reason = sds.add_new_subobject(params)
    if fail_reason and str(fail_reason):
        raise ToolError(Code.UE_OPERATION_FAILED, f'Could not add component: {fail_reason}', target=bp.get_path_name())
    sds.rename_subobject(handle, unreal.Text(name))
    return handle


def component_template(bp: unreal.Blueprint, name: str) -> unreal.Object:
    _, data = find_subobject(bp, name)
    template = unreal.SubobjectDataBlueprintFunctionLibrary.get_object_for_blueprint(data, bp)
    if template is None:
        raise ToolError(Code.UE_OPERATION_FAILED, f'No editable template for {name}', target=bp.get_path_name())
    return template
