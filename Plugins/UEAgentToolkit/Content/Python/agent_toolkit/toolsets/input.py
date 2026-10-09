"""InputTools: Enhanced Input actions, mapping contexts, key mappings (with triggers and
modifiers), project default contexts, Blueprint wiring (mapping context registration,
input action events) and controller support (gamepad mirroring, force feedback).

Trigger specs: pressed, released, down, hold[:seconds], hold_and_release[:seconds],
tap[:seconds], pulse[:interval], chord:<InputActionPath>.
Modifier specs: negate[:xyz axes, e.g. negate:x], swizzle[:YXZ|ZYX|XZY|YZX|ZXY],
deadzone[:lower[,upper][,axial|radial]], scalar:<x>[,y,z], response_curve:<exponent>[,y,z],
smooth, fov_scaling.
"""

from __future__ import annotations

import json

import unreal

from agent_toolkit.core import bp as bpu
from agent_toolkit.core import config, editor, native, resolve
from agent_toolkit.core.errors import Code, ToolError
from agent_toolkit.core.serialize import split_csv, to_jsonable
from agent_toolkit.core.tooling import agent_tool, ctx

_VALUE_TYPES = {'bool': 'BOOLEAN', 'boolean': 'BOOLEAN', 'axis1d': 'AXIS1D', 'float': 'AXIS1D',
                'axis2d': 'AXIS2D', 'vector2d': 'AXIS2D', 'axis3d': 'AXIS3D', 'vector': 'AXIS3D'}
_EI_SETTINGS_SECTION = '/Script/EnhancedInput.EnhancedInputDeveloperSettings'

# Keyboard/mouse button -> gamepad button used by add_gamepad_mappings (Xbox naming: Bottom=A,
# Right=B, Left=X, Top=Y; Special_Left=View/Back, Special_Right=Menu/Start).
_GAMEPAD_BUTTONS = {
    'SpaceBar': 'Gamepad_FaceButton_Bottom', 'Enter': 'Gamepad_FaceButton_Bottom',
    'LeftControl': 'Gamepad_FaceButton_Right', 'C': 'Gamepad_FaceButton_Right',
    'BackSpace': 'Gamepad_FaceButton_Right',
    'E': 'Gamepad_FaceButton_Left', 'F': 'Gamepad_FaceButton_Left', 'R': 'Gamepad_FaceButton_Top',
    'LeftShift': 'Gamepad_LeftThumbstick', 'V': 'Gamepad_RightThumbstick',
    'MiddleMouseButton': 'Gamepad_RightThumbstick',
    'LeftMouseButton': 'Gamepad_RightTrigger', 'RightMouseButton': 'Gamepad_LeftTrigger',
    'Q': 'Gamepad_LeftShoulder', 'G': 'Gamepad_RightShoulder',
    'Tab': 'Gamepad_Special_Left', 'I': 'Gamepad_Special_Left',
    'Escape': 'Gamepad_Special_Right', 'P': 'Gamepad_Special_Right',
    'One': 'Gamepad_DPad_Up', 'Two': 'Gamepad_DPad_Right', 'Three': 'Gamepad_DPad_Down', 'Four': 'Gamepad_DPad_Left',
    'Up': 'Gamepad_DPad_Up', 'Right': 'Gamepad_DPad_Right', 'Down': 'Gamepad_DPad_Down', 'Left': 'Gamepad_DPad_Left',
    'MouseScrollUp': 'Gamepad_DPad_Up', 'MouseScrollDown': 'Gamepad_DPad_Down',
}
_MOVE_KEYS = {'W', 'A', 'S', 'D', 'Up', 'Down', 'Left', 'Right'}
_MOUSE_AXES = {'Mouse2D': 'Gamepad_Right2D', 'MouseX': 'Gamepad_RightX', 'MouseY': 'Gamepad_RightY'}
_FF_CHANNELS = ('left_large', 'left_small', 'right_large', 'right_small')


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def _split_specs(text: str | None) -> list[str]:
    """CSV of trigger/modifier specs where multi-value args stay attached:
    "deadzone:0.2,1,radial,negate:y" -> ["deadzone:0.2,1,radial", "negate:y"]."""
    out = []
    for part in split_csv(text):
        if out and ':' in out[-1] and ':' not in part and (_is_number(part) or part.lower() in ('axial', 'radial')):
            out[-1] += ',' + part
        else:
            out.append(part)
    return out


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
             'smooth': unreal.InputModifierSmooth, 'fov_scaling': unreal.InputModifierFOVScaling,
             'response_curve': unreal.InputModifierResponseCurveExponential}
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
        parts = [p.strip() for p in arg.split(',') if p.strip()]
        kinds = [p for p in parts if p.lower() in ('axial', 'radial')]
        nums = [float(p) for p in parts if p not in kinds]
        if nums:
            mod.set_editor_property('lower_threshold', nums[0])
        if len(nums) > 1:
            mod.set_editor_property('upper_threshold', nums[1])
        if kinds:
            mod.set_editor_property('type', getattr(unreal.DeadZoneType, kinds[0].upper()))
    elif name in ('scalar', 'response_curve'):
        vals = [float(v) for v in (arg or '1').split(',')]
        vals += [vals[-1]] * (3 - len(vals))
        mod.set_editor_property('scalar' if name == 'scalar' else 'curve_exponent', unreal.Vector(*vals[:3]))
    return mod


def _trigger_spec(trig) -> str | None:
    """Inverse of _make_trigger (None for trigger types it cannot express)."""
    name = trig.get_class().get_name()
    if name == 'InputTriggerHold':
        return f"hold:{trig.get_editor_property('hold_time_threshold'):g}"
    if name == 'InputTriggerHoldAndRelease':
        return f"hold_and_release:{trig.get_editor_property('hold_time_threshold'):g}"
    if name == 'InputTriggerTap':
        return f"tap:{trig.get_editor_property('tap_release_time_threshold'):g}"
    if name == 'InputTriggerPulse':
        return f"pulse:{trig.get_editor_property('interval'):g}"
    if name == 'InputTriggerChordAction':
        chord = trig.get_editor_property('chord_action')
        return f'chord:{chord.get_outermost().get_name()}' if chord else None
    return {'InputTriggerPressed': 'pressed', 'InputTriggerReleased': 'released',
            'InputTriggerDown': 'down'}.get(name)


def _axis_modifier_specs(mods) -> list[str]:
    """Negate/swizzle specs of a mapping (the parts of a mouse mapping that carry over to a stick)."""
    out = []
    for mod in mods or []:
        if mod is None:
            continue
        name = mod.get_class().get_name()
        if name == 'InputModifierNegate':
            axes = ''.join(a for a in 'xyz' if mod.get_editor_property(a))
            if axes:
                out.append(f'negate:{axes}')
        elif name == 'InputModifierSwizzleAxis':
            out.append(f"swizzle:{str(mod.get_editor_property('order')).split('.')[-1].split(':')[0].strip()}")
    return out


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


def _add_mapping(imc: unreal.InputMappingContext, ia: unreal.InputAction, key: str, triggers: str | None,
                 modifiers: str | None) -> dict:
    k = _key(key)
    mappings, _ = _mappings(imc)
    if _find_mapping_indices(mappings, ia, key):
        raise ToolError(Code.ALREADY_EXISTS, f'{key} is already mapped to {ia.get_name()}',
                        likely_causes=['Use update_key_mapping to change triggers/modifiers.'])
    # Validate/construct everything before mutating so a bad spec leaves the context untouched.
    new_triggers = [_make_trigger(t, imc) for t in _split_specs(triggers)]
    new_modifiers = [_make_modifier(x, imc) for x in _split_specs(modifiers)]
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


def _parse_key_map(text: str | None) -> dict[str, str | None]:
    """"E=Gamepad_FaceButton_Right,Q=none" -> {"e": "Gamepad_FaceButton_Right", "q": None}."""
    out = {}
    for pair in split_csv(text):
        src, sep, dst = pair.partition('=')
        if not sep or not src.strip() or not dst.strip():
            raise ToolError(Code.INVALID_ARGUMENT, f'key_map entry {pair!r} must be Key=GamepadKey (or Key=none)')
        dst = dst.strip()
        if dst.lower() != 'none':
            _key(dst)  # validate
        out[src.strip().lower()] = None if dst.lower() == 'none' else dst
    return out


def _gamepad_plan(imc: unreal.InputMappingContext, key_map: dict, deadzone: float, look_scale: float) -> dict:
    """Gamepad mappings mirroring the keyboard/mouse mappings of a context (nothing is written)."""
    mappings, _ = _mappings(imc)
    by_action: dict = {}
    for m in mappings:
        ia = m.get_editor_property('action')
        if ia is not None:
            by_action.setdefault(ia.get_outermost().get_name(), (ia, []))[1].append(m)
    used = {}  # gamepad key -> action already using it
    for m in mappings:
        key = str(m.get_editor_property('key').get_editor_property('key_name'))
        if key.startswith('Gamepad_') and m.get_editor_property('action') is not None:
            used[key] = m.get_editor_property('action').get_outermost().get_name()
    plan, skipped, unmapped, conflicts = [], [], [], []
    for action, (ia, maps) in by_action.items():
        keys = [str(m.get_editor_property('key').get_editor_property('key_name')) for m in maps]
        if any(k.startswith('Gamepad_') for k in keys):
            skipped.append({'action': action, 'reason': 'already has gamepad mappings'})
            continue
        value_type = str(ia.get_editor_property('value_type')).upper()
        entries = {}  # gamepad key -> entry (one mapping per gamepad key)

        def add(pad_key, sources, triggers=None, modifiers=None):
            if pad_key in entries:
                entries[pad_key]['from'] += sources
                return
            if used.get(pad_key, action) != action:
                conflicts.append({'action': action, 'from': sources, 'key': pad_key, 'used_by': used[pad_key]})
                return
            entries[pad_key] = {'action': action, 'key': pad_key, 'from': list(sources),
                                'triggers': ','.join(triggers or []) or None,
                                'modifiers': ','.join(modifiers or []) or None}

        move = [k for k in keys if k in _MOVE_KEYS and k.lower() not in key_map]
        if move and 'BOOLEAN' not in value_type:
            if 'AXIS1D' in value_type:
                pad = 'Gamepad_LeftY' if set(move) & {'W', 'S', 'Up', 'Down'} else 'Gamepad_LeftX'
                add(pad, move, modifiers=[f'deadzone:{deadzone:g},1,axial'])
            else:
                add('Gamepad_Left2D', move, modifiers=[f'deadzone:{deadzone:g},1,radial'])
        for m, key in zip(maps, keys):
            if key in move and 'BOOLEAN' not in value_type:
                continue
            if key.lower() in key_map:
                pad = key_map[key.lower()]
                if pad is None:
                    continue
            elif key in _MOUSE_AXES:
                pad = _MOUSE_AXES[key]
                mods = _axis_modifier_specs(m.get_editor_property('modifiers'))
                mods.append(f"deadzone:{deadzone:g},1,{'radial' if key == 'Mouse2D' else 'axial'}")
                if look_scale != 1:
                    mods.append(f'scalar:{look_scale:g}')
                add(pad, [key], modifiers=mods)
                continue
            else:
                pad = _GAMEPAD_BUTTONS.get(key)
            if pad is None:
                unmapped.append({'action': action, 'key': key})
                continue
            triggers = [s for s in (_trigger_spec(t) for t in m.get_editor_property('triggers') or [] if t) if s]
            add(pad, [key], triggers=triggers)
        for pad_key, entry in entries.items():
            used[pad_key] = action
            plan.append(entry)
    return {'plan': plan, 'skipped_actions': skipped, 'unmapped_keys': unmapped, 'conflicts': conflicts}


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
        for spec in _split_specs(triggers):  # validate specs before creating the asset
            _make_trigger(spec, scratch)
        for spec in _split_specs(modifiers):
            _make_modifier(spec, scratch)
        folder, name = package.rsplit('/', 1)
        ia = editor.asset_tools().create_asset(name, folder, unreal.InputAction, unreal.InputAction_Factory())
        ia.set_editor_property('value_type', getattr(unreal.InputActionValueType, vt))
        ia.set_editor_property('consume_input', consume_input)
        if triggers:
            ia.set_editor_property('triggers', [_make_trigger(t, ia) for t in _split_specs(triggers)])
        if modifiers:
            ia.set_editor_property('modifiers', [_make_modifier(m, ia) for m in _split_specs(modifiers)])
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
        return _add_mapping(imc, ia, key, triggers, modifiers)

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
                                  [_make_trigger(t, imc) for t in _split_specs(triggers)])
        if modifiers is not None:
            m.set_editor_property('modifiers', [] if modifiers.lower() == 'none' else
                                  [_make_modifier(x, imc) for x in _split_specs(modifiers)])
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

    @agent_tool(mutates=True)
    def add_gamepad_mappings(context_path: str, key_map: str | None = None, deadzone: float = 0.2,
                             look_scale: float = 1.0, dry_run: bool = False) -> dict:
        """Adds controller support to a Mapping Context by mirroring its keyboard/mouse mappings
        onto gamepad keys: WASD/arrows -> Gamepad_Left2D (LeftX/LeftY for axis1d), Mouse2D/MouseX/
        MouseY -> right stick (negate/swizzle copied, radial dead zone), buttons via a standard
        layout (SpaceBar -> FaceButton_Bottom, LeftMouseButton -> RightTrigger, RightMouseButton ->
        LeftTrigger, E/F -> FaceButton_Left, R -> FaceButton_Top, C/LeftControl -> FaceButton_Right,
        LeftShift -> LeftThumbstick, Q/G -> shoulders, Escape -> Special_Right, Tab -> Special_Left,
        1-4/arrows -> DPad). Triggers of button mappings are copied. Actions that already have a
        gamepad mapping are skipped; gamepad keys already used by another action are reported as
        conflicts (resolve with key_map).

        Args:
            context_path: Input Mapping Context path.
            key_map: Overrides, e.g. "E=Gamepad_FaceButton_Right,Q=none" (none = do not mirror).
            deadzone: Lower dead zone for stick mappings.
            look_scale: Scalar applied to right-stick look mappings (stick values are -1..1 per frame).
            dry_run: Return the planned mappings without writing.
        """
        imc = resolve.load_asset(context_path, unreal.InputMappingContext)
        ctx().set_target(imc.get_outermost().get_name())
        if not 0 <= deadzone < 1:
            raise ToolError(Code.INVALID_ARGUMENT, 'deadzone must be in [0, 1)')
        result = _gamepad_plan(imc, _parse_key_map(key_map), deadzone, look_scale)
        result['context'] = imc.get_outermost().get_name()
        if dry_run:
            ctx().mark_modified(False)
            return {'dry_run': True, **result}
        added = []
        for entry in result.pop('plan'):
            ia = resolve.load_asset(entry['action'], unreal.InputAction)
            info = _add_mapping(imc, ia, entry['key'], entry['triggers'], entry['modifiers'])
            added.append({**info, 'from': entry['from']})
        if result['conflicts'] or result['unmapped_keys']:
            ctx().warn('Some keys were not mirrored; see conflicts / unmapped_keys and pass key_map.', 'PARTIAL')
        ctx().mark_modified(bool(added))
        return {'added': added, **result}

    @agent_tool(mutates=True)
    def create_force_feedback_effect(asset_path: str, curve: str = '0:1,0.2:0', channels: str = 'all') -> dict:
        """Creates a Force Feedback Effect (controller rumble) asset with one intensity curve.
        Play it with PlayerController "Client Play Force Feedback"
        (/Script/Engine.PlayerController:K2_ClientPlayForceFeedback) or
        GameplayStatics:SpawnForceFeedbackAtLocation. Requires the UEAgentToolkitNative plugin.

        Args:
            asset_path: e.g. /Game/Input/FF_Hit.
            curve: "time:intensity" keys in seconds / 0..1, e.g. "0:1,0.15:0.6,0.4:0".
            channels: "all" or comma-separated left_large, left_small, right_large, right_small.
        """
        package, _ = resolve.normalize_asset_path(asset_path)
        ctx().set_target(package)
        if resolve.asset_exists(package):
            raise ToolError(Code.ALREADY_EXISTS, f'{package} already exists', target=package)
        keys = []
        for pair in split_csv(curve):
            t, sep, v = pair.partition(':')
            if not sep or not _is_number(t) or not _is_number(v):
                raise ToolError(Code.INVALID_ARGUMENT, f'curve key {pair!r} must be time:intensity, e.g. 0.2:0.5')
            keys.append((float(t), float(v)))
        if len(keys) < 2 or any(b[0] <= a[0] for a, b in zip(keys, keys[1:])) or keys[0][0] < 0:
            raise ToolError(Code.INVALID_ARGUMENT, 'curve needs >= 2 keys with increasing, non-negative times')
        if any(not 0 <= v <= 1 for _, v in keys):
            raise ToolError(Code.INVALID_ARGUMENT, 'intensities must be in [0, 1]')
        chosen = set(_FF_CHANNELS) if channels.strip().lower() == 'all' else {c.lower() for c in split_csv(channels)}
        if not chosen or chosen - set(_FF_CHANNELS):
            raise ToolError(Code.INVALID_ARGUMENT, f'channels must be "all" or a subset of {list(_FF_CHANNELS)}')
        lib = getattr(unreal, 'AgentToolkitWorldLibrary', None) or native.require('Force feedback curves')
        folder, name = package.rsplit('/', 1)
        effect = editor.asset_tools().create_asset(name, folder, unreal.ForceFeedbackEffect,
                                                   unreal.ForceFeedbackEffectFactory())
        if effect is None:
            raise ToolError(Code.UE_OPERATION_FAILED, f'Could not create {package}', target=package)
        flags = ','.join(f"bAffects{''.join(p.title() for p in c.split('_'))}={c in chosen}" for c in _FF_CHANNELS)
        key_text = ','.join(f'(Time={t:f},Value={v:f})' for t, v in keys)
        native.check(lib.set_property_from_text(effect, 'ChannelDetails',
                                                f'(({flags},Curve=(EditorCurveData=(Keys=({key_text})))))'),
                     package, Code.INVALID_ARGUMENT)
        return {'asset': package, 'duration': keys[-1][0], 'channels': [c for c in _FF_CHANNELS if c in chosen],
                'keys': [{'time': t, 'value': v} for t, v in keys]}
