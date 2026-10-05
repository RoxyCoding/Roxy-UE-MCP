"""LevelTools: level lifecycle, actor placement helpers, attachment, World Settings,
GameMode/maps configuration and level streaming.

Complements Epic's SceneTools (load_level, find_actors, add_to_scene_from_class/asset,
remove_from_scene, folders, level instances) and ActorTools (transform, tags, components).
"""

from __future__ import annotations

from collections import defaultdict

import unreal

from agent_toolkit.core import config, editor, resolve
from agent_toolkit.core.errors import Code, ToolError
from agent_toolkit.core.serialize import from_jsonable, parse_json_arg, split_csv, to_jsonable
from agent_toolkit.core.tooling import agent_tool, confirmation_required, ctx

_TEMPLATES = {
    'basic': '/Engine/Maps/Templates/Template_Default',
    'open_world': '/Engine/Maps/Templates/OpenWorld',
}
_LIGHTS = {
    'point': unreal.PointLight, 'spot': unreal.SpotLight, 'directional': unreal.DirectionalLight,
    'rect': unreal.RectLight, 'sky': unreal.SkyLight,
}
_VOLUMES = {
    'trigger_box': unreal.TriggerBox, 'trigger_sphere': unreal.TriggerSphere,
    'trigger_capsule': unreal.TriggerCapsule, 'trigger_volume': unreal.TriggerVolume,
    'blocking': unreal.BlockingVolume, 'post_process': unreal.PostProcessVolume,
    'nav_mesh_bounds': unreal.NavMeshBoundsVolume, 'kill_z': unreal.KillZVolume,
    'audio': unreal.AudioVolume, 'physics': unreal.PhysicsVolume, 'pain_causing': unreal.PainCausingVolume,
    'cull_distance': unreal.CullDistanceVolume, 'lightmass_importance': unreal.LightmassImportanceVolume,
}


def _vec(values: list[float] | None, default: tuple[float, float, float]) -> unreal.Vector:
    v = list(values) if values else list(default)
    if len(v) != 3:
        raise ToolError(Code.INVALID_ARGUMENT, f'Expected [x, y, z], got {values}')
    return unreal.Vector(*[float(x) for x in v])


def _rot(values: list[float] | None) -> unreal.Rotator:
    v = list(values) if values else [0.0, 0.0, 0.0]
    if len(v) != 3:
        raise ToolError(Code.INVALID_ARGUMENT, f'Expected rotation [pitch, yaw, roll], got {values}')
    return unreal.Rotator(roll=float(v[2]), pitch=float(v[0]), yaw=float(v[1]))


def _apply_properties(obj: unreal.Object, properties: str | dict | None) -> list[str]:
    props = properties if isinstance(properties, dict) else parse_json_arg(properties or '', 'properties_json', dict)
    applied = []
    for key, value in props.items():
        try:
            current = obj.get_editor_property(key)
        except Exception as e:  # pylint: disable=broad-exception-caught
            raise ToolError(Code.INVALID_ARGUMENT, f'{obj.get_class().get_name()} has no property {key!r}',
                            target=obj.get_path_name(),
                            likely_causes=['Use ObjectTools.list_properties to get exact property names.']) from e
        obj.set_editor_property(key, from_jsonable(value, current, key))
        applied.append(key)
    return applied


def _actor_summary(a: unreal.Actor) -> dict:
    return {'label': a.get_actor_label(), 'name': a.get_name(), 'class': a.get_class().get_name(),
            'location': to_jsonable(a.get_actor_location()), 'path': a.get_path_name()}


def _spawn(cls_or_asset: str, location, rotation, label: str | None, folder: str | None) -> unreal.Actor:
    eas = editor.actor_subsystem()
    actor = None
    asset = None
    if cls_or_asset.startswith('/') and not cls_or_asset.startswith('/Script/'):
        asset = resolve.load_asset(cls_or_asset)
    if asset is not None and not isinstance(asset, unreal.Blueprint):
        actor = eas.spawn_actor_from_object(asset, location, rotation)
        if actor is None:
            actor = _spawn_for_asset_fallback(asset, location, rotation)
    else:
        cls = resolve.resolve_class(cls_or_asset, unreal.Actor)
        actor = eas.spawn_actor_from_class(cls, location, rotation)
    if actor is None:
        raise ToolError(Code.UE_OPERATION_FAILED, f'Failed to spawn {cls_or_asset}',
                        likely_causes=['Abstract classes cannot be spawned.', 'No editor world loaded.'])
    if label:
        actor.set_actor_label(label)
    if folder:
        actor.set_folder_path(folder)
    return actor


def _spawn_for_asset_fallback(asset: unreal.Object, location, rotation) -> unreal.Actor | None:
    """Spawns the standard actor for common asset types when no actor factory is available
    (e.g. headless/commandlet sessions without the Level Editor UI)."""
    eas = editor.actor_subsystem()
    table = ((unreal.StaticMesh, unreal.StaticMeshActor, unreal.StaticMeshComponent, 'static_mesh'),
             (unreal.SkeletalMesh, unreal.SkeletalMeshActor, unreal.SkeletalMeshComponent, 'skeletal_mesh_asset'),
             (unreal.NiagaraSystem, unreal.NiagaraActor, unreal.NiagaraComponent, 'asset'),
             (unreal.SoundBase, unreal.AmbientSound, unreal.AudioComponent, 'sound'))
    for asset_type, actor_cls, comp_cls, prop in table:
        if isinstance(asset, asset_type):
            actor = eas.spawn_actor_from_class(actor_cls, location, rotation)
            comp = actor.get_component_by_class(comp_cls) if actor else None
            if comp is not None:
                comp.set_editor_property(prop, asset)
            return actor
    return None


def _require_world() -> unreal.World:
    world = editor.editor_world()
    if world is None:
        raise ToolError(Code.EDITOR_STATE, 'No editor world is loaded.')
    return world


@unreal.uclass()
class LevelTools(unreal.ToolsetDefinition):
    """Level creation/saving, actor spawning (generic + light/camera/volume/PlayerStart helpers),
    duplication/deletion, attachment, World Settings, GameMode and default maps, level
    streaming and folder organisation. All actor edits are undoable."""

    @agent_tool(mutates=True, transaction=False)
    def create_level(asset_path: str, template: str = 'empty', world_partition: bool = False,
                     confirm_discard_unsaved: bool = False) -> dict:
        """Creates a new level asset and opens it. The current level is closed without saving,
        so unsaved level changes require confirm_discard_unsaved=True.

        Args:
            asset_path: New level path, e.g. /Game/Maps/L_Arena.
            template: "empty", "basic" (sky, light, floor), "open_world", or a level asset path to copy.
            world_partition: Create a World Partition level (only for template="empty").
            confirm_discard_unsaved: Required when the current level has unsaved changes.
        """
        package, _ = resolve.normalize_asset_path(asset_path)
        ctx().set_target(package)
        if resolve.asset_exists(package):
            raise ToolError(Code.ALREADY_EXISTS, f'{package} already exists', target=package)
        dirty_maps = [p.get_name() for p in unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages() or []]
        if dirty_maps and not confirm_discard_unsaved:
            raise confirmation_required('The open level has unsaved changes that would be discarded '
                                        '(save it first, or pass confirm_discard_unsaved=true).',
                                        {'dirty_levels': dirty_maps}, package)
        les = editor.level_subsystem()
        if template == 'empty':
            ok = les.new_level(package, world_partition)
        else:
            tpl = _TEMPLATES.get(template, template)
            if not resolve.asset_exists(tpl):
                raise ToolError(Code.ASSET_NOT_FOUND, f'Template level not found: {tpl}',
                                likely_causes=['Use empty, basic, open_world or an existing level path.'])
            ok = les.new_level_from_template(package, tpl)
        if not ok:
            raise ToolError(Code.UE_OPERATION_FAILED, f'Level creation failed: {package}', target=package)
        return {'level': package, 'template': template}

    @agent_tool(mutates=True, transaction=False)
    def save_current_level(save_as_path: str | None = None) -> dict:
        """Saves the currently open level (and its external actors). Untitled levels need save_as_path.

        Args:
            save_as_path: Optional new level path, e.g. /Game/Maps/L_Test (required for untitled levels).
        """
        world = _require_world()
        current = world.get_outermost().get_name()
        if save_as_path:
            package, _ = resolve.normalize_asset_path(save_as_path)
            ok = unreal.EditorLoadingAndSavingUtils.save_map(world, package)
            target = package
        else:
            if current.startswith('/Temp/'):
                raise ToolError(Code.INVALID_ARGUMENT, 'The level is untitled; pass save_as_path.', target=current)
            ok = unreal.EditorLoadingAndSavingUtils.save_current_level()
            target = current
        ctx().set_target(target)
        if not ok:
            raise ToolError(Code.UE_OPERATION_FAILED, f'Saving level failed: {target}', target=target,
                            likely_causes=['File is read-only or locked by source control.'])
        ctx().mark_modified(True)
        return {'saved': target}

    @agent_tool(mutates=True)
    def spawn_actor(class_or_asset: str, location: list[float], rotation: list[float],
                    scale: list[float], label: str | None = None, folder: str | None = None,
                    properties_json: str | None = None) -> dict:
        """Spawns an actor from a class (e.g. "PointLight", "/Game/BP/BP_Enemy") or from an
        asset (StaticMesh -> StaticMeshActor, SkeletalMesh, NiagaraSystem, Sound...) and
        optionally sets label, folder and properties in one call.

        Args:
            class_or_asset: Actor class name / script path / Blueprint path, or a placeable asset path.
            location: [x, y, z] in cm.
            rotation: [pitch, yaw, roll] in degrees.
            scale: [x, y, z] (use [1,1,1] for default).
            label: Outliner label.
            folder: Outliner folder, e.g. "Gameplay/Enemies".
            properties_json: JSON object of actor properties, e.g. {"can_be_damaged": false}.
        """
        actor = _spawn(class_or_asset, _vec(location, (0, 0, 0)), _rot(rotation), label, folder)
        actor.set_actor_scale3d(_vec(scale, (1, 1, 1)))
        applied = _apply_properties(actor, properties_json)
        ctx().set_target(actor.get_path_name())
        return {**_actor_summary(actor), 'properties_set': applied}

    @agent_tool(mutates=True)
    def add_light(light_type: str, location: list[float], rotation: list[float], intensity: float = -1.0,
                  color: str | None = None, label: str | None = None, folder: str | None = None) -> dict:
        """Adds a light actor.

        Args:
            light_type: point, spot, directional, rect or sky.
            location: [x, y, z].
            rotation: [pitch, yaw, roll] (directional/spot lights point along +X).
            intensity: Light intensity in the light's units (-1 keeps the default).
            color: Optional "r,g,b" in 0..1, e.g. "1,0.8,0.6".
            label: Outliner label.
            folder: Outliner folder.
        """
        cls = _LIGHTS.get(light_type.lower())
        if cls is None:
            raise ToolError(Code.INVALID_ARGUMENT, f'Unknown light_type {light_type!r}',
                            likely_causes=[f'Valid: {sorted(_LIGHTS)}'])
        actor = _spawn(cls.static_class().get_path_name(), _vec(location, (0, 0, 0)), _rot(rotation), label, folder)
        comp = actor.get_component_by_class(unreal.LightComponentBase)
        if comp is not None:
            if intensity >= 0:
                comp.set_editor_property('intensity', float(intensity))
            if color:
                rgb = [float(c) for c in split_csv(color)][:3]
                if len(rgb) != 3:
                    raise ToolError(Code.INVALID_ARGUMENT, f'color must be "r,g,b", got {color!r}')
                comp.set_editor_property('light_color', unreal.Color(*[int(max(0.0, min(1.0, c)) * 255) for c in rgb], 255))
        ctx().set_target(actor.get_path_name())
        return _actor_summary(actor)

    @agent_tool(mutates=True)
    def add_camera(location: list[float], rotation: list[float], field_of_view: float = 90.0,
                   cine_camera: bool = False, label: str | None = None, folder: str | None = None) -> dict:
        """Adds a camera actor (CameraActor or CineCameraActor).

        Args:
            location: [x, y, z].
            rotation: [pitch, yaw, roll].
            field_of_view: Horizontal FOV in degrees (CameraActor only).
            cine_camera: Spawn a CineCameraActor (for Sequencer) instead of a CameraActor.
            label: Outliner label.
            folder: Outliner folder.
        """
        cls = unreal.CineCameraActor if cine_camera else unreal.CameraActor
        actor = _spawn(cls.static_class().get_path_name(), _vec(location, (0, 0, 0)), _rot(rotation), label, folder)
        if not cine_camera:
            actor.get_component_by_class(unreal.CameraComponent).set_editor_property('field_of_view', float(field_of_view))
        ctx().set_target(actor.get_path_name())
        return _actor_summary(actor)

    @agent_tool(mutates=True)
    def add_volume(volume_type: str, location: list[float], extent: list[float], label: str | None = None,
                   folder: str | None = None, properties_json: str | None = None) -> dict:
        """Adds a volume or trigger sized by its half-extent.

        Args:
            volume_type: trigger_box, trigger_sphere, trigger_capsule, trigger_volume, blocking,
                post_process, nav_mesh_bounds, kill_z, audio, physics, pain_causing,
                cull_distance or lightmass_importance.
            location: Center [x, y, z].
            extent: Half size [x, y, z] in cm (spheres use x as radius).
            label: Outliner label.
            folder: Outliner folder.
            properties_json: Extra properties, e.g. {"unbound": true} for post_process.
        """
        cls = _VOLUMES.get(volume_type.lower())
        if cls is None:
            raise ToolError(Code.INVALID_ARGUMENT, f'Unknown volume_type {volume_type!r}',
                            likely_causes=[f'Valid: {sorted(_VOLUMES)}'])
        ext = _vec(extent, (100, 100, 100))
        actor = _spawn(cls.static_class().get_path_name(), _vec(location, (0, 0, 0)), unreal.Rotator(), label, folder)
        if isinstance(actor, unreal.TriggerBase):
            shape = actor.get_component_by_class(unreal.ShapeComponent)
            if isinstance(shape, unreal.BoxComponent):
                shape.set_box_extent(ext)
            elif isinstance(shape, unreal.SphereComponent):
                shape.set_sphere_radius(ext.x)
            elif isinstance(shape, unreal.CapsuleComponent):
                shape.set_capsule_size(ext.x, ext.z)
        else:
            # Brush volumes spawn with a 200cm cube brush (half extent 100): scale to fit.
            actor.set_actor_scale3d(unreal.Vector(ext.x / 100.0, ext.y / 100.0, ext.z / 100.0))
        applied = _apply_properties(actor, properties_json)
        ctx().set_target(actor.get_path_name())
        if cls is unreal.NavMeshBoundsVolume:
            ctx().warn('Navigation rebuilds automatically in the editor; verify with AI debug or "show navigation".',
                       'NAV_REBUILD')
        return {**_actor_summary(actor), 'extent': to_jsonable(ext), 'properties_set': applied}

    @agent_tool(mutates=True)
    def add_player_start(location: list[float], yaw: float = 0.0, player_start_tag: str | None = None,
                         label: str | None = None) -> dict:
        """Adds a PlayerStart (player spawn point).

        Args:
            location: [x, y, z]; keep ~100cm above the floor so the capsule fits.
            yaw: Facing direction in degrees.
            player_start_tag: Optional PlayerStartTag used by GameMode logic.
            label: Outliner label.
        """
        actor = _spawn('/Script/Engine.PlayerStart', _vec(location, (0, 0, 100)),
                       unreal.Rotator(roll=0, pitch=0, yaw=float(yaw)), label, None)
        if player_start_tag:
            actor.set_editor_property('player_start_tag', unreal.Name(player_start_tag))
        ctx().set_target(actor.get_path_name())
        return _actor_summary(actor)

    @agent_tool(mutates=True)
    def duplicate_actors(actors: list[str], offset: list[float]) -> dict:
        """Duplicates actors with a location offset.

        Args:
            actors: Actor labels/names/paths.
            offset: [x, y, z] offset applied to the copies.
        """
        sources = resolve.find_actors(actors)
        copies = editor.actor_subsystem().duplicate_actors(sources, None, _vec(offset, (0, 0, 0))) or []
        if not copies:
            raise ToolError(Code.UE_OPERATION_FAILED, 'Duplication produced no actors')
        return {'copies': [_actor_summary(c) for c in copies]}

    @agent_tool(mutates=True)
    def delete_actors(actors: list[str], confirm: bool = False) -> dict:
        """Deletes level actors (undoable with SafetyTools.undo). Without confirm=True only
        previews which actors (and attached children) would be removed.

        Args:
            actors: Actor labels/names/paths.
            confirm: Perform the deletion.
        """
        targets = resolve.find_actors(actors)
        preview = [{**_actor_summary(a), 'attached_children': [c.get_actor_label() for c in a.get_attached_actors()]}
                   for a in targets]
        if not confirm:
            raise confirmation_required(f'{len(targets)} actors would be deleted.', {'actors': preview})
        labels = [a.get_actor_label() for a in targets]
        if not editor.actor_subsystem().destroy_actors(targets):
            raise ToolError(Code.UE_OPERATION_FAILED, 'Some actors could not be deleted', likely_causes=[
                'Actors inside a non-editable level instance or locked sublevel cannot be deleted.'])
        return {'deleted': labels}

    @agent_tool(mutates=True)
    def attach_actor(child: str, parent: str, socket_name: str | None = None,
                     keep_world_transform: bool = True) -> dict:
        """Attaches one actor to another (optionally to a socket/bone on the parent's root).

        Args:
            child: Actor to attach.
            parent: Actor to attach to.
            socket_name: Optional socket or bone name.
            keep_world_transform: Keep the child's world transform (otherwise snap to parent).
        """
        c, p = resolve.find_actor(child), resolve.find_actor(parent)
        if c == p:
            raise ToolError(Code.INVALID_ARGUMENT, 'An actor cannot be attached to itself')
        rule = unreal.AttachmentRule.KEEP_WORLD if keep_world_transform else unreal.AttachmentRule.SNAP_TO_TARGET
        c.attach_to_actor(p, unreal.Name(socket_name or 'None'), rule, rule, rule, False)
        if c.get_attach_parent_actor() != p:
            raise ToolError(Code.UE_OPERATION_FAILED, 'Attachment failed', target=c.get_path_name(),
                            likely_causes=['Mobility mismatch: a Movable child cannot attach to a Static parent '
                                           'root (or vice versa). Set both root components to Movable.'])
        ctx().set_target(c.get_path_name())
        return {'child': c.get_actor_label(), 'parent': p.get_actor_label(), 'socket': socket_name}

    @agent_tool(mutates=True)
    def detach_actor(actor: str) -> dict:
        """Detaches an actor from its parent, keeping its world transform.

        Args:
            actor: Actor label/name/path.
        """
        a = resolve.find_actor(actor)
        parent = a.get_attach_parent_actor()
        if parent is None:
            ctx().mark_modified(False)
            return {'actor': a.get_actor_label(), 'was_attached': False}
        rule = unreal.DetachmentRule.KEEP_WORLD
        a.detach_from_actor(rule, rule, rule)
        ctx().set_target(a.get_path_name())
        return {'actor': a.get_actor_label(), 'previous_parent': parent.get_actor_label()}

    @agent_tool(mutates=True)
    def set_world_settings(settings_json: str) -> dict:
        """Sets World Settings properties of the current level, e.g.
        {"kill_z": -5000, "global_gravity_set": true, "global_gravity_z": -980,
        "default_game_mode": "/Game/Core/BP_GameMode"}.

        Args:
            settings_json: JSON object of WorldSettings property names to values.
        """
        ws = _require_world().get_world_settings()
        ctx().set_target(ws.get_path_name())
        settings = parse_json_arg(settings_json, 'settings_json', dict)
        applied = []
        if isinstance(settings.get('default_game_mode'), str):
            ws.set_editor_property('default_game_mode', resolve.resolve_class(settings.pop('default_game_mode'),
                                                                               unreal.GameModeBase))
            applied.append('default_game_mode')
        applied += _apply_properties(ws, settings)
        return {'applied': applied}

    @agent_tool(mutates=True)
    def set_level_game_mode_override(game_mode_class: str | None = None) -> dict:
        """Sets (or clears) the GameMode override of the current level (World Settings).

        Args:
            game_mode_class: GameMode class or Blueprint path; omit to clear the override.
        """
        ws = _require_world().get_world_settings()
        cls = resolve.resolve_class(game_mode_class, unreal.GameModeBase) if game_mode_class else None
        ws.set_editor_property('default_game_mode', cls)
        ctx().set_target(ws.get_path_name())
        return {'game_mode_override': cls.get_path_name() if cls else None}

    @agent_tool(mutates=True, transaction=False)
    def set_project_maps_and_modes(global_default_game_mode: str | None = None,
                                   game_default_map: str | None = None,
                                   editor_startup_map: str | None = None,
                                   game_instance_class: str | None = None,
                                   dry_run: bool = False) -> dict:
        """Sets project-wide Maps & Modes (Config/DefaultEngine.ini, backed up first) and applies
        them to the running editor. Only the arguments you pass are changed.

        Args:
            global_default_game_mode: GameMode class or Blueprint path.
            game_default_map: Level path used when the game starts.
            editor_startup_map: Level opened when the editor starts.
            game_instance_class: GameInstance class or Blueprint path.
            dry_run: Show the ini changes without writing.
        """
        gms = unreal.GameMapsSettings.get_game_maps_settings()
        values: dict[str, str] = {}
        apply: list[tuple[str, object]] = []
        if global_default_game_mode:
            cls = resolve.resolve_class(global_default_game_mode, unreal.GameModeBase)
            values['GlobalDefaultGameMode'] = cls.get_path_name()
            apply.append(('global_default_game_mode', unreal.SoftClassPath(cls.get_path_name())))
        if game_instance_class:
            cls = resolve.resolve_class(game_instance_class, unreal.GameInstance)
            values['GameInstanceClass'] = cls.get_path_name()
            apply.append(('game_instance_class', unreal.SoftClassPath(cls.get_path_name())))
        for key, prop, value in (('GameDefaultMap', 'game_default_map', game_default_map),
                                 ('EditorStartupMap', 'editor_startup_map', editor_startup_map)):
            if value:
                package, object_path = resolve.normalize_asset_path(value)
                if not resolve.asset_exists(package):
                    raise ToolError(Code.ASSET_NOT_FOUND, f'Level not found: {package}', target=package)
                values[key] = object_path
                apply.append((prop, unreal.SoftObjectPath(object_path)))
        if not values:
            raise ToolError(Code.INVALID_ARGUMENT, 'Pass at least one setting to change.')
        change = config.set_values('DefaultEngine', '/Script/EngineSettings.GameMapsSettings', values, dry_run)
        if dry_run:
            ctx().mark_modified(False)
            return {'dry_run': True, 'change': change}
        for prop, value in apply:
            try:
                gms.set_editor_property(prop, value)
            except Exception as e:  # pylint: disable=broad-exception-caught
                ctx().warn(f'Written to ini but not applied live ({prop}): {e}; restart the editor to apply.',
                           'NOT_APPLIED_LIVE')
        ctx().set_target(change['file'])
        ctx().mark_modified(True)
        return {'change': change}

    @agent_tool(mutates=True)
    def add_streaming_level(level_path: str, always_loaded: bool = False, location: str | None = None) -> dict:
        """Adds an existing level as a streaming sublevel of the current persistent level.

        Args:
            level_path: Level asset to add.
            always_loaded: Use AlwaysLoaded streaming instead of Blueprint/dynamic streaming.
            location: Optional level offset "x,y,z".
        """
        world = _require_world()
        package, _ = resolve.normalize_asset_path(level_path)
        if not resolve.asset_exists(package):
            raise ToolError(Code.ASSET_NOT_FOUND, f'Level not found: {package}', target=package)
        cls = unreal.LevelStreamingAlwaysLoaded if always_loaded else unreal.LevelStreamingDynamic
        if location:
            streaming = unreal.EditorLevelUtils.add_level_to_world_with_transform(
                world, package, cls, unreal.Transform(location=_vec([float(v) for v in split_csv(location)], (0, 0, 0))))
        else:
            streaming = unreal.EditorLevelUtils.add_level_to_world(world, package, cls)
        if streaming is None:
            raise ToolError(Code.UE_OPERATION_FAILED, f'Could not add {package} (already added or World Partition?)',
                            target=package, likely_causes=['World Partition levels do not use level streaming; '
                                                           'use Level Instances or Data Layers instead.'])
        ctx().set_target(package)
        return {'added': package, 'streaming_class': cls.__name__}

    @agent_tool(mutates=True)
    def remove_streaming_level(level_path: str) -> dict:
        """Removes a streaming sublevel from the current persistent level (the asset is kept).

        Args:
            level_path: Sublevel package path.
        """
        world = _require_world()
        package = resolve.package_of(level_path)
        for lvl in unreal.EditorLevelUtils.get_levels(world) or []:
            if lvl.get_outermost().get_name() == package:
                if not unreal.EditorLevelUtils.remove_level_from_world(lvl):
                    raise ToolError(Code.UE_OPERATION_FAILED, f'Could not remove {package}', target=package)
                ctx().set_target(package)
                return {'removed': package}
        raise ToolError(Code.OBJECT_NOT_FOUND, f'{package} is not a sublevel of the current level', target=package)

    @agent_tool(mutates=True)
    def set_streaming_level_visibility(level_path: str, visible: bool) -> dict:
        """Shows or hides a sublevel in the editor.

        Args:
            level_path: Sublevel package path.
            visible: Visibility.
        """
        world = _require_world()
        package = resolve.package_of(level_path)
        for lvl in unreal.EditorLevelUtils.get_levels(world) or []:
            if lvl.get_outermost().get_name() == package:
                unreal.EditorLevelUtils.set_level_visibility(lvl, visible, True)
                ctx().set_target(package)
                return {'level': package, 'visible': visible}
        raise ToolError(Code.OBJECT_NOT_FOUND, f'{package} is not loaded in the current world', target=package)

    @agent_tool(mutates=True)
    def organize_actors_into_folders(group_by: str = 'class', root_folder: str | None = None, only_unfiled: bool = True,
                                     dry_run: bool = False) -> dict:
        """Organises Outliner folders automatically.

        Args:
            group_by: "class" (folder per actor class) or "type" (Lights, Cameras, Volumes,
                Meshes, Gameplay, Other).
            root_folder: Parent folder for the generated folders (empty = top level).
            only_unfiled: Only move actors that are not already in a folder.
            dry_run: Return the plan without moving actors.
        """
        def type_of(a: unreal.Actor) -> str:
            for base, name in ((unreal.Light, 'Lights'), (unreal.CameraActor, 'Cameras'), (unreal.Volume, 'Volumes'),
                               (unreal.TriggerBase, 'Triggers'), (unreal.StaticMeshActor, 'Meshes'),
                               (unreal.PlayerStart, 'Gameplay'), (unreal.Pawn, 'Gameplay')):
                if isinstance(a, base):
                    return name
            return 'Other'

        plan: dict[str, list[str]] = defaultdict(list)
        moves = []
        for a in resolve.all_level_actors():
            if isinstance(a, (unreal.WorldSettings, unreal.Brush)) and not isinstance(a, unreal.Volume):
                continue
            if only_unfiled and str(a.get_folder_path()) not in ('', 'None'):
                continue
            group = a.get_class().get_name() if group_by == 'class' else type_of(a)
            folder = f'{root_folder.strip("/")}/{group}' if root_folder else group
            plan[folder].append(a.get_actor_label())
            moves.append((a, folder))
        if dry_run:
            ctx().mark_modified(False)
            return {'dry_run': True, 'plan': dict(plan)}
        for a, folder in moves:
            a.set_folder_path(folder)
        return {'moved': len(moves), 'plan': dict(plan)}
