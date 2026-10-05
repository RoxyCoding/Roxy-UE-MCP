"""NetworkingTools: replication inspection/configuration of Blueprints and PIE network modes.

Replicated variables and RPC custom events are created with BlueprintAuthoringTools
(add_blueprint_variable(replication=...), add_custom_event(replication=...)); this toolset
reports and adjusts them. Authority checks at runtime use the Actor:HasAuthority /
SwitchHasAuthority nodes.
"""

from __future__ import annotations

import json

import unreal

from agent_toolkit.core import bp as bpu
from agent_toolkit.core import native, resolve
from agent_toolkit.core.errors import Code, ToolError
from agent_toolkit.core.serialize import from_jsonable, to_jsonable
from agent_toolkit.core.tooling import agent_tool, ctx

_ACTOR_NET_PROPS = ('replicates', 'replicate_movement', 'net_dormancy', 'net_update_frequency',
                    'min_net_update_frequency', 'net_cull_distance_squared', 'always_relevant',
                    'only_relevant_to_owner', 'net_load_on_client', 'net_priority')
_NET_MODES = {'none': 0, 'server': 1, 'client': 2, 'multicast': 3}


def _get(obj, name):
    try:
        return obj.get_editor_property(name)
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def _actor_bp(asset_path: str) -> tuple[unreal.Blueprint, unreal.Actor]:
    bp = resolve.load_asset(asset_path, unreal.Blueprint)
    ctx().set_target(bp.get_outermost().get_name())
    unreal.BlueprintEditorLibrary.compile_blueprint(bp)
    cdo = unreal.get_default_object(bp.generated_class()) if bp.generated_class() else None
    if not isinstance(cdo, unreal.Actor):
        raise ToolError(Code.WRONG_TYPE, 'Replication settings apply to Actor Blueprints', target=bp.get_path_name())
    return bp, cdo


def _custom_events(bp: unreal.Blueprint):
    for graph in bpu.graphs(bp):
        for node in bpu.graph_nodes(graph):
            if isinstance(node, unreal.K2Node_CustomEvent):
                yield graph, node


@unreal.uclass()
class NetworkingTools(unreal.ToolsetDefinition):
    """Multiplayer support: replication summary of an Actor Blueprint (actor net settings,
    replicated variables with conditions, RPC events, replicating components), actor
    replication settings, RPC mode changes and Play-In-Editor network mode."""

    @agent_tool()
    def inspect_replication(asset_path: str) -> dict:
        """Returns the replication setup of an Actor Blueprint: actor net settings (replicates,
        movement, dormancy, frequencies, relevancy), replicated variables (mode + condition),
        custom events with RPC mode, and components that replicate.

        Args:
            asset_path: Actor Blueprint path.
        """
        bp, cdo = _actor_bp(asset_path)
        lib = native.library()
        variables = []
        for var in bpu.variable_infos(bp, include_defaults=False):
            mode = str(var.get('replication', 'NONE'))
            if 'NONE' in mode.upper() and len(mode) <= 6:
                continue
            entry = {'name': var['name'], 'type': var.get('type'), 'replication': mode}
            if lib is not None:
                cond = lib.get_variable_replication_condition(bp, var['name'])
                names = {getattr(unreal.LifetimeCondition, m).value: m for m in dir(unreal.LifetimeCondition)
                         if m.startswith('COND_')}
                entry['condition'] = names.get(cond, cond)
            variables.append(entry)
        events = []
        for graph, node in _custom_events(bp):
            item = {'name': str(node.get_node_title()).split('\n')[0], 'node': node.get_name(), 'graph': graph.get_name()}
            if lib is not None:
                item['rpc'] = str(lib.get_custom_event_replication(node))
            events.append(item)
        comps = []
        for c in bpu.component_tree(bp):
            try:
                template = bpu.component_template(bp, c['name'])
            except ToolError:
                continue
            if _get(template, 'replicates'):
                comps.append(c['name'])
        warnings = []
        if (variables or any(e.get('rpc', 'None') != 'None' for e in events)) and not _get(cdo, 'replicates'):
            warnings.append('Replicated variables/RPCs exist but the actor does not replicate (replicates=false).')
        for w in warnings:
            ctx().warn(w, 'REPLICATION_DISABLED')
        return {'actor_settings': {p: to_jsonable(_get(cdo, p)) for p in _ACTOR_NET_PROPS},
                'replicated_variables': variables, 'custom_events': events, 'replicating_components': comps}

    @agent_tool(mutates=True)
    def set_actor_replication(asset_path: str, replicates: bool | None = None, replicate_movement: bool | None = None,
                              net_dormancy: str | None = None, net_update_frequency: float | None = None,
                              always_relevant: bool | None = None, only_relevant_to_owner: bool | None = None,
                              net_cull_distance: float | None = None) -> dict:
        """Sets actor-level replication defaults of an Actor Blueprint. Only provided arguments change.

        Args:
            asset_path: Actor Blueprint path.
            replicates: Replicate this actor.
            replicate_movement: Replicate transform/velocity.
            net_dormancy: DORM_NEVER, DORM_AWAKE, DORM_DORMANT_ALL, DORM_DORMANT_PARTIAL, DORM_INITIAL.
            net_update_frequency: Max updates per second.
            always_relevant: Relevant to all connections.
            only_relevant_to_owner: Only relevant to the owning connection.
            net_cull_distance: Relevancy distance in cm (stored squared).
        """
        bp, cdo = _actor_bp(asset_path)
        changes = {'replicates': replicates, 'replicate_movement': replicate_movement,
                   'net_update_frequency': net_update_frequency, 'always_relevant': always_relevant,
                   'only_relevant_to_owner': only_relevant_to_owner}
        cdo.modify()
        applied = {}
        for prop, value in changes.items():
            if value is not None:
                cdo.set_editor_property(prop, value)
                applied[prop] = value
        if net_dormancy is not None:
            cdo.set_editor_property('net_dormancy', from_jsonable(net_dormancy, cdo.get_editor_property('net_dormancy'),
                                                                  'net_dormancy'))
            applied['net_dormancy'] = to_jsonable(cdo.get_editor_property('net_dormancy'))
        if net_cull_distance is not None:
            cdo.set_editor_property('net_cull_distance_squared', float(net_cull_distance) ** 2)
            applied['net_cull_distance_squared'] = float(net_cull_distance) ** 2
        if not applied:
            raise ToolError(Code.INVALID_ARGUMENT, 'No setting was provided.')
        bp.modify()
        return {'applied': applied}

    @agent_tool(mutates=True)
    def set_custom_event_replication(asset_path: str, event_name: str, replication: str, reliable: bool = False) -> dict:
        """Changes the RPC mode of an existing custom event (requires the native plugin).

        Args:
            asset_path: Blueprint path.
            event_name: Custom event name.
            replication: none, server, client or multicast.
            reliable: Reliable RPC.
        """
        bp = resolve.load_asset(asset_path, unreal.Blueprint)
        ctx().set_target(bp.get_outermost().get_name())
        lib = native.require('RPC configuration')
        mode = _NET_MODES.get(replication.lower())
        if mode is None:
            raise ToolError(Code.INVALID_ARGUMENT, f'replication must be one of {sorted(_NET_MODES)}')
        for _, node in _custom_events(bp):
            if str(node.get_node_title()).split('\n')[0] == event_name:
                lib.set_custom_event_replication(node, mode, reliable)
                unreal.BlueprintEditorLibrary.compile_blueprint(bp)
                return {'event': event_name, 'rpc': str(lib.get_custom_event_replication(node))}
        raise ToolError(Code.OBJECT_NOT_FOUND, f'Custom event {event_name!r} not found', target=bp.get_path_name(),
                        likely_causes=[f"Custom events: {[str(n.get_node_title()) for _, n in _custom_events(bp)]}"])

    @agent_tool(mutates=True, transaction=False)
    def set_pie_network_mode(net_mode: str = 'listen_server', number_of_players: int = 2,
                             run_under_one_process: bool = True) -> dict:
        """Configures Play-In-Editor for multiplayer testing (Editor Preferences > Level Editor > Play).

        Args:
            net_mode: standalone, listen_server or client (dedicated server + clients).
            number_of_players: Number of players/clients.
            run_under_one_process: Run all instances in the editor process.
        """
        modes = {'standalone': 'PIE_Standalone', 'listen_server': 'PIE_ListenServer', 'client': 'PIE_Client'}
        if net_mode.lower() not in modes:
            raise ToolError(Code.INVALID_ARGUMENT, f'net_mode must be one of {sorted(modes)}')
        if number_of_players < 1:
            raise ToolError(Code.INVALID_ARGUMENT, 'number_of_players must be >= 1')
        cls = unreal.load_class(None, '/Script/UnrealEd.LevelEditorPlaySettings')
        if cls is None:
            raise ToolError(Code.NOT_SUPPORTED, 'LevelEditorPlaySettings class not available')
        settings = unreal.get_default_object(cls)
        values = {'PlayNetMode': modes[net_mode.lower()], 'PlayNumberOfClients': int(number_of_players),
                  'RunUnderOneProcess': run_under_one_process}
        if not unreal.ToolsetLibrary.set_object_properties(settings, json.dumps(values)):
            raise ToolError(Code.UE_OPERATION_FAILED, 'Could not update Play settings')
        ctx().mark_modified(True)
        return json.loads(unreal.ToolsetLibrary.get_object_properties(settings, list(values)))
