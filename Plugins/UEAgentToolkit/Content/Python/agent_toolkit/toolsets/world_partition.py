"""WorldPartitionTools: Data Layers (create, assign actors, runtime/editor state, delete) and
per-actor streaming settings (spatial loading, runtime grid) for World Partition levels.

Uses DataLayerEditorSubsystem; Data Layer assets are created with DataLayerFactory.
"""

from __future__ import annotations

import unreal

from agent_toolkit.core import editor, resolve
from agent_toolkit.core.errors import Code, ToolError
from agent_toolkit.core.serialize import split_csv
from agent_toolkit.core.tooling import agent_tool, confirmation_required, ctx

_RUNTIME_STATES = {'unloaded': unreal.DataLayerRuntimeState.UNLOADED, 'loaded': unreal.DataLayerRuntimeState.LOADED,
                   'activated': unreal.DataLayerRuntimeState.ACTIVATED}
_TYPES = {'runtime': unreal.DataLayerType.RUNTIME, 'editor': unreal.DataLayerType.EDITOR}


def _dles() -> unreal.DataLayerEditorSubsystem:
    return unreal.get_editor_subsystem(unreal.DataLayerEditorSubsystem)


def _is_partitioned() -> bool:
    try:
        return unreal.WorldPartitionBlueprintLibrary.get_actor_descs() is not None
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _require_partitioned() -> None:
    if not _is_partitioned():
        raise ToolError(Code.EDITOR_STATE, 'The current level is not a World Partition level',
                        likely_causes=['Data Layers and streaming settings need World Partition. Create one with '
                                       'LevelTools.create_level(world_partition=true) or convert the level in the editor '
                                       '(Tools > Convert Level).'])


def _instances() -> list:
    return list(_dles().get_all_data_layers() or [])


def _layer_name(inst) -> str:
    asset = inst.get_asset()
    return asset.get_name() if asset else str(inst.get_data_layer_short_name())


def _find_layer(name: str):
    insts = _instances()
    for inst in insts:
        asset = inst.get_asset()
        if name in (_layer_name(inst), str(inst.get_data_layer_short_name()), str(inst.get_data_layer_full_name()),
                    asset.get_path_name() if asset else '', asset.get_outermost().get_name() if asset else ''):
            return inst
    raise ToolError(Code.OBJECT_NOT_FOUND, f'Data Layer {name!r} not found in the current level',
                    likely_causes=[f'Data Layers: {[_layer_name(i) for i in insts]}'])


def _describe(inst, with_actors: bool = True) -> dict:
    asset = inst.get_asset()
    info = {
        'name': _layer_name(inst),
        'asset': asset.get_outermost().get_name() if asset else None,
        'type': 'runtime' if inst.is_runtime() else 'editor',
        'initial_runtime_state': inst.get_initial_runtime_state().name.lower() if inst.is_runtime() else None,
        'visible_in_editor': bool(inst.is_visible()),
        'initially_visible': bool(inst.is_initially_visible()),
    }
    parent = inst.get_parent() if hasattr(inst, 'get_parent') else None
    if parent:
        info['parent'] = _layer_name(parent)
    if with_actors:
        actors = list(_dles().get_actors_from_data_layer(inst) or [])
        info['actor_count'] = len(actors)
        info['actors'] = [a.get_actor_label() for a in actors[:50]]
    return info


def _actors(names: str) -> list:
    wanted = split_csv(names)
    if not wanted:
        raise ToolError(Code.INVALID_ARGUMENT, 'actors must list at least one actor label or name')
    return [resolve.find_actor(n) for n in wanted]


@unreal.uclass()
class WorldPartitionTools(unreal.ToolsetDefinition):
    """World Partition: inspect the partition and its Data Layers, create Data Layers (runtime or
    editor), assign/remove actors, set initial runtime state / editor visibility / loading, delete
    layers, and set per-actor streaming (spatially loaded, runtime grid)."""

    @agent_tool()
    def inspect_world_partition(include_actors: bool = True) -> dict:
        """Returns whether the level uses World Partition, its Data Layers (type, initial runtime state,
        editor visibility, actors) and the number of non-spatially-loaded (always loaded) actors.

        Args:
            include_actors: Include up to 50 actor labels per Data Layer.
        """
        world = editor.editor_world()
        if not _is_partitioned():
            return {'level': world.get_outermost().get_name() if world else None, 'world_partition': False}
        always_loaded = [a.get_actor_label() for a in resolve.all_level_actors()
                         if not a.get_editor_property('is_spatially_loaded')]
        return {'level': world.get_outermost().get_name(), 'world_partition': True,
                'data_layers': [_describe(i, include_actors) for i in _instances()],
                'always_loaded_actor_count': len(always_loaded), 'always_loaded_actors': always_loaded[:50]}

    @agent_tool(mutates=True)
    def create_data_layer(name: str, folder: str = '/Game/DataLayers', layer_type: str = 'runtime',
                          initial_runtime_state: str = 'unloaded', parent: str | None = None) -> dict:
        """Creates a Data Layer asset and its instance in the current World Partition level.

        Args:
            name: Asset name, e.g. "DL_Dungeon".
            folder: Content folder for the Data Layer asset.
            layer_type: runtime (streams in game, controlled by state) or editor (editor-only organization).
            initial_runtime_state: unloaded, loaded or activated (runtime layers only).
            parent: Optional parent Data Layer name.
        """
        _require_partitioned()
        if layer_type not in _TYPES:
            raise ToolError(Code.INVALID_ARGUMENT, f'layer_type must be one of {sorted(_TYPES)}')
        if initial_runtime_state not in _RUNTIME_STATES:
            raise ToolError(Code.INVALID_ARGUMENT, f'initial_runtime_state must be one of {sorted(_RUNTIME_STATES)}')
        if any(_layer_name(i) == name for i in _instances()):
            raise ToolError(Code.ALREADY_EXISTS, f'Data Layer {name!r} already exists in this level')
        parent_inst = _find_layer(parent) if parent else None
        package = f'{resolve.normalize_asset_path(folder)[0].rstrip("/")}/{name}'
        asset = unreal.EditorAssetLibrary.load_asset(package) if unreal.EditorAssetLibrary.does_asset_exist(package) else None
        if asset is None:
            asset = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, package.rsplit('/', 1)[0],
                                                                            unreal.DataLayerAsset, unreal.DataLayerFactory())
            if asset is None:
                raise ToolError(Code.UE_OPERATION_FAILED, f'Could not create Data Layer asset {package}')
        elif not isinstance(asset, unreal.DataLayerAsset):
            raise ToolError(Code.WRONG_TYPE, f'{package} exists and is a {asset.get_class().get_name()}')
        asset.set_editor_property('data_layer_type', _TYPES[layer_type])
        params = unreal.DataLayerCreationParameters()
        params.set_editor_property('data_layer_asset', asset)
        inst = _dles().create_data_layer_instance(params)
        if inst is None:
            raise ToolError(Code.UE_OPERATION_FAILED, f'Could not create a Data Layer instance for {package}',
                            likely_causes=['The asset may already be used by this level.'])
        if layer_type == 'runtime':
            _dles().set_data_layer_initial_runtime_state(inst, _RUNTIME_STATES[initial_runtime_state])
        if parent_inst is not None and not _dles().set_parent_data_layer(inst, parent_inst):
            ctx().warn(f'Could not set parent {parent!r} (runtime/editor types must match)', 'PARENT_NOT_SET')
        return _describe(inst)

    @agent_tool(mutates=True)
    def assign_actors_to_data_layer(data_layer: str, actors: str, remove: bool = False) -> dict:
        """Adds level actors to a Data Layer (or removes them with remove=true).

        Args:
            data_layer: Data Layer name (asset name) or asset path.
            actors: Comma-separated actor labels or names.
            remove: Remove instead of add.
        """
        _require_partitioned()
        inst = _find_layer(data_layer)
        targets = _actors(actors)
        if remove:
            ok = _dles().remove_actors_from_data_layer(targets, inst)
        else:
            if not _dles().is_actor_valid_for_data_layer_instances(targets[0], [inst]):
                ctx().warn('Some actors may not be valid for this Data Layer (e.g. WorldSettings, landscape '
                           'proxies of another layer)', 'ACTOR_NOT_VALID_FOR_LAYER')
            ok = _dles().add_actors_to_data_layer(targets, inst)
        if not ok:
            ctx().warn('Data Layer membership did not change (already assigned / not assigned?)', 'NO_CHANGE')
        return {'data_layer': _layer_name(inst), 'removed' if remove else 'added': [a.get_actor_label() for a in targets],
                'actor_count': len(list(_dles().get_actors_from_data_layer(inst) or []))}

    @agent_tool(mutates=True)
    def set_data_layer_state(data_layer: str, initial_runtime_state: str | None = None,
                             visible_in_editor: bool | None = None, initially_visible: bool | None = None,
                             loaded_in_editor: bool | None = None) -> dict:
        """Changes Data Layer states. Only provided arguments change.

        Args:
            data_layer: Data Layer name or asset path.
            initial_runtime_state: unloaded, loaded or activated (runtime layers): state when the game starts.
            visible_in_editor: Show/hide its actors in the editor viewport.
            initially_visible: Editor visibility when the level is opened.
            loaded_in_editor: Load/unload its actors in the editor.
        """
        _require_partitioned()
        inst = _find_layer(data_layer)
        changed = {}
        if initial_runtime_state is not None:
            if initial_runtime_state not in _RUNTIME_STATES:
                raise ToolError(Code.INVALID_ARGUMENT, f'initial_runtime_state must be one of {sorted(_RUNTIME_STATES)}')
            if not inst.is_runtime():
                raise ToolError(Code.INVALID_ARGUMENT, f'{data_layer!r} is an editor Data Layer (no runtime state)')
            _dles().set_data_layer_initial_runtime_state(inst, _RUNTIME_STATES[initial_runtime_state])
            changed['initial_runtime_state'] = initial_runtime_state
        if visible_in_editor is not None:
            _dles().set_data_layer_visibility(inst, visible_in_editor)
            changed['visible_in_editor'] = visible_in_editor
        if initially_visible is not None:
            _dles().set_data_layer_is_initially_visible(inst, initially_visible)
            changed['initially_visible'] = initially_visible
        if loaded_in_editor is not None:
            _dles().set_data_layer_is_loaded_in_editor(inst, loaded_in_editor, True)
            changed['loaded_in_editor'] = loaded_in_editor
        if not changed:
            raise ToolError(Code.INVALID_ARGUMENT, 'No state argument was provided.')
        return {'changed': changed, 'data_layer': _describe(inst, False)}

    @agent_tool(mutates=True)
    def delete_data_layer(data_layer: str, confirm: bool = False) -> dict:
        """Deletes a Data Layer instance from the level (its actors stay, unassigned). The Data Layer asset
        is kept; delete it with AssetManagementTools.delete_assets if unused.

        Args:
            data_layer: Data Layer name or asset path.
            confirm: Must be true to delete; false returns a preview.
        """
        _require_partitioned()
        inst = _find_layer(data_layer)
        info = _describe(inst)
        if not confirm:
            raise confirmation_required(f'Delete Data Layer {info["name"]} ({info["actor_count"]} actors will be unassigned)',
                                  {'preview': info})
        _dles().delete_data_layer(inst)
        return {'deleted': info['name'], 'unassigned_actors': info['actors']}

    @agent_tool(mutates=True)
    def set_actor_streaming(actors: str, is_spatially_loaded: bool | None = None,
                            runtime_grid: str | None = None) -> dict:
        """Sets World Partition streaming of actors: spatially loaded (streams by distance) or always
        loaded, and the runtime grid name.

        Args:
            actors: Comma-separated actor labels or names.
            is_spatially_loaded: false = always loaded (e.g. game managers, skyboxes).
            runtime_grid: Runtime grid name from World Settings ("" for the default grid).
        """
        _require_partitioned()
        if is_spatially_loaded is None and runtime_grid is None:
            raise ToolError(Code.INVALID_ARGUMENT, 'Provide is_spatially_loaded and/or runtime_grid.')
        out = []
        for actor in _actors(actors):
            actor.modify()
            if is_spatially_loaded is not None:
                actor.set_editor_property('is_spatially_loaded', is_spatially_loaded)
            if runtime_grid is not None:
                actor.set_editor_property('runtime_grid', unreal.Name(runtime_grid))
            out.append({'actor': actor.get_actor_label(),
                        'is_spatially_loaded': bool(actor.get_editor_property('is_spatially_loaded')),
                        'runtime_grid': str(actor.get_editor_property('runtime_grid'))})
        return {'actors': out}
