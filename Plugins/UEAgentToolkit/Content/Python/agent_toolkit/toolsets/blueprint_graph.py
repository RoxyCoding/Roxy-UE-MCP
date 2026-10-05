"""BlueprintGraphTools: bulk graph construction and graph discovery for Blueprints.

Fills the gaps against Epic's BlueprintTools (Graph DSL, find_node_types, find_nodes,
get_connected_subgraph, arrange_nodes, remove_variable, add_function_param, get/set_parent)
with locale-independent, JSON-declared equivalents. Nodes are created with the same
(node_kind, identifier) scheme as BlueprintAuthoringTools.add_blueprint_node.
"""

from __future__ import annotations

import json
from collections import deque

import unreal

from agent_toolkit.core import bp as bpu
from agent_toolkit.core import native, resolve
from agent_toolkit.core.errors import Code, ToolError
from agent_toolkit.core.serialize import parse_json_arg, split_csv
from agent_toolkit.core.tooling import agent_tool, ctx
from agent_toolkit.toolsets.blueprint_authoring import (BEL, _bp, _function_graph_editor, _graph_editor,
                                                        _require_var, create_node)

_EXEC_PIN_NAMES = ('execute', 'then')


def _is_exec(pin) -> bool:
    try:
        return str(pin.get_editor_property('pin_type').get_editor_property('pin_category')).lower() == 'exec'
    except Exception:  # pylint: disable=broad-exception-caught
        text = str(pin.get_pin_type_display_string()).lower()
        return 'exec' in text or str(pin.get_pin_name()).lower() in _EXEC_PIN_NAMES


def _is_input(pin) -> bool:
    return pin.get_pin_direction() == unreal.EdGraphPinDirection.EGPD_INPUT


def _resolve_ref(ref: str, ids: dict[str, str]) -> str:
    """'local.Pin' -> 'K2Node_X_0.Pin' using the local ids declared in the spec."""
    node, sep, pin = ref.strip().partition('.')
    return f'{ids.get(node, node)}{sep}{pin}'


def _links(graph: unreal.EdGraph) -> tuple[dict, dict]:
    """(nodes by name, edges as list of (src_node, dst_node, is_exec)) for a graph."""
    nodes = {n.get_name(): n for n in bpu.graph_nodes(graph)}
    edges = []
    for name, node in nodes.items():
        for pin in node.list_all_pins() or []:
            if _is_input(pin):
                continue
            for other in pin.list_connected_pins() or []:
                dst = other.get_owning_node().get_name()
                edges.append((name, dst, _is_exec(pin)))
    return nodes, edges


def _arrange(graph: unreal.EdGraph, names: list[str] | None, x_spacing: int, y_spacing: int) -> list[str]:
    """Layered left-to-right layout: column = longest path from a source node, rows stack per column."""
    nodes, edges = _links(graph)
    selected = [n for n in (names or list(nodes)) if n in nodes]
    chosen = set(selected)
    incoming: dict[str, list[str]] = {n: [] for n in selected}
    for src, dst, _ in edges:
        if src in chosen and dst in chosen and src != dst:
            incoming[dst].append(src)
    depth: dict[str, int] = {}

    def depth_of(name: str, stack: tuple = ()) -> int:
        if name in depth:
            return depth[name]
        if name in stack:  # cycle (loop macros): break it
            return 0
        d = 1 + max((depth_of(p, stack + (name,)) for p in incoming[name]), default=-1)
        depth[name] = d
        return d

    for name in selected:
        depth_of(name)
    positions = [nodes[n].get_node_pos() for n in selected]
    origin_x = min((p.x for p in positions), default=0)
    origin_y = min((p.y for p in positions), default=0)
    rows: dict[int, int] = {}
    for name in sorted(selected, key=lambda n: (depth[n], nodes[n].get_node_pos().y)):
        col = depth[name]
        row = rows.get(col, 0)
        rows[col] = row + 1
        bpu.set_node_pos(nodes[name], origin_x + col * x_spacing, origin_y + row * y_spacing)
    return selected


def _set_pin_value(pin, value) -> None:
    pin.set_pin_value(str(value).lower() if isinstance(value, bool) else str(value))


@unreal.uclass()
class BlueprintGraphTools(unreal.ToolsetDefinition):
    """Bulk Blueprint graph construction from a JSON declaration (build_blueprint_graph), node type
    discovery (find_blueprint_node_types / get_blueprint_node_type_pins), graph queries
    (find_blueprint_nodes / get_connected_subgraph), auto layout, and variable/function/parent
    editing gaps. Locale-independent counterpart of Epic's Graph DSL tools."""

    @agent_tool(mutates=True)
    def build_blueprint_graph(asset_path: str, graph_json: str, graph_name: str | None = None,
                              arrange: bool = True) -> dict:
        """Builds a whole graph section in one call: creates nodes, sets pin defaults and connects pins.
        All-or-nothing: if any step fails, the nodes created by this call are removed again.

        Args:
            asset_path: Blueprint asset path.
            graph_json: JSON object {"nodes": [...], "connections": [...]}. Each node:
                {"id": "begin", "kind": "event", "identifier": "ReceiveBeginPlay", "x": 0, "y": 0,
                 "defaults": {"InString": "Hello"}} where kind/identifier follow add_blueprint_node
                (function "KismetSystemLibrary:PrintString", event, custom_event, variable_get,
                variable_set, branch, macro, component_event, action). "id" is a local name used by
                connections. Connections: ["begin.then->print.execute"]; node ids of nodes that already
                exist in the graph (K2Node_...) can be used too. Existing events (e.g. BeginPlay) are reused.
            graph_name: Target graph (default EventGraph).
            arrange: Auto-layout the created nodes that have no explicit x/y.
        """
        bp = _bp(asset_path)
        graph, ed = _graph_editor(bp, graph_name)
        spec = parse_json_arg(graph_json, 'graph_json', dict)
        node_specs = spec.get('nodes') or []
        connections = spec.get('connections') or []
        if not isinstance(node_specs, list) or not isinstance(connections, list):
            raise ToolError(Code.INVALID_ARGUMENT, 'graph_json needs "nodes" (list) and "connections" (list of strings)')
        ids: dict[str, str] = {}
        created: list = []
        reused: list[str] = []
        unplaced: list[str] = []
        applied_defaults: dict[str, str] = {}
        made: list[str] = []
        try:
            for i, ns in enumerate(node_specs):
                if not isinstance(ns, dict) or 'kind' not in ns:
                    raise ToolError(Code.INVALID_ARGUMENT, f'nodes[{i}] must be an object with "kind" and "identifier"')
                local = str(ns.get('id') or f'n{i}')
                if local in ids:
                    raise ToolError(Code.INVALID_ARGUMENT, f'Duplicate node id {local!r}')
                node, is_new = create_node(bp, graph, ed, ns['kind'], ns.get('identifier'),
                                           int(ns.get('x', 0)), int(ns.get('y', 0)))
                ids[local] = node.get_name()
                if is_new:
                    created.append(node)
                    if 'x' not in ns and 'y' not in ns:
                        unplaced.append(node.get_name())
                else:
                    reused.append(local)
                for pin_name, value in (ns.get('defaults') or {}).items():
                    pin = bpu.find_pin(graph, f'{node.get_name()}.{pin_name}', 'in')
                    _set_pin_value(pin, value)
                    applied_defaults[f'{local}.{pin_name}'] = str(pin.get_pin_value())
            for conn in connections:
                if '->' not in str(conn):
                    raise ToolError(Code.INVALID_ARGUMENT, f'Connection must be "A.Pin->B.Pin", got {conn!r}')
                src_text, dst_text = [c.strip() for c in str(conn).split('->', 1)]
                src = bpu.find_pin(graph, _resolve_ref(src_text, ids), 'out')
                dst = bpu.find_pin(graph, _resolve_ref(dst_text, ids), 'in')
                if not src.try_create_connection(dst):
                    raise ToolError(Code.UE_OPERATION_FAILED, f'Connection {conn!r} was rejected by the schema',
                                    target=graph.get_path_name(),
                                    likely_causes=[f'{src.get_pin_type_display_string()} -> '
                                                   f'{dst.get_pin_type_display_string()}'])
                made.append(str(conn))
        except Exception:
            if created:
                try:
                    ed.remove_nodes(created)
                except Exception:  # pylint: disable=broad-exception-caught
                    ctx().warn('Rollback could not remove every created node', 'ROLLBACK_INCOMPLETE')
            raise
        if arrange and unplaced:
            _arrange(graph, unplaced, 360, 180)
        return {'nodes': ids, 'created': [n.get_name() for n in created], 'reused_existing': reused,
                'defaults': applied_defaults, 'connected': made}

    @agent_tool()
    def find_blueprint_node_categories(asset_path: str, parent: str | None = None, graph_name: str | None = None) -> dict:
        """Lists node menu categories available in a graph (localized to the editor language) with counts.

        Args:
            asset_path: Blueprint asset path.
            parent: Parent category path like "Math|Vector" to list its sub-categories (default top level).
            graph_name: Graph name (default EventGraph).
        """
        _, ed = _graph_editor(_bp(asset_path), graph_name)
        prefix = [p.strip() for p in parent.split('|')] if parent else []
        counts: dict[str, int] = {}
        for action in ed.list_available_nodes([]) or []:
            parts = [p.strip() for p in action.split('|')]
            if parts[:len(prefix)] != prefix or len(parts) <= len(prefix) + 1:
                continue
            key = parts[len(prefix)]
            counts[key] = counts.get(key, 0) + 1
        return {'parent': parent or '', 'categories': [{'name': k, 'nodes': v} for k, v in sorted(counts.items())]}

    @agent_tool()
    def find_blueprint_node_types(asset_path: str, query: str, category: str | None = None,
                                  graph_name: str | None = None, limit: int = 50) -> dict:
        """Finds creatable node types. Two result lists:
        - functions: language-independent matches by English function name / display name / keywords
          (spaces and case ignored) on this Blueprint's class and all function libraries. Use their "id"
          with add_blueprint_node / build_blueprint_graph as kind=function, identifier=id.
        - menu_actions: matches in the editor's right-click menu (titles are in the editor language),
          usable with kind=action. Confirm pins first with get_blueprint_node_type_pins.

        Args:
            asset_path: Blueprint asset path.
            query: e.g. "Print String", "SetActorLocation", "random float".
            category: Optional menu category prefix filter for menu_actions, e.g. "Math|Vector".
            graph_name: Graph name (default EventGraph).
            limit: Maximum results per list.
        """
        bp = _bp(asset_path)
        _, ed = _graph_editor(bp, graph_name)
        functions = []
        lib = native.library()
        if lib is not None:
            functions = json.loads(lib.find_callable_functions(bp.generated_class(), query, limit) or '[]')
        else:
            ctx().warn('UEAgentToolkitNative not loaded: only localized menu titles are searched', 'NATIVE_MISSING')
        q = query.lower()
        q_compact = q.replace(' ', '')
        prefix = category.lower().strip() if category else ''
        out = []
        for action in ed.list_available_nodes([]) or []:
            low = action.lower()
            if (q not in low and q_compact not in low.replace(' ', '')) or (prefix and not low.startswith(prefix)):
                continue
            parts = [p.strip() for p in action.split('|')]
            out.append({'action': action, 'category': '|'.join(parts[:-1]), 'title': parts[-1]})
        return {'total': len(functions) + len(out), 'functions': functions, 'menu_actions': out[:limit],
                'menu_total': len(out)}

    @agent_tool(mutates=True)
    def get_blueprint_node_type_pins(asset_path: str, node_kind: str, identifier: str | None = None,
                                     graph_name: str | None = None) -> dict:
        """Shows the pins a node would have, before you build with it. The node is created temporarily and
        removed again (an existing event node is only read).

        Args:
            asset_path: Blueprint asset path.
            node_kind: Same as add_blueprint_node (function, event, custom_event, variable_get, ...).
            identifier: Same as add_blueprint_node.
            graph_name: Graph name (default EventGraph).
        """
        bp = _bp(asset_path)
        graph, ed = _graph_editor(bp, graph_name)
        node, is_new = create_node(bp, graph, ed, node_kind, identifier, 0, 0)
        try:
            info = bpu.node_info(node, include_values=True)
        finally:
            if is_new:
                ed.remove_nodes([node])
        info.pop('id', None)
        info.pop('pos', None)
        return info

    @agent_tool()
    def find_blueprint_nodes(asset_path: str, graph_name: str | None = None, text: str | None = None,
                             class_name: str | None = None, only_errors: bool = False, limit: int = 100) -> dict:
        """Finds nodes in a graph by title/id text, node class and error state.

        Args:
            asset_path: Blueprint asset path.
            graph_name: Graph name (default EventGraph).
            text: Case-insensitive substring of node title or id, e.g. "PrintString" or "Event".
            class_name: Substring of the node class, e.g. "K2Node_CallFunction", "VariableSet".
            only_errors: Only nodes carrying an error/warning message.
            limit: Maximum results.
        """
        graph = bpu.find_graph(_bp(asset_path), graph_name or '')
        out = []
        for node in bpu.graph_nodes(graph):
            info = bpu.node_info(node, include_pins=False)
            if text and text.lower() not in f"{info['title']} {info['id']}".lower():
                continue
            if class_name and class_name.lower() not in info['class'].lower():
                continue
            if only_errors and not info.get('message'):
                continue
            out.append(info)
        return {'graph': graph.get_name(), 'total': len(out), 'nodes': out[:limit]}

    @agent_tool()
    def get_connected_subgraph(asset_path: str, start_node: str, graph_name: str | None = None,
                               direction: str = 'forward', exec_only: bool = False, max_nodes: int = 200) -> dict:
        """Returns the nodes reachable from a node (e.g. everything an event triggers) plus their links.

        Args:
            asset_path: Blueprint asset path.
            start_node: Node id (or unique title), e.g. "K2Node_Event_0".
            graph_name: Graph name (default EventGraph).
            direction: forward (outputs), backward (inputs) or both.
            exec_only: Follow execution pins only (skip data wires).
            max_nodes: Stop after this many nodes.
        """
        if direction not in ('forward', 'backward', 'both'):
            raise ToolError(Code.INVALID_ARGUMENT, 'direction must be forward, backward or both')
        graph = bpu.find_graph(_bp(asset_path), graph_name or '')
        start = bpu.find_node(graph, start_node)
        seen = {start.get_name(): start}
        queue = deque([start])
        edges: list[str] = []
        truncated = False
        while queue:
            node = queue.popleft()
            for pin in node.list_all_pins() or []:
                is_in = _is_input(pin)
                if (is_in and direction == 'forward') or (not is_in and direction == 'backward'):
                    continue
                if exec_only and not _is_exec(pin):
                    continue
                for other in pin.list_connected_pins() or []:
                    nxt = other.get_owning_node()
                    edge = f'{bpu.pin_ref(pin)}->{bpu.pin_ref(other)}' if not is_in else \
                        f'{bpu.pin_ref(other)}->{bpu.pin_ref(pin)}'
                    if edge not in edges:
                        edges.append(edge)
                    if nxt.get_name() not in seen:
                        if len(seen) >= max_nodes:
                            truncated = True
                            continue
                        seen[nxt.get_name()] = nxt
                        queue.append(nxt)
        return {'start': start.get_name(), 'truncated': truncated,
                'nodes': [bpu.node_info(n, include_pins=False) for n in seen.values()], 'connections': edges}

    @agent_tool(mutates=True)
    def arrange_blueprint_nodes(asset_path: str, graph_name: str | None = None, node_ids: str | None = None,
                                x_spacing: int = 360, y_spacing: int = 180) -> dict:
        """Auto-arranges nodes left to right by their distance from the graph's source nodes.

        Args:
            asset_path: Blueprint asset path.
            graph_name: Graph name (default EventGraph).
            node_ids: Comma-separated node ids to arrange (default: the whole graph).
            x_spacing: Horizontal distance between columns.
            y_spacing: Vertical distance between rows.
        """
        graph = bpu.find_graph(_bp(asset_path), graph_name or '')
        names = [bpu.find_node(graph, n).get_name() for n in split_csv(node_ids)] if node_ids else None
        arranged = _arrange(graph, names, x_spacing, y_spacing)
        return {'arranged': len(arranged), 'graph': graph.get_name()}

    @agent_tool(mutates=True)
    def remove_blueprint_variable(asset_path: str, variable_name: str) -> dict:
        """Removes a member variable. Variable get/set nodes that use it become errors, so check
        find_blueprint_nodes(text=variable_name) and compile afterwards.

        Args:
            asset_path: Blueprint asset path.
            variable_name: Variable name.
        """
        bp = _bp(asset_path)
        _require_var(bp, variable_name)
        graph = BEL.find_event_graph(bp) or bpu.graphs(bp)[0]
        bpu.graph_editor(graph).remove_member_variable(variable_name)
        still = variable_name in [str(n) for n in BEL.list_member_variable_names(bp, False) or []]
        if still:
            raise ToolError(Code.UE_OPERATION_FAILED, f'Could not remove variable {variable_name!r}', target=bp.get_path_name())
        return {'removed': variable_name}

    @agent_tool(mutates=True)
    def add_blueprint_function_params(asset_path: str, function_name: str, inputs_json: str | None = None,
                                      outputs_json: str | None = None) -> dict:
        """Adds inputs and/or outputs to an existing function graph.

        Args:
            asset_path: Blueprint asset path.
            function_name: Function graph name.
            inputs_json: JSON list, e.g. [{"name": "Damage", "type": "float"}].
            outputs_json: JSON list, e.g. [{"name": "IsDead", "type": "bool"}].
        """
        bp = _bp(asset_path)
        _, ed = _function_graph_editor(bp, function_name)
        inputs = parse_json_arg(inputs_json or '', 'inputs_json', list)
        outputs = parse_json_arg(outputs_json or '', 'outputs_json', list)
        if not inputs and not outputs:
            raise ToolError(Code.INVALID_ARGUMENT, 'Provide inputs_json and/or outputs_json.')
        for item in inputs:
            ed.add_graph_input_parameter(item['name'], bpu.parse_pin_type(item['type']), str(item.get('default', '')))
        for item in outputs:
            ed.add_graph_output_parameter(item['name'], bpu.parse_pin_type(item['type']))
        return {'function': function_name, 'inputs_added': [i['name'] for i in inputs],
                'outputs_added': [o['name'] for o in outputs]}

    @agent_tool()
    def get_blueprint_parent(asset_path: str) -> dict:
        """Returns the parent class of a Blueprint.

        Args:
            asset_path: Blueprint asset path.
        """
        bp = _bp(asset_path)
        parent = BEL.get_blueprint_parent_class(bp)
        return {'parent_class': parent.get_path_name() if parent else None,
                'parent_chain': resolve.class_parent_chain(parent) if parent else []}

    @agent_tool(mutates=True)
    def set_blueprint_parent(asset_path: str, parent_class: str) -> dict:
        """Reparents a Blueprint (e.g. Actor -> Character). Nodes and components that no longer exist in
        the new parent turn into errors, so compile and check afterwards.

        Args:
            asset_path: Blueprint asset path.
            parent_class: Native class name or Blueprint class path.
        """
        bp = _bp(asset_path)
        new_parent = resolve.resolve_class(parent_class)
        old = BEL.get_blueprint_parent_class(bp)
        if old == new_parent:
            return {'parent_class': new_parent.get_path_name(), 'changed': False}
        BEL.reparent_blueprint(bp, new_parent)
        BEL.compile_blueprint(bp)
        now = BEL.get_blueprint_parent_class(bp)
        if now != new_parent:
            raise ToolError(Code.WRONG_TYPE, f'{new_parent.get_name()} is not a valid parent for this Blueprint',
                            target=bp.get_path_name())
        return {'previous': old.get_path_name() if old else None, 'parent_class': new_parent.get_path_name(),
                'changed': True, 'status': bpu.status_name(bp)}

    @agent_tool(mutates=True)
    def remove_blueprint_function(asset_path: str, function_name: str) -> dict:
        """Removes a function graph, macro graph or event dispatcher. Call nodes that used it become
        errors, so check find_blueprint_nodes(text=function_name) and compile afterwards.

        Args:
            asset_path: Blueprint asset path.
            function_name: Function, macro or event dispatcher name.
        """
        bp = _bp(asset_path)
        dispatchers = [str(n) for n in BEL.list_event_dispatchers(bp) or []]
        if function_name in dispatchers:
            if not BEL.remove_event_dispatcher(bp, unreal.Name(function_name)):
                raise ToolError(Code.UE_OPERATION_FAILED, f'Could not remove dispatcher {function_name!r}',
                                target=bp.get_path_name())
            return {'removed': function_name, 'kind': 'event_dispatcher'}
        names = [g.get_name() for g in bpu.graphs(bp)]
        if function_name not in names or function_name in ('EventGraph', 'UserConstructionScript'):
            raise ToolError(Code.OBJECT_NOT_FOUND, f'Function/macro {function_name!r} not found', target=bp.get_path_name(),
                            likely_causes=[f'Graphs: {names}; dispatchers: {dispatchers}',
                                           'EventGraph and UserConstructionScript cannot be removed.'])
        BEL.remove_function_graph(bp, function_name)
        if function_name in [g.get_name() for g in bpu.graphs(bp)]:
            raise ToolError(Code.UE_OPERATION_FAILED, f'Could not remove graph {function_name!r}', target=bp.get_path_name())
        return {'removed': function_name, 'kind': 'graph'}

    @agent_tool()
    def list_component_events(asset_path: str, component_name: str) -> dict:
        """Lists the bindable events of a component (e.g. OnComponentBeginOverlap), for
        add_blueprint_node(node_kind="component_event", identifier="Component:Event").

        Args:
            asset_path: Blueprint asset path.
            component_name: Component variable name (or object name, e.g. CharacterMesh0).
        """
        bp = _bp(asset_path)
        _, data = bpu.find_subobject(bp, component_name)
        template = unreal.SubobjectDataBlueprintFunctionLibrary.get_object_for_blueprint(data, bp)
        if not isinstance(template, unreal.ActorComponent):
            raise ToolError(Code.WRONG_TYPE, f'{component_name!r} is not a component', target=bp.get_path_name())
        graph = BEL.find_event_graph(bp)
        if graph is None:
            raise ToolError(Code.NOT_SUPPORTED, 'This Blueprint has no event graph', target=bp.get_path_name())
        events = sorted(str(e) for e in bpu.graph_editor(graph).list_component_events(template) or [])
        return {'component': component_name, 'class': template.get_class().get_name(), 'events': events}

    # ------------------------------------------------- native-backed (UEAgentToolkitNative)
    @agent_tool(mutates=True)
    def add_blueprint_node_pin(asset_path: str, node_id: str, graph_name: str | None = None) -> dict:
        """Adds an input pin to a node with a variable pin count (Sequence, Make Array, Select, Switch...).
        Requires the native module.

        Args:
            asset_path: Blueprint asset path.
            node_id: Node id.
            graph_name: Graph name (default EventGraph).
        """
        bp = _bp(asset_path)
        lib = native.require('Adding node pins')
        node = bpu.find_node(bpu.find_graph(bp, graph_name or ''), node_id)
        pin = native.check(lib.add_node_pin(node), node_id)
        return {'added_pin': pin, 'node': bpu.node_info(node)}

    @agent_tool(mutates=True)
    def remove_blueprint_node_pin(asset_path: str, pin: str, graph_name: str | None = None) -> dict:
        """Removes a pin from a node with a variable pin count (e.g. "K2Node_ExecutionSequence_0.then_2").
        Requires the native module.

        Args:
            asset_path: Blueprint asset path.
            pin: "NodeId.PinName".
            graph_name: Graph name (default EventGraph).
        """
        bp = _bp(asset_path)
        lib = native.require('Removing node pins')
        graph = bpu.find_graph(bp, graph_name or '')
        p = bpu.find_pin(graph, pin)
        node = p.get_owning_node()
        native.check(lib.remove_node_pin(node, p.get_pin_name()), pin)
        return {'removed_pin': pin, 'node': bpu.node_info(node)}

    @agent_tool(mutates=True)
    def retarget_blueprint_node_class(asset_path: str, node_id: str, new_class: str,
                                      graph_name: str | None = None) -> dict:
        """Points a function-call node at the same function on another class (e.g. a base class
        to a subclass). Requires the native module.

        Args:
            asset_path: Blueprint asset path.
            node_id: Call Function node id.
            new_class: Class name or path that has a function with the same name.
            graph_name: Graph name (default EventGraph).
        """
        bp = _bp(asset_path)
        lib = native.require('Retargeting node classes')
        node = bpu.find_node(bpu.find_graph(bp, graph_name or ''), node_id)
        if node.get_class().get_name() != 'K2Node_CallFunction':
            raise ToolError(Code.WRONG_TYPE, f'{node_id} is a {node.get_class().get_name()}, not a function call node')
        native.check(lib.retarget_call_function_class(node, resolve.resolve_class(new_class)), node_id)
        return bpu.node_info(node)

    @agent_tool(mutates=True)
    def add_event_dispatcher(asset_path: str, dispatcher_name: str, inputs_json: str | None = None) -> dict:
        """Creates an Event Dispatcher with optional parameters. Requires the native module.

        Args:
            asset_path: Blueprint asset path.
            dispatcher_name: Dispatcher name.
            inputs_json: JSON list of parameters, e.g. [{"name": "Damage", "type": "float"}].
        """
        bp = _bp(asset_path)
        lib = native.require('Event dispatchers')
        inputs = parse_json_arg(inputs_json or '', 'inputs_json', list)
        native.check(lib.add_event_dispatcher(bp, dispatcher_name), bp.get_path_name())
        for item in inputs:
            native.check(lib.add_graph_param(bp, dispatcher_name, item['name'], bpu.parse_pin_type(item['type']), False),
                         bp.get_path_name())
        BEL.compile_blueprint(bp)
        return {'dispatcher': dispatcher_name, 'parameters': [i['name'] for i in inputs],
                'event_dispatchers': [str(d) for d in (BEL.list_event_dispatchers(bp) or [])]}

    @agent_tool(mutates=True)
    def add_blueprint_dispatcher_params(asset_path: str, dispatcher_name: str, inputs_json: str) -> dict:
        """Adds parameters to an existing Event Dispatcher signature. Requires the native module.

        Args:
            asset_path: Blueprint asset path.
            dispatcher_name: Dispatcher name.
            inputs_json: JSON list, e.g. [{"name": "Damage", "type": "float"}].
        """
        bp = _bp(asset_path)
        lib = native.require('Event dispatchers')
        inputs = parse_json_arg(inputs_json, 'inputs_json', list)
        for item in inputs:
            native.check(lib.add_graph_param(bp, dispatcher_name, item['name'], bpu.parse_pin_type(item['type']), False),
                         bp.get_path_name())
        return {'dispatcher': dispatcher_name, 'added': [i['name'] for i in inputs]}

    @agent_tool(mutates=True)
    def remove_blueprint_function_params(asset_path: str, function_name: str, names_json: str,
                                         output: bool = False) -> dict:
        """Removes parameters from a function graph or Event Dispatcher signature. Requires the native module.

        Args:
            asset_path: Blueprint asset path.
            function_name: Function graph or dispatcher name.
            names_json: JSON list of parameter names, e.g. ["Damage"].
            output: Remove outputs (Return node) instead of inputs.
        """
        bp = _bp(asset_path)
        lib = native.require('Removing function parameters')
        names = parse_json_arg(names_json, 'names_json', list)
        for name in names:
            native.check(lib.remove_graph_param(bp, function_name, str(name), output), bp.get_path_name())
        return {'function': function_name, 'removed': names, 'output': output}

    @agent_tool(mutates=True)
    def set_create_event_function(asset_path: str, node_id: str, function_name: str | None = None,
                                  graph_name: str | None = None) -> dict:
        """Gets or sets the function bound by a Create Event node. Requires the native module.

        Args:
            asset_path: Blueprint asset path.
            node_id: Create Event node id.
            function_name: Function or custom event name to assign; omit to only read the current one.
            graph_name: Graph name (default EventGraph).
        """
        bp = _bp(asset_path)
        lib = native.require('Create Event functions')
        node = bpu.find_node(bpu.find_graph(bp, graph_name or ''), node_id)
        if node.get_class().get_name() != 'K2Node_CreateDelegate':
            raise ToolError(Code.WRONG_TYPE, f'{node_id} is a {node.get_class().get_name()}, not a Create Event node')
        if function_name:
            native.check(lib.set_create_event_function(node, function_name), node_id)
        return {'node': node_id, 'function': str(lib.get_create_event_function(node))}

    @agent_tool()
    def list_compatible_event_functions(asset_path: str, node_id: str, graph_name: str | None = None) -> dict:
        """Lists functions/custom events whose parameters match the delegate of a Create Event node
        (candidates for set_create_event_function; the final check happens when assigning). Requires the
        native module. Connect the delegate pin of the Create Event node first.

        Args:
            asset_path: Blueprint asset path.
            node_id: Create Event node id.
            graph_name: Graph name (default EventGraph).
        """
        bp = _bp(asset_path)
        lib = native.require('Create Event candidates')
        node = bpu.find_node(bpu.find_graph(bp, graph_name or ''), node_id)
        if node.get_class().get_name() != 'K2Node_CreateDelegate':
            raise ToolError(Code.WRONG_TYPE, f'{node_id} is a {node.get_class().get_name()}, not a Create Event node')
        names = [str(n) for n in lib.list_compatible_event_functions(node) or []]
        current = lib.get_create_event_function(node)
        return {'node': node_id, 'current': '' if current.is_none() else str(current), 'candidates': names,
                'note': 'Empty usually means the delegate pin is not connected yet.' if not names else ''}
