"""MetaSoundTools: build MetaSound Sources declaratively and edit existing MetaSounds node by node
(partial editing uses the UEAgentToolkitNative plugin, which exposes node ids)."""

from __future__ import annotations

import json

import unreal

from agent_toolkit.core import native, resolve
from agent_toolkit.core.errors import Code, ToolError
from agent_toolkit.core.serialize import parse_json_arg
from agent_toolkit.core.tooling import agent_tool, ctx


def _ms_literal(value, data_type: str):
    """MetasoundFrontendLiteral for a JSON value, guided by the pin data type."""
    mbs = unreal.get_engine_subsystem(unreal.MetaSoundBuilderSubsystem)
    dt = data_type.lower()
    if isinstance(value, bool) or dt == 'bool':
        lit = mbs.create_bool_meta_sound_literal(bool(value))
    elif isinstance(value, str) and value.startswith('/'):
        lit = mbs.create_object_meta_sound_literal(resolve.load_asset(value))
    elif dt in ('int32', 'int') or (isinstance(value, int) and dt not in ('float', 'time')):
        lit = mbs.create_int_meta_sound_literal(int(value))
    elif isinstance(value, (int, float)):
        lit = mbs.create_float_meta_sound_literal(float(value))
    else:
        lit = mbs.create_string_meta_sound_literal(str(value))
    return lit[0] if isinstance(lit, tuple) else lit  # some creators also return the data type


def _ok(result, what: str) -> None:
    if 'SUCCEEDED' not in str(result).upper():
        raise ToolError(Code.UE_OPERATION_FAILED, f'MetaSound builder failed: {what}',
                        likely_causes=['Check node class names ("UE.Wave Player.Mono"), pin names (see '
                                       'details pins) and that connected pins have the same data type.'])


def _pins(builder, node, outputs: bool) -> dict:
    handles = (builder.find_node_outputs if outputs else builder.find_node_inputs)(node)
    handles = handles[0] if isinstance(handles, tuple) else handles
    getter = builder.get_node_output_data if outputs else builder.get_node_input_data
    pins = {}
    for h in handles:
        data = getter(h)
        pins[str(data[0])] = (h, str(data[1]))
    return pins


@unreal.uclass()
class MetaSoundTools(unreal.ToolsetDefinition):
    """MetaSound authoring: declarative one-call construction of a MetaSound Source, full graph
    inspection with node ids, and partial editing of existing MetaSounds (add/remove nodes,
    connect/disconnect pins, input defaults, graph inputs)."""

    @agent_tool(mutates=True, transaction=False)
    def build_metasound_source(asset_path: str, graph_json: str, stereo: bool = False, one_shot: bool = True) -> dict:
        """Builds a MetaSound Source asset from a declarative graph in one call.

        graph_json example (looping wave with volume input):
        {"nodes": [{"id": "wave", "class": "UE.Wave Player.Mono",
                    "inputs": {"Wave Asset": "/Game/Audio/S_Loop", "Loop": true}},
                   {"id": "gain", "class": "UE.Multiply.Audio by Float"}],
         "graph_inputs": [{"name": "Volume", "type": "Float", "default": 0.8}],
         "connections": ["graph.On Play->wave.Play", "wave.Out Mono->gain.PrimaryOperand",
                         "graph.Volume->gain.AdditionalOperands", "gain.Out->graph.Out Mono",
                         "wave.On Finished->graph.On Finished"]}
        Endpoints: "graph.On Play" (trigger), graph inputs by name, "graph.Out Mono" /
        "graph.Out Left"/"graph.Out Right", "graph.On Finished" (one-shot only).

        Args:
            asset_path: New MetaSound Source path, e.g. /Game/Audio/MS_Ambience.
            graph_json: Graph description (see above).
            stereo: Stereo output (Out Left/Out Right) instead of mono (Out Mono).
            one_shot: One-shot source (has On Finished); false for continuously playing sounds.
        """
        package, _ = resolve.normalize_asset_path(asset_path)
        ctx().set_target(package)
        if resolve.asset_exists(package):
            raise ToolError(Code.ALREADY_EXISTS, f'{package} already exists', target=package)
        spec = parse_json_arg(graph_json, 'graph_json', dict)
        mbs = unreal.get_engine_subsystem(unreal.MetaSoundBuilderSubsystem)
        fmt = unreal.MetaSoundOutputAudioFormat.STEREO if stereo else unreal.MetaSoundOutputAudioFormat.MONO
        builder, on_play, on_finished, audio_outs, result = mbs.create_source_builder(
            unreal.Name(f'AgentToolkit_{package.rsplit("/", 1)[1]}'), fmt, one_shot)
        _ok(result, 'create source builder')
        out_names = ['Out Left', 'Out Right'] if stereo else ['Out Mono']
        endpoints_out = {'On Play': on_play}
        endpoints_in = {name: h for name, h in zip(out_names, audio_outs)}
        if one_shot:
            endpoints_in['On Finished'] = on_finished
        for gi in spec.get('graph_inputs', []):
            handle, r = builder.add_graph_input_node(gi['name'], gi.get('type', 'Float'),
                                                     _ms_literal(gi.get('default', 0), gi.get('type', 'Float')), False)
            _ok(r, f"graph input {gi['name']}")
            endpoints_out[gi['name']] = handle
        nodes, node_pins = {}, {}
        for n in spec.get('nodes', []):
            parts = n['class'].split('.')
            if len(parts) < 2:
                raise ToolError(Code.INVALID_ARGUMENT, f"class must be Namespace.Name[.Variant], got {n['class']!r}")
            cn = unreal.MetasoundFrontendClassName(namespace=parts[0], name=parts[1],
                                                   variant='.'.join(parts[2:]) if len(parts) > 2 else '')
            handle, r = builder.add_node_by_class_name(cn)
            _ok(r, f"add node {n['class']}")
            nodes[n['id']] = handle
            node_pins[n['id']] = (_pins(builder, handle, False), _pins(builder, handle, True))
            for pin, value in n.get('inputs', {}).items():
                ins = node_pins[n['id']][0]
                if pin not in ins:
                    raise ToolError(Code.OBJECT_NOT_FOUND, f"{n['id']} has no input {pin!r}",
                                    likely_causes=[f'Inputs: {sorted(ins)}'])
                _ok(builder.set_node_input_default(ins[pin][0], _ms_literal(value, ins[pin][1])), f"{n['id']}.{pin}")
        for conn in spec.get('connections', []):
            src, _, dst = conn.partition('->')
            (s_node, _, s_pin), (d_node, _, d_pin) = src.strip().partition('.'), dst.strip().partition('.')
            if s_node == 'graph':
                out_h = endpoints_out.get(s_pin)
            else:
                out_h = node_pins.get(s_node, ({}, {}))[1].get(s_pin, (None,))[0]
            if d_node == 'graph':
                in_h = endpoints_in.get(d_pin)
            else:
                in_h = node_pins.get(d_node, ({}, {}))[0].get(d_pin, (None,))[0]
            if out_h is None or in_h is None:
                raise ToolError(Code.OBJECT_NOT_FOUND, f'Unknown endpoint in {conn!r}',
                                likely_causes=[f'graph outputs: {sorted(endpoints_out)}; graph inputs: {sorted(endpoints_in)}'] +
                                [f'{k}: in={sorted(v[0])} out={sorted(v[1])}' for k, v in node_pins.items()])
            _ok(builder.connect_nodes(out_h, in_h), conn)
        folder, name = package.rsplit('/', 1)
        asset, r = unreal.get_editor_subsystem(unreal.MetaSoundEditorSubsystem).build_to_asset(
            builder, 'UE Agent Toolkit', name, folder, None)
        _ok(r, 'build to asset')
        return {'asset': package, 'nodes': {k: {'inputs': sorted(v[0]), 'outputs': sorted(v[1])} for k, v in node_pins.items()},
                'graph_inputs': [g['name'] for g in spec.get('graph_inputs', [])]}

    @agent_tool()
    def inspect_metasound(asset_path: str) -> dict:
        """Returns a MetaSound's graph: nodes (id, name, class, kind, inputs with type/default/link,
        outputs), edges and graph inputs/outputs. Node ids are used by the editing tools.

        Args:
            asset_path: MetaSound Source or Patch path.
        """
        asset = _metasound(asset_path)
        lib = _ms_lib(required=False)
        if lib is not None:
            graph = _graph(asset)
            graph['aliases'] = {f'@{_ALIAS_BY_NAME[n]}': n for n in graph['graph_inputs'] + graph['graph_outputs']
                                if n in _ALIAS_BY_NAME}
            return graph
        builder, r = unreal.get_editor_subsystem(unreal.MetaSoundEditorSubsystem).find_or_begin_building(asset)
        _ok(r, 'open builder')
        names = builder.get_graph_input_names()
        outputs = builder.get_graph_output_names()
        names = names[0] if isinstance(names, tuple) else names
        outputs = outputs[0] if isinstance(outputs, tuple) else outputs
        ctx().warn('UEAgentToolkitNative not loaded: only graph inputs/outputs are available.', 'PARTIAL_INSPECTION')
        return {'graph_inputs': sorted(str(n) for n in names), 'graph_outputs': sorted(str(o) for o in outputs)}

    # ------------------------------------------------------------ partial editing
    @agent_tool(mutates=True, transaction=False)
    def add_metasound_node(asset_path: str, node_class: str, major_version: int = 1, x: int = 0, y: int = 0,
                           input_defaults_json: str | None = None) -> dict:
        """Adds a node to an existing MetaSound and returns its id and pins.

        Args:
            asset_path: MetaSound Source or Patch path.
            node_class: "Namespace.Name[.Variant]", e.g. "UE.Wave Player.Mono", "UE.Multiply.Audio by Float",
                "UE.ADSR Envelope.Audio", "UE.Trigger Delay". Unknown names fail with NOT_FOUND.
            major_version: Node class major version.
            x: Editor X position.
            y: Editor Y position.
            input_defaults_json: Optional {"Pin": value} defaults for the new node.
        """
        asset = _metasound(asset_path)
        lib = _ms_lib()
        node_id = native.check(lib.add_node(asset, node_class, major_version, unreal.Vector2D(x, y)), asset.get_path_name())
        for pin, value in parse_json_arg(input_defaults_json or '', 'input_defaults_json', dict).items():
            native.check(lib.set_input_default(asset, node_id, pin, _text(value)), asset.get_path_name(), Code.INVALID_ARGUMENT)
        node = next(n for n in _graph(asset)['nodes'] if n['id'] == node_id)
        return node

    @agent_tool(mutates=True, transaction=False)
    def remove_metasound_nodes(asset_path: str, nodes: list[str]) -> dict:
        """Removes nodes (and their connections) from a MetaSound.

        Args:
            asset_path: MetaSound Source or Patch path.
            nodes: Node ids or unique node names.
        """
        asset = _metasound(asset_path)
        lib = _ms_lib()
        graph = _graph(asset)
        removed = []
        for ref in nodes:
            node = _find_node(graph, ref)
            if node['kind'] in ('graph_input', 'graph_output'):
                raise ToolError(Code.INVALID_ARGUMENT, f'{ref} is a graph input/output node; use remove_metasound_graph_input')
            native.check(lib.remove_node(asset, node['id']), asset.get_path_name())
            removed.append(node['id'])
        return {'removed': removed}

    @agent_tool(mutates=True, transaction=False)
    def connect_metasound_pins(asset_path: str, connections: list[str]) -> dict:
        """Connects pins: "FromNode.Output->ToNode.Input". Nodes are ids or unique names; "@Name"
        addresses a graph input/output node (aliases: @On Play, @On Finished, @Out Mono, @Out Left,
        @Out Right; exposed inputs by their name), e.g.
        "@Volume->gain.AdditionalOperands", "gain.Out->@Out Mono", "@On Play->wave.Play".

        Args:
            asset_path: MetaSound Source or Patch path.
            connections: Connection strings.
        """
        asset = _metasound(asset_path)
        lib = _ms_lib()
        done = []
        for conn in connections:
            if '->' not in conn:
                raise ToolError(Code.INVALID_ARGUMENT, f'Connection must be "A.Out->B.In", got {conn!r}')
            graph = _graph(asset)
            src, dst = [c.strip() for c in conn.split('->', 1)]
            s_node, s_pin = _endpoint(graph, src, 'outputs')
            d_node, d_pin = _endpoint(graph, dst, 'inputs')
            native.check(lib.connect(asset, s_node['id'], s_pin, d_node['id'], d_pin), asset.get_path_name())
            done.append(conn)
        return {'connected': done}

    @agent_tool(mutates=True, transaction=False)
    def disconnect_metasound_pin(asset_path: str, input_pin: str) -> dict:
        """Removes the connection feeding an input pin ("Node.Input" or "@Out Mono").

        Args:
            asset_path: MetaSound Source or Patch path.
            input_pin: Destination pin.
        """
        asset = _metasound(asset_path)
        lib = _ms_lib()
        node, pin = _endpoint(_graph(asset), input_pin, 'inputs')
        native.check(lib.disconnect(asset, node['id'], pin), asset.get_path_name(), Code.INVALID_ARGUMENT)
        return {'disconnected': input_pin}

    @agent_tool(mutates=True, transaction=False)
    def set_metasound_input_defaults(asset_path: str, values_json: str) -> dict:
        """Sets node input defaults, e.g. {"wave.Wave Asset": "/Game/Audio/S_Rain", "wave.Loop": true,
        "<node id>.Pitch Shift": 2.0}. Use null to clear a default.

        Args:
            asset_path: MetaSound Source or Patch path.
            values_json: JSON object "Node.Input" -> value.
        """
        asset = _metasound(asset_path)
        lib = _ms_lib()
        graph = _graph(asset)
        applied = {}
        for ref, value in parse_json_arg(values_json, 'values_json', dict).items():
            node, pin = _endpoint(graph, ref, 'inputs')
            native.check(lib.set_input_default(asset, node['id'], pin, '' if value is None else _text(value)),
                         asset.get_path_name(), Code.INVALID_ARGUMENT)
            applied[ref] = value
        return {'values': applied}

    @agent_tool(mutates=True, transaction=False)
    def add_metasound_graph_input(asset_path: str, name: str, data_type: str = 'Float',
                                  default_value: str | None = None) -> dict:
        """Adds an exposed graph input (parameter settable from Blueprints / Audio Component).

        Args:
            asset_path: MetaSound Source or Patch path.
            name: Input name, e.g. "Volume".
            data_type: Float, Int32, Bool, Trigger, Time, String, Audio, WaveAsset, ...
            default_value: Default as text (number, true/false, or asset path).
        """
        asset = _metasound(asset_path)
        lib = _ms_lib()
        node_id = native.check(lib.add_graph_input(asset, name, data_type, default_value or ''), asset.get_path_name())
        return {'input': name, 'node': node_id, 'data_type': data_type}

    @agent_tool(mutates=True, transaction=False)
    def remove_metasound_graph_input(asset_path: str, name: str) -> dict:
        """Removes an exposed graph input (its connections are removed too).

        Args:
            asset_path: MetaSound Source or Patch path.
            name: Input name.
        """
        asset = _metasound(asset_path)
        lib = _ms_lib()
        if name not in _graph(asset)['graph_inputs']:
            raise ToolError(Code.OBJECT_NOT_FOUND, f'Graph input {name!r} not found', target=asset.get_path_name())
        native.check(lib.remove_graph_input(asset, name), asset.get_path_name())
        return {'removed': name}


def _metasound(path: str):
    asset = resolve.load_asset(path)
    if asset.get_class().get_name() not in ('MetaSoundSource', 'MetaSoundPatch') and \
            not asset.get_class().get_name().startswith('MetaSound'):
        raise ToolError(Code.WRONG_TYPE, f'{asset.get_path_name()} is a {asset.get_class().get_name()}, not a MetaSound',
                        target=asset.get_path_name())
    ctx().set_target(asset.get_outermost().get_name())
    return asset


def _ms_lib(required: bool = True):
    lib = getattr(unreal, 'AgentToolkitMetaSoundLibrary', None)
    if lib is None and required:
        native.require('MetaSound partial editing')
    return lib


def _graph(asset) -> dict:
    return json.loads(native.check(_ms_lib().describe_graph(asset), asset.get_path_name()))


def _text(value) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


def _find_node(graph: dict, ref: str) -> dict:
    for n in graph['nodes']:
        if n['id'] == ref or n['id'].lower() == ref.replace('-', '').lower():
            return n
    named = [n for n in graph['nodes'] if n['name'] == ref]
    if len(named) == 1:
        return named[0]
    raise ToolError(Code.OBJECT_NOT_FOUND if not named else Code.AMBIGUOUS, f'Node {ref!r} not found' if not named
                    else f'{len(named)} nodes are named {ref!r}; use the node id',
                    likely_causes=[f"Nodes: {[(n['id'][:8], n['name']) for n in graph['nodes']]}"])


_INTERFACE_ALIASES = {
    'on play': 'UE.Source.OnPlay', 'on finished': 'UE.Source.OneShot.OnFinished',
    'out mono': 'UE.OutputFormat.Mono.Audio:0', 'out left': 'UE.OutputFormat.Stereo.Audio:0',
    'out right': 'UE.OutputFormat.Stereo.Audio:1',
}
_ALIAS_BY_NAME = {v: k.title() for k, v in _INTERFACE_ALIASES.items()}


def _endpoint(graph: dict, text: str, side: str) -> tuple[dict, str]:
    """Resolves "Node.Pin" or "@GraphVertex" (friendly aliases such as "@Out Mono") to (node, pin)."""
    if text.startswith('@'):
        kind = 'graph_input' if side == 'outputs' else 'graph_output'

        def lookup(vertex: str) -> list:
            vertex = _INTERFACE_ALIASES.get(vertex.lower(), vertex)
            return [n for n in graph['nodes'] if n['kind'] == kind and n['name'] == vertex]

        # Vertex names may contain dots (UE.Source.OnPlay), so try the whole text before "Name.Pin".
        raw, pin = text[1:], ''
        nodes = lookup(raw)
        if not nodes and '.' in raw:
            head, _, tail = raw.rpartition('.')
            if lookup(head):
                nodes, pin = lookup(head), tail
        name = raw
        if not nodes:
            raise ToolError(Code.OBJECT_NOT_FOUND, f'No {kind.replace("_", " ")} named {name!r}',
                            likely_causes=[f"graph inputs: {graph['graph_inputs']}; outputs: {graph['graph_outputs']}"])
        node = nodes[0]
        pins = [p['name'] for p in node[side]]
        return node, pin or (name if name in pins else pins[0])
    if '.' not in text:
        raise ToolError(Code.INVALID_ARGUMENT, f'Endpoint must be "Node.Pin" or "@Name", got {text!r}')
    node_ref, pin = text.split('.', 1)
    node = _find_node(graph, node_ref)
    if pin not in [p['name'] for p in node[side]]:
        raise ToolError(Code.OBJECT_NOT_FOUND, f'{node["name"]} has no {side[:-1]} {pin!r}',
                        likely_causes=[f"{side}: {[p['name'] for p in node[side]]}"])
    return node, pin
