"""AnimationTools: Animation Blueprints (state machines, states, transitions), montages
(sections, notifies), blend spaces and animation inspection.

State machine / montage section / blend space editing uses the UEAgentToolkitNative plugin
(no Python API exists). Complements Epic's ControlRigTools, Sequencer toolsets and
SkeletalMeshTools (sockets, bones, physics assets).
"""

from __future__ import annotations

import json

import unreal

from agent_toolkit.core import editor, native, resolve
from agent_toolkit.core.errors import Code, ToolError
from agent_toolkit.core.serialize import parse_json_arg, to_jsonable
from agent_toolkit.core.tooling import agent_tool, ctx

ALIB = unreal.AnimationLibrary


def _anim_bp(asset_path: str) -> unreal.AnimBlueprint:
    bp = resolve.load_asset(asset_path, unreal.AnimBlueprint)
    ctx().set_target(bp.get_outermost().get_name())
    return bp


def _new_asset_path(asset_path: str) -> tuple[str, str, str]:
    package, _ = resolve.normalize_asset_path(asset_path)
    if resolve.asset_exists(package):
        raise ToolError(Code.ALREADY_EXISTS, f'{package} already exists', target=package)
    folder, name = package.rsplit('/', 1)
    ctx().set_target(package)
    return package, folder, name


def _skeleton(path: str) -> unreal.Skeleton:
    asset = resolve.load_asset(path, (unreal.Skeleton, unreal.SkeletalMesh))
    return asset if isinstance(asset, unreal.Skeleton) else asset.get_editor_property('skeleton')


def _describe(bp: unreal.AnimBlueprint) -> dict:
    lib = native.require_graph('State machine inspection')
    return json.loads(native.check(lib.anim_describe_state_machines(bp), bp.get_path_name()))


def _rig_chains(rig) -> tuple[list[str], str]:
    """(retarget chain names, retarget root) of an IK Rig. Empty chains = not retargetable."""
    ctrl = unreal.IKRigController.get_controller(rig)
    chains = [str(c.get_editor_property('chain_name')) for c in (ctrl.get_retarget_chains() or [])]
    root = str(ctrl.get_retarget_root())
    return chains, '' if root in ('None', '') else root


def _require_retargetable(rig, role: str) -> None:
    chains, root = _rig_chains(rig)
    if not chains or not root:
        # The engine asserts (crashes) when retargeting with rigs lacking chains/root, so refuse early.
        raise ToolError(Code.INVALID_ARGUMENT, f'{role} IK Rig {rig.get_name()} has no retarget chains/root',
                        target=rig.get_path_name(),
                        likely_causes=['Skeleton did not match an auto template; add a retarget root and chains '
                                       '(IK Rig editor or IKRigController.add_retarget_chain) first.'])


@unreal.uclass()
class AnimationTools(unreal.ToolsetDefinition):
    """Animation authoring: Anim Blueprints with state machines (states playing sequences or
    blend spaces, transitions with bool/auto rules), montages with sections and notifies,
    1D/2D blend spaces, and structured inspection of animation assets."""

    @agent_tool(mutates=True)
    def create_anim_blueprint(asset_path: str, skeleton_path: str, parent_class: str | None = None) -> dict:
        """Creates an Animation Blueprint targeting a skeleton.

        Args:
            asset_path: New asset path, e.g. /Game/Characters/ABP_Hero.
            skeleton_path: Skeleton (or Skeletal Mesh) the Anim Blueprint animates.
            parent_class: Optional AnimInstance subclass (default AnimInstance).
        """
        package, folder, name = _new_asset_path(asset_path)
        lib = native.require_graph('Anim Blueprint creation')
        skeleton = _skeleton(skeleton_path)
        parent = resolve.resolve_class(parent_class, unreal.AnimInstance) if parent_class else None
        bp = lib.create_anim_blueprint(folder, name, skeleton, parent)
        if bp is None:
            raise ToolError(Code.UE_OPERATION_FAILED, f'Could not create {package}', target=package)
        return {'asset': package, 'skeleton': skeleton.get_path_name()}

    @agent_tool(mutates=True)
    def add_anim_state_machine(asset_path: str, machine_name: str, connect_to_output_pose: bool = True) -> dict:
        """Adds a state machine node to the AnimGraph (optionally wired to Output Pose).

        Args:
            asset_path: Animation Blueprint path.
            machine_name: State machine name, e.g. "Locomotion".
            connect_to_output_pose: Replace the Output Pose input with this state machine.
        """
        bp = _anim_bp(asset_path)
        lib = native.require_graph('State machine creation')
        name = native.check(lib.anim_add_state_machine(bp, machine_name, connect_to_output_pose), bp.get_path_name())
        return {'state_machine': name}

    @agent_tool(mutates=True)
    def add_anim_state(asset_path: str, machine_name: str, state_name: str, animation_path: str | None = None,
                       blend_x_variable: str | None = None, blend_y_variable: str | None = None,
                       set_as_entry: bool = False) -> dict:
        """Adds a state that plays a sequence or blend space. The first state becomes the entry state.

        Args:
            asset_path: Animation Blueprint path.
            machine_name: Existing state machine.
            state_name: New state name, e.g. "Idle", "Locomotion", "Jump".
            animation_path: AnimSequence or BlendSpace asset to play (optional: empty state).
            blend_x_variable: Float member variable driving the blend space X axis (e.g. "Speed").
            blend_y_variable: Float member variable driving the blend space Y axis (e.g. "Direction").
            set_as_entry: Make this the entry state.
        """
        bp = _anim_bp(asset_path)
        lib = native.require_graph('State creation')
        anim = resolve.load_asset(animation_path, unreal.AnimationAsset) if animation_path else None
        name = native.check(lib.anim_add_state(bp, machine_name, state_name, anim, blend_x_variable or 'None',
                                               blend_y_variable or 'None', set_as_entry), bp.get_path_name())
        return {'state': name, 'animation': to_jsonable(anim)}

    @agent_tool(mutates=True)
    def add_anim_transition(asset_path: str, machine_name: str, from_state: str, to_state: str, rule: str = 'bool',
                            variable_name: str | None = None, crossfade_duration: float = 0.2) -> dict:
        """Adds a transition between two states with a rule.

        Args:
            asset_path: Animation Blueprint path.
            machine_name: State machine name.
            from_state: Source state.
            to_state: Target state.
            rule: "bool" (variable true), "not_bool" (variable false), "auto" (source animation finishing)
                or "always".
            variable_name: Bool member variable for bool/not_bool rules (e.g. "IsInAir").
            crossfade_duration: Blend time in seconds.
        """
        bp = _anim_bp(asset_path)
        lib = native.require_graph('Transition creation')
        node = native.check(lib.anim_add_transition(bp, machine_name, from_state, to_state, rule,
                                                    variable_name or 'None', crossfade_duration), bp.get_path_name())
        return {'transition': node, 'from': from_state, 'to': to_state, 'rule': rule}

    @agent_tool()
    def inspect_anim_state_machines(asset_path: str) -> dict:
        """Returns all state machines of an Anim Blueprint: entry state, states (with animation
        assets) and transitions (with rule summary and crossfade).

        Args:
            asset_path: Animation Blueprint path.
        """
        bp = _anim_bp(asset_path)
        details = _describe(bp)
        details['target_skeleton'] = to_jsonable(bp.get_editor_property('target_skeleton'))
        return details

    @agent_tool(mutates=True)
    def create_anim_montage(asset_path: str, animation_path: str) -> dict:
        """Creates an Anim Montage from an animation sequence (default slot).

        Args:
            asset_path: New montage path, e.g. /Game/Anims/AM_Attack.
            animation_path: Source AnimSequence.
        """
        package, folder, name = _new_asset_path(asset_path)
        anim = resolve.load_asset(animation_path, unreal.AnimSequence)
        factory = unreal.AnimMontageFactory()
        factory.set_editor_property('source_animation', anim)
        factory.set_editor_property('target_skeleton', anim.get_editor_property('skeleton'))
        montage = editor.asset_tools().create_asset(name, folder, unreal.AnimMontage, factory)
        if montage is None:
            raise ToolError(Code.UE_OPERATION_FAILED, f'Could not create {package}', target=package)
        return {'asset': package, 'length': round(montage.get_play_length(), 4),
                'slots': [str(s) for s in ALIB.get_montage_slot_names(montage)]}

    @agent_tool(mutates=True)
    def add_montage_section(montage_path: str, section_name: str, start_time: float) -> dict:
        """Adds a named section to a montage (e.g. combo steps). Sections are not auto-linked.

        Args:
            montage_path: Anim Montage path.
            section_name: Section name.
            start_time: Section start in seconds.
        """
        montage = resolve.load_asset(montage_path, unreal.AnimMontage)
        ctx().set_target(montage.get_outermost().get_name())
        lib = native.require_graph('Montage sections')
        native.check(lib.montage_add_section(montage, section_name, start_time), montage.get_path_name())
        return {'section': section_name, 'start_time': start_time}

    @agent_tool(mutates=True)
    def add_animation_notify(animation_path: str, notify_class: str, time: float, duration: float = 0.0,
                             track_name: str = 'AgentNotifies') -> dict:
        """Adds an Anim Notify (or Notify State when duration > 0) to a sequence or montage.

        Args:
            animation_path: AnimSequence or AnimMontage path.
            notify_class: Notify class, e.g. AnimNotify_PlaySound, AnimNotify_PlayNiagaraEffect,
                AnimNotifyState_Trail, or a Blueprint notify path.
            time: Trigger time in seconds.
            duration: >0 creates a Notify State of this length.
            track_name: Notify track (created if missing).
        """
        anim = resolve.load_asset(animation_path, unreal.AnimSequenceBase)
        ctx().set_target(anim.get_outermost().get_name())
        cls = resolve.resolve_class(notify_class)
        length = anim.get_play_length()
        if not 0 <= time <= length:
            raise ToolError(Code.INVALID_ARGUMENT, f'time {time} is outside 0..{length:.3f}')
        if not ALIB.is_valid_anim_notify_track_name(anim, track_name):
            ALIB.add_animation_notify_track(anim, track_name)
        if duration > 0:
            if not resolve.is_child_of(cls, unreal.AnimNotifyState):
                raise ToolError(Code.WRONG_TYPE, f'{cls.get_name()} is not an AnimNotifyState (needed for duration > 0)')
            notify = ALIB.add_animation_notify_state_event(anim, track_name, time, duration, cls)
        else:
            if not resolve.is_child_of(cls, unreal.AnimNotify):
                raise ToolError(Code.WRONG_TYPE, f'{cls.get_name()} is not an AnimNotify class')
            notify = ALIB.add_animation_notify_event(anim, track_name, time, cls)
        if notify is None:
            raise ToolError(Code.UE_OPERATION_FAILED, 'Notify was not created')
        return {'notify': notify.get_name(), 'class': cls.get_name(), 'time': time, 'track': track_name}

    @agent_tool(mutates=True)
    def create_blend_space(asset_path: str, skeleton_path: str, samples_json: str, x_name: str = 'Speed',
                           x_min: float = 0.0, x_max: float = 600.0, y_name: str | None = None,
                           y_min: float = -180.0, y_max: float = 180.0) -> dict:
        """Creates a blend space (1D when y_name is omitted, 2D otherwise) with axes and samples.

        Args:
            asset_path: New blend space path, e.g. /Game/Anims/BS_Locomotion.
            skeleton_path: Skeleton or Skeletal Mesh.
            samples_json: JSON list, e.g. [{"animation": "/Game/Anims/Idle", "x": 0},
                {"animation": "/Game/Anims/Run", "x": 600}] (add "y" for 2D).
            x_name: X axis name.
            x_min: X minimum.
            x_max: X maximum.
            y_name: Y axis name (makes it 2D).
            y_min: Y minimum.
            y_max: Y maximum.
        """
        package, folder, name = _new_asset_path(asset_path)
        lib = native.require_graph('Blend space configuration')
        samples = parse_json_arg(samples_json, 'samples_json', list)
        skeleton = _skeleton(skeleton_path)
        anims = [(resolve.load_asset(s['animation'], unreal.AnimSequence), float(s.get('x', 0)), float(s.get('y', 0)))
                 for s in samples]
        factory = unreal.BlendSpaceFactoryNew() if y_name else unreal.BlendSpaceFactory1D()
        factory.set_editor_property('target_skeleton', skeleton)
        cls = unreal.BlendSpace if y_name else getattr(unreal, 'BlendSpace1D', unreal.BlendSpace)
        bs = editor.asset_tools().create_asset(name, folder, cls, factory)
        if bs is None:
            raise ToolError(Code.UE_OPERATION_FAILED, f'Could not create {package}', target=package)
        native.check(lib.blend_space_set_axes(bs, x_name, x_min, x_max, y_name or '', y_min, y_max), package)
        added = []
        for anim, x, y in anims:
            native.check(lib.blend_space_add_sample(bs, anim, x, y), package)
            added.append({'animation': anim.get_outermost().get_name(), 'x': x, 'y': y})
        return {'asset': package, 'dimensions': 2 if y_name else 1, 'samples': added}

    # ----------------------------------------------------------------- retargeting
    @agent_tool(mutates=True)
    def create_ik_rig(asset_path: str, skeletal_mesh_path: str, auto_retarget_chains: bool = True,
                      auto_full_body_ik: bool = False) -> dict:
        """Creates an IK Rig for a skeletal mesh, auto-generating retarget chains (and optionally a
        full-body IK setup) when the skeleton matches a known template (Mannequin, MetaHuman, ...).

        Args:
            asset_path: New IK Rig path, e.g. /Game/Retarget/IK_Hero.
            skeletal_mesh_path: Skeletal mesh.
            auto_retarget_chains: Generate retarget root + chains automatically.
            auto_full_body_ik: Generate a Full Body IK solver setup.
        """
        package, folder, name = _new_asset_path(asset_path)
        mesh = resolve.load_asset(skeletal_mesh_path, unreal.SkeletalMesh)
        rig = editor.asset_tools().create_asset(name, folder, unreal.IKRigDefinition, unreal.IKRigDefinitionFactory())
        ctrl = unreal.IKRigController.get_controller(rig)
        if not ctrl.set_skeletal_mesh(mesh):
            raise ToolError(Code.UE_OPERATION_FAILED, 'Skeletal mesh is incompatible with the IK Rig', target=package)
        matched = bool(ctrl.apply_auto_generated_retarget_definition()) if auto_retarget_chains else False
        fbik = bool(ctrl.apply_auto_fbik()) if auto_full_body_ik else False
        if auto_retarget_chains and not matched:
            ctx().warn('Skeleton did not match a known template; add chains with the IK Rig editor or '
                       'IKRigController.add_retarget_chain.', 'NO_TEMPLATE_MATCH')
        chains, root = _rig_chains(rig)
        return {'asset': package, 'template_matched': matched, 'full_body_ik': fbik,
                'retarget_root': root or None, 'chains': chains, 'retargetable': bool(chains and root)}

    @agent_tool(mutates=True)
    def create_ik_retargeter(asset_path: str, source_ik_rig: str, target_ik_rig: str,
                             source_preview_mesh: str | None = None, target_preview_mesh: str | None = None,
                             auto_align: bool = True) -> dict:
        """Creates an IK Retargeter between two IK Rigs: default ops, fuzzy chain mapping and
        (optionally) automatic pose alignment.

        Args:
            asset_path: New retargeter path, e.g. /Game/Retarget/RTG_MannyToHero.
            source_ik_rig: IK Rig of the animations' skeleton.
            target_ik_rig: IK Rig of the destination skeleton.
            source_preview_mesh: Optional source preview mesh.
            target_preview_mesh: Optional target preview mesh.
            auto_align: Auto-align target bones to the source pose.
        """
        package, folder, name = _new_asset_path(asset_path)
        src = resolve.load_asset(source_ik_rig, unreal.IKRigDefinition)
        tgt = resolve.load_asset(target_ik_rig, unreal.IKRigDefinition)
        ready = all(_rig_chains(r)[0] and _rig_chains(r)[1] for r in (src, tgt))
        rtg = editor.asset_tools().create_asset(name, folder, unreal.IKRetargeter, unreal.IKRetargetFactory())
        ctrl = unreal.IKRetargeterController.get_controller(rtg)
        ctrl.set_ik_rig(unreal.RetargetSourceOrTarget.SOURCE, src)
        ctrl.set_ik_rig(unreal.RetargetSourceOrTarget.TARGET, tgt)
        if source_preview_mesh:
            ctrl.set_preview_mesh(unreal.RetargetSourceOrTarget.SOURCE, resolve.load_asset(source_preview_mesh, unreal.SkeletalMesh))
        if target_preview_mesh:
            ctrl.set_preview_mesh(unreal.RetargetSourceOrTarget.TARGET, resolve.load_asset(target_preview_mesh, unreal.SkeletalMesh))
        if ready:
            ctrl.add_default_ops()
            try:
                ctrl.assign_ik_rig_to_all_ops(unreal.RetargetSourceOrTarget.TARGET, tgt)
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            ctrl.auto_map_chains(unreal.AutoMapChainType.FUZZY, True)
            if auto_align:
                ctrl.auto_align_all_bones(unreal.RetargetSourceOrTarget.TARGET)
        else:
            ctx().warn('A rig has no retarget chains/root: ops, chain mapping and alignment were skipped. '
                       'Define chains, then re-run setup in the IK Retargeter editor.', 'RIG_NOT_RETARGETABLE')
        return {'asset': package, 'source': src.get_path_name(), 'target': tgt.get_path_name(),
                'ops': int(ctrl.get_num_retarget_ops()), 'configured': ready}

    @agent_tool(mutates=True, transaction=False)
    def retarget_animations(retargeter_path: str, animation_paths: list[str], source_mesh: str, target_mesh: str,
                            target_folder: str | None = None, suffix: str = '_Retargeted',
                            overwrite: bool = False) -> dict:
        """Duplicates animations (sequences, montages, blend spaces) and retargets them to the target
        skeletal mesh with an IK Retargeter.

        Args:
            retargeter_path: IK Retargeter asset.
            animation_paths: Animations to retarget.
            source_mesh: Skeletal mesh the animations were made for.
            target_mesh: Destination skeletal mesh.
            target_folder: Output folder (default: next to the sources).
            suffix: Name suffix for the new assets.
            overwrite: Overwrite existing assets with the same names.
        """
        rtg = resolve.load_asset(retargeter_path, unreal.IKRetargeter)
        rctrl = unreal.IKRetargeterController.get_controller(rtg)
        for role, side in (('Source', unreal.RetargetSourceOrTarget.SOURCE), ('Target', unreal.RetargetSourceOrTarget.TARGET)):
            rig = rctrl.get_ik_rig(side)
            if rig is None:
                raise ToolError(Code.INVALID_ARGUMENT, f'Retargeter has no {role.lower()} IK Rig', target=rtg.get_path_name())
            _require_retargetable(rig, role)
        datas = [resolve.asset_data(p) for p in animation_paths]
        src = resolve.load_asset(source_mesh, unreal.SkeletalMesh)
        tgt = resolve.load_asset(target_mesh, unreal.SkeletalMesh)
        out = unreal.IKRetargetBatchOperation.duplicate_and_retarget(
            datas, src, tgt, rtg, '', '', '', suffix, target_folder or '', not bool(target_folder), True, overwrite)
        created = [str(d.package_name) for d in out or []]
        if not created:
            raise ToolError(Code.UE_OPERATION_FAILED, 'Retargeting produced no assets',
                            likely_causes=['Chains not mapped (inspect the retargeter)', 'Assets already exist (overwrite=false).'])
        ctx().set_target(rtg.get_path_name())
        return {'created': created}

    @agent_tool()
    def inspect_animation_asset(asset_path: str) -> dict:
        """Returns animation asset info: class, skeleton, length, frames, rate scale, notifies
        (name/time/track), montage slots and sections where applicable.

        Args:
            asset_path: AnimSequence, AnimMontage or BlendSpace path.
        """
        anim = resolve.load_asset(asset_path, unreal.AnimationAsset)
        ctx().set_target(anim.get_outermost().get_name())
        details: dict = {'asset': anim.get_outermost().get_name(), 'class': anim.get_class().get_name(),
                         'skeleton': to_jsonable(anim.get_editor_property('skeleton'))}
        if isinstance(anim, unreal.AnimSequenceBase):
            details['length'] = round(anim.get_play_length(), 4)
            details['rate_scale'] = to_jsonable(anim.get_editor_property('rate_scale'))
            events = []
            for e in ALIB.get_animation_notify_events(anim) or []:
                notify = e.get_editor_property('notify') or e.get_editor_property('notify_state_class')
                events.append({'name': str(e.get_editor_property('notify_name')),
                               'class': notify.get_class().get_name() if notify else None,
                               'time': round(ALIB.get_anim_notify_event_trigger_time(e), 4),
                               'duration': round(ALIB.get_anim_notify_event_duration(e), 4)})
            details['notifies'] = events
            details['notify_tracks'] = [str(t) for t in ALIB.get_animation_notify_track_names(anim)]
        if isinstance(anim, unreal.AnimMontage):
            details['slots'] = [str(s) for s in ALIB.get_montage_slot_names(anim)]
            lib = native.graph_library()
            if lib is not None:
                sections = []
                for entry in lib.montage_get_sections(anim) or []:
                    name, start, nxt = str(entry).split('|')
                    sections.append({'name': name, 'start_time': float(start), 'next': None if nxt == 'None' else nxt})
                details['sections'] = sections
        if isinstance(anim, unreal.AnimSequence):
            try:
                details['frames'] = int(ALIB.get_num_frames(anim))
                details['root_motion'] = bool(anim.get_editor_property('enable_root_motion'))
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        return details
