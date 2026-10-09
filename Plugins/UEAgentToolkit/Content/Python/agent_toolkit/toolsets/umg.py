"""UMGTools: widget layout (Canvas slot anchors/position/size/alignment), widget properties by
name, Widget Animations (keyframed opacity / render transform / color) and gamepad/keyboard
focus navigation (navigation rules, focusable widgets, initial focus).

Complements Epic's UMGToolSet (create Widget Blueprints, add/move/remove widgets, widget tree,
compile) and MVVMToolset. Widget Animation and widget lookup use the UEAgentToolkitNative plugin.
"""

from __future__ import annotations

import json

import unreal

from agent_toolkit.core import bp as bpu
from agent_toolkit.core import native, resolve
from agent_toolkit.core.errors import Code, ToolError
from agent_toolkit.core.serialize import from_jsonable, parse_json_arg, split_csv, to_jsonable
from agent_toolkit.core.tooling import agent_tool, ctx

_ANCHORS = {
    'top_left': (0, 0, 0, 0), 'top_center': (0.5, 0, 0.5, 0), 'top_right': (1, 0, 1, 0),
    'center_left': (0, 0.5, 0, 0.5), 'center': (0.5, 0.5, 0.5, 0.5), 'center_right': (1, 0.5, 1, 0.5),
    'bottom_left': (0, 1, 0, 1), 'bottom_center': (0.5, 1, 0.5, 1), 'bottom_right': (1, 1, 1, 1),
    'top_fill': (0, 0, 1, 0), 'bottom_fill': (0, 1, 1, 1), 'left_fill': (0, 0, 0, 1), 'right_fill': (1, 0, 1, 1),
    'fill': (0, 0, 1, 1),
}
_TRANSFORM_CHANNELS = {'translation_x': 0, 'translation_y': 1, 'angle': 2, 'scale_x': 3, 'scale_y': 4,
                       'shear_x': 5, 'shear_y': 6}
_COLOR_CHANNELS = {'r': 0, 'g': 1, 'b': 2, 'a': 3}
_NAV_DIRS = ('up', 'down', 'left', 'right', 'next', 'previous')
_NAV_RULES = {'escape': 'ESCAPE', 'stop': 'STOP', 'wrap': 'WRAP'}


def _wbp(asset_path: str) -> unreal.WidgetBlueprint:
    wbp = resolve.load_asset(asset_path, unreal.WidgetBlueprint)
    ctx().set_target(wbp.get_outermost().get_name())
    return wbp


def _widget(wbp: unreal.WidgetBlueprint, name: str) -> unreal.Widget:
    lib = native.require_graph('Widget lookup')
    widget = getattr(unreal, 'AgentToolkitWorldLibrary').find_widget_in_blueprint(wbp, name) if lib else None
    if widget is None:
        raise ToolError(Code.OBJECT_NOT_FOUND, f'Widget {name!r} not found in {wbp.get_name()}', target=wbp.get_path_name(),
                        likely_causes=['Use UMGToolSet.GetWidgets to list widget names.'])
    return widget


def _world_lib():
    native.require_graph('UMG helpers')
    return unreal.AgentToolkitWorldLibrary


def _floats(text: str, count: int, name: str) -> list[float]:
    vals = [float(v) for v in split_csv(text)]
    if len(vals) != count:
        raise ToolError(Code.INVALID_ARGUMENT, f'{name} must have {count} comma-separated numbers, got {text!r}')
    return vals


def _enum_name(value) -> str:
    return str(value).split('.')[-1].split(':')[0].strip(' <>')


def _nav_data(wbp: unreal.WidgetBlueprint, spec: str) -> unreal.WidgetNavigationData:
    """"escape" / "stop" / "wrap", or a widget name (explicit navigation to that widget)."""
    rule = _NAV_RULES.get(spec.strip().lower())
    if rule:
        return unreal.WidgetNavigationData(getattr(unreal.UINavigationRule, rule))
    _widget(wbp, spec.strip())  # the target must exist
    return unreal.WidgetNavigationData(unreal.UINavigationRule.EXPLICIT, unreal.Name(spec.strip()))


def _set_navigation(widget: unreal.Widget, rules: dict) -> None:
    widget.modify()
    nav = widget.get_editor_property('navigation')
    if nav is None:
        nav = unreal.new_object(unreal.WidgetNavigation, widget)
    for direction, data in rules.items():
        nav.set_editor_property(direction, data)
    widget.set_editor_property('navigation', nav)


def _set_focusable(widget: unreal.Widget, focusable: bool) -> bool:
    try:
        widget.set_editor_property('is_focusable', focusable)
        return True
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _nav_info(widget: unreal.Widget) -> dict:
    nav = widget.get_editor_property('navigation')
    out = {}
    for direction in _NAV_DIRS if nav is not None else ():
        data = nav.get_editor_property(direction)
        rule = _enum_name(data.get_editor_property('rule')).lower()
        out[direction] = str(data.get_editor_property('widget_to_focus')) if rule == 'explicit' else rule
    return out


def _wire_focus_on_construct(wbp: unreal.WidgetBlueprint) -> str:
    """Event Construct -> SetFocus (self) so the widget's desired focus target gets focus when shown."""
    graph = unreal.BlueprintEditorLibrary.find_event_graph(wbp)
    if graph is None:
        raise ToolError(Code.WRONG_TYPE, 'Widget Blueprint has no event graph', target=wbp.get_path_name())
    lib = native.library()
    if lib is not None:
        for node in bpu.graph_nodes(graph):
            member = str(lib.get_node_member_name(node))  # "Widget:SetFocus"
            if node.get_class().get_name() == 'K2Node_CallFunction' and member.split(':')[-1] == 'SetFocus':
                return 'already_wired'
    construct = unreal.BlueprintEditorLibrary.add_event_override(wbp, 'Construct', unreal.IntPoint(0, -300))
    if construct is None:
        raise ToolError(Code.UE_OPERATION_FAILED, 'Could not add Event Construct', target=wbp.get_path_name())
    pos = construct.get_node_pos()
    focus = bpu.graph_editor(graph).add_call_function_node('/Script/UMG.Widget:SetFocus')
    if focus is None:
        raise ToolError(Code.UE_OPERATION_FAILED, 'Could not create a SetFocus node', target=wbp.get_path_name())
    bpu.set_node_pos(focus, pos.x + 300, pos.y)
    then_pin = construct.find_then_pin()
    previous = list(then_pin.list_connected_pins() or [])
    for p in previous:
        then_pin.break_single_pin_link(p)
    then_pin.try_create_connection(focus.find_execute_pin())
    for p in previous:
        focus.find_then_pin().try_create_connection(p)
    return 'wired'


@unreal.uclass()
class UMGTools(unreal.ToolsetDefinition):
    """UMG helpers: Canvas Panel slot layout (anchor presets, position, size, alignment, z-order,
    auto size), widget properties by name, Widget Animations with keyframes, and gamepad/keyboard
    menu navigation (focus rules, focusable widgets, initial focus)."""

    @agent_tool(mutates=True)
    def set_widget_layout(widget_blueprint: str, widget_name: str, anchors: str | None = None,
                          position: str | None = None, size: str | None = None, alignment: str | None = None,
                          auto_size: bool | None = None, z_order: int | None = None) -> dict:
        """Sets Canvas Panel slot layout of a widget. Only provided arguments change.

        Args:
            widget_blueprint: Widget Blueprint path.
            widget_name: Widget name (must be a child of a Canvas Panel).
            anchors: Preset (top_left, top_center, top_right, center_left, center, center_right, bottom_left,
                bottom_center, bottom_right, top_fill, bottom_fill, left_fill, right_fill, fill) or "minX,minY,maxX,maxY".
            position: "x,y" offset from the anchor (for fill anchors these are left/top margins).
            size: "width,height" (for fill anchors: right/bottom margins).
            alignment: "x,y" pivot in 0..1 (0.5,0.5 = centered on the anchor).
            auto_size: Size to content.
            z_order: Draw order.
        """
        wbp = _wbp(widget_blueprint)
        widget = _widget(wbp, widget_name)
        slot = widget.get_editor_property('slot')
        if not isinstance(slot, unreal.CanvasPanelSlot):
            raise ToolError(Code.WRONG_TYPE, f'{widget_name} is in a {slot.get_class().get_name() if slot else "no"} slot, '
                            'not a Canvas Panel slot', likely_causes=['Use ObjectTools.set_properties on the slot for '
                                                                      'other panel types (padding/alignment).'])
        slot.modify()
        changed = {}
        if anchors is not None:
            a = _ANCHORS.get(anchors.lower()) or tuple(_floats(anchors, 4, 'anchors'))
            slot.set_anchors(unreal.Anchors(minimum=unreal.Vector2D(a[0], a[1]), maximum=unreal.Vector2D(a[2], a[3])))
            changed['anchors'] = list(a)
        if position is not None:
            slot.set_position(unreal.Vector2D(*_floats(position, 2, 'position')))
            changed['position'] = position
        if size is not None:
            slot.set_size(unreal.Vector2D(*_floats(size, 2, 'size')))
            changed['size'] = size
        if alignment is not None:
            slot.set_alignment(unreal.Vector2D(*_floats(alignment, 2, 'alignment')))
            changed['alignment'] = alignment
        if auto_size is not None:
            slot.set_auto_size(auto_size)
            changed['auto_size'] = auto_size
        if z_order is not None:
            slot.set_z_order(z_order)
            changed['z_order'] = z_order
        if not changed:
            raise ToolError(Code.INVALID_ARGUMENT, 'No layout argument was provided.')
        wbp.modify()
        unreal.BlueprintEditorLibrary.compile_blueprint(wbp)
        return {'widget': widget_name, 'changed': changed}

    @agent_tool(mutates=True)
    def set_widget_properties(widget_blueprint: str, widget_name: str, properties_json: str) -> dict:
        """Sets properties of a designer widget by name, e.g. {"text": "Paused"} (TextBlock),
        {"percent": 0.5, "fill_color_and_opacity": [0,1,0,1]} (ProgressBar),
        {"render_opacity": 0.8, "visibility": "HIDDEN"}.

        Args:
            widget_blueprint: Widget Blueprint path.
            widget_name: Widget name.
            properties_json: JSON object of property names to values. Struct values given as objects are
                merged into the current value, e.g. {"font": {"size": 28}} keeps the font object and typeface.
        """
        wbp = _wbp(widget_blueprint)
        widget = _widget(wbp, widget_name)
        widget.modify()
        applied = []
        for key, value in parse_json_arg(properties_json, 'properties_json', dict).items():
            try:
                current = widget.get_editor_property(key)
            except Exception as e:  # pylint: disable=broad-exception-caught
                raise ToolError(Code.INVALID_ARGUMENT, f'{widget.get_class().get_name()} has no property {key!r}',
                                target=f'{wbp.get_path_name()}:{widget_name}') from e
            if isinstance(current, unreal.Text) or (key == 'text' and isinstance(value, str)):
                value = unreal.Text(str(value))
                widget.set_editor_property(key, value)
            else:
                widget.set_editor_property(key, from_jsonable(value, current, key))
            applied.append(key)
        wbp.modify()
        unreal.BlueprintEditorLibrary.compile_blueprint(wbp)
        return {'widget': widget_name, 'properties_set': applied}

    @agent_tool(mutates=True)
    def create_widget_animation(widget_blueprint: str, animation_name: str, length_seconds: float = 1.0) -> dict:
        """Creates a Widget Animation (available as a variable for Play Animation after compiling).

        Args:
            widget_blueprint: Widget Blueprint path.
            animation_name: Animation name, e.g. "FadeIn".
            length_seconds: Playback length.
        """
        wbp = _wbp(widget_blueprint)
        lib = _world_lib()
        if length_seconds <= 0:
            raise ToolError(Code.INVALID_ARGUMENT, 'length_seconds must be > 0')
        existing = json.loads(lib.describe_widget_animations(wbp))['animations']
        if any(a['name'] == animation_name for a in existing):
            raise ToolError(Code.ALREADY_EXISTS, f'Animation {animation_name!r} already exists', target=wbp.get_path_name())
        anim = lib.add_widget_animation(wbp, animation_name, length_seconds)
        if anim is None:
            raise ToolError(Code.UE_OPERATION_FAILED, 'Animation was not created')
        unreal.BlueprintEditorLibrary.compile_blueprint(wbp)
        return {'animation': animation_name, 'length': length_seconds}

    @agent_tool(mutates=True)
    def add_widget_animation_keys(widget_blueprint: str, animation_name: str, widget_name: str, property_name: str,
                                  keys_json: str, channel: str | None = None) -> dict:
        """Adds keyframes for a widget property to an animation.

        Args:
            widget_blueprint: Widget Blueprint path.
            animation_name: Existing animation.
            widget_name: Animated widget.
            property_name: RenderOpacity (or another float property), RenderTransform, or a color property
                such as ColorAndOpacity.
            keys_json: JSON [[time_seconds, value], ...], e.g. [[0, 0], [0.5, 1]].
            channel: RenderTransform: translation_x, translation_y, angle, scale_x, scale_y, shear_x, shear_y;
                color: r, g, b, a. Omit for float properties.
        """
        wbp = _wbp(widget_blueprint)
        lib = _world_lib()
        keys = parse_json_arg(keys_json, 'keys_json', list)
        try:
            times = [float(k[0]) for k in keys]
            values = [float(k[1]) for k in keys]
        except (TypeError, ValueError, IndexError) as e:
            raise ToolError(Code.INVALID_ARGUMENT, 'keys_json must be [[time, value], ...]') from e
        index = 0
        if property_name == 'RenderTransform':
            index = _TRANSFORM_CHANNELS.get((channel or '').lower(), -1)
        elif channel:
            index = _COLOR_CHANNELS.get(channel.lower(), -1)
        if index < 0:
            raise ToolError(Code.INVALID_ARGUMENT, f'Invalid channel {channel!r} for {property_name}',
                            likely_causes=[f'RenderTransform: {sorted(_TRANSFORM_CHANNELS)}; colors: r,g,b,a'])
        track = native.check(lib.add_widget_animation_keys(wbp, animation_name, widget_name, property_name, index,
                                                           times, values), wbp.get_path_name())
        unreal.BlueprintEditorLibrary.compile_blueprint(wbp)
        return {'track': track, 'keys': len(times), 'status': bpu.status_name(wbp)}

    @agent_tool()
    def inspect_widget_animations(widget_blueprint: str) -> dict:
        """Lists Widget Animations with length, bound widgets and tracks (key counts).

        Args:
            widget_blueprint: Widget Blueprint path.
        """
        wbp = _wbp(widget_blueprint)
        return json.loads(_world_lib().describe_widget_animations(wbp))

    @agent_tool()
    def inspect_widget(widget_blueprint: str, widget_name: str) -> dict:
        """Returns a designer widget's class, slot type and Canvas layout (anchors, offsets, alignment).

        Args:
            widget_blueprint: Widget Blueprint path.
            widget_name: Widget name.
        """
        wbp = _wbp(widget_blueprint)
        widget = _widget(wbp, widget_name)
        slot = widget.get_editor_property('slot')
        info = {'widget': widget_name, 'class': widget.get_class().get_name(),
                'slot': slot.get_class().get_name() if slot else None,
                'visibility': to_jsonable(widget.get_editor_property('visibility')),
                'render_opacity': to_jsonable(widget.get_editor_property('render_opacity')),
                'navigation': _nav_info(widget)}
        try:
            info['is_focusable'] = bool(widget.get_editor_property('is_focusable'))
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        if isinstance(slot, unreal.CanvasPanelSlot):
            info['layout'] = to_jsonable(slot.get_layout())
            info['z_order'] = slot.get_z_order()
            info['auto_size'] = slot.get_auto_size()
        return info

    @agent_tool(mutates=True)
    def set_widget_navigation(widget_blueprint: str, widget_name: str, up: str | None = None, down: str | None = None,
                              left: str | None = None, right: str | None = None, tab_next: str | None = None,
                              tab_previous: str | None = None, focusable: bool | None = None) -> dict:
        """Sets a widget's gamepad/keyboard focus navigation per direction (D-pad / left stick /
        arrow keys; tab_next/tab_previous = Tab / Shift+Tab). Each value is "escape" (default:
        move to the nearest widget), "stop", "wrap", or a widget name to move to explicitly.

        Args:
            widget_blueprint: Widget Blueprint path.
            widget_name: Widget name.
            up: Rule or target widget for Up.
            down: Rule or target widget for Down.
            left: Rule or target widget for Left.
            right: Rule or target widget for Right.
            tab_next: Rule or target widget for Tab.
            tab_previous: Rule or target widget for Shift+Tab.
            focusable: Set is_focusable (Buttons, sliders, check boxes... must be focusable to be navigated to).
        """
        wbp = _wbp(widget_blueprint)
        widget = _widget(wbp, widget_name)
        specs = {'up': up, 'down': down, 'left': left, 'right': right, 'next': tab_next, 'previous': tab_previous}
        rules = {d: _nav_data(wbp, v) for d, v in specs.items() if v}
        if not rules and focusable is None:
            raise ToolError(Code.INVALID_ARGUMENT, 'Pass at least one direction or focusable.')
        if focusable is not None and not _set_focusable(widget, focusable):
            raise ToolError(Code.INVALID_ARGUMENT, f'{widget.get_class().get_name()} has no is_focusable property',
                            target=f'{wbp.get_path_name()}:{widget_name}')
        if rules:
            _set_navigation(widget, rules)
        wbp.modify()
        unreal.BlueprintEditorLibrary.compile_blueprint(wbp)
        return {'widget': widget_name, 'navigation': _nav_info(widget)}

    @agent_tool(mutates=True)
    def setup_gamepad_navigation(widget_blueprint: str, widget_names: str, columns: int = 1, wrap: bool = True,
                                 initial_focus: str | None = None, focus_on_construct: bool = True) -> dict:
        """Makes a menu operable with a controller in one call: the listed widgets (e.g. buttons)
        become focusable and get explicit D-pad/stick/arrow navigation as a list or grid (plus Tab
        order), the Widget Blueprint's Desired Focus is set to the initial widget, and optionally
        Event Construct -> SetFocus is wired so the first button is focused when the menu opens.
        A focused Button is pressed with Gamepad A (FaceButton_Bottom) / Enter by default. Show the
        menu with WidgetBlueprintLibrary:SetInputMode_UIOnlyEx (or GameAndUI) for gamepad input.

        Args:
            widget_blueprint: Widget Blueprint path.
            widget_names: Widgets in reading order, comma-separated, e.g. "ResumeButton,OptionsButton,QuitButton".
            columns: 1 = vertical list, N = grid with N columns (use the widget count for a horizontal row).
            wrap: Wrap around at the edges (otherwise focus stops).
            initial_focus: Widget focused first (default: the first listed widget).
            focus_on_construct: Wire Event Construct -> SetFocus in the Widget Blueprint's event graph.
        """
        wbp = _wbp(widget_blueprint)
        names = split_csv(widget_names)
        if not names or len(set(names)) != len(names):
            raise ToolError(Code.INVALID_ARGUMENT, 'widget_names must list one or more distinct widgets')
        if columns < 1:
            raise ToolError(Code.INVALID_ARGUMENT, 'columns must be >= 1')
        initial = initial_focus or names[0]
        widgets = [_widget(wbp, n) for n in names]
        if initial not in names:
            _widget(wbp, initial)
        not_focusable = [n for n, w in zip(names, widgets) if not _set_focusable(w, True)]
        count = len(names)
        rows = (count + columns - 1) // columns
        edge = 'wrap' if wrap else 'stop'

        def target(i: int, dr: int, dc: int) -> str:
            r, c = divmod(i, columns)
            r2, c2 = r + dr, c + dc
            if dc:
                row_len = min(columns, count - r * columns)
                if not 0 <= c2 < row_len:
                    if not wrap:
                        return edge
                    c2 %= row_len
            if dr:
                col_rows = [rr for rr in range(rows) if rr * columns + c < count]
                if r2 not in col_rows:
                    if not wrap:
                        return edge
                    r2 = col_rows[0] if dr > 0 else col_rows[-1]
            j = r2 * columns + c2
            return names[j] if j != i else 'stop'

        result = {}
        for i, (name, widget) in enumerate(zip(names, widgets)):
            specs = {'up': target(i, -1, 0), 'down': target(i, 1, 0), 'left': target(i, 0, -1),
                     'right': target(i, 0, 1),
                     'next': names[(i + 1) % count] if wrap or i + 1 < count else 'stop',
                     'previous': names[(i - 1) % count] if wrap or i > 0 else 'stop'}
            specs = {d: 'stop' if v == name else v for d, v in specs.items()}
            _set_navigation(widget, {d: _nav_data(wbp, v) for d, v in specs.items()})
            result[name] = _nav_info(widget)
        cdo = unreal.get_default_object(wbp.generated_class())
        focus = unreal.WidgetChild()
        focus.set_editor_property('widget_name', unreal.Name(initial))
        cdo.set_editor_property('desired_focus_widget', focus)
        cdo.set_editor_property('is_focusable', True)
        wired = _wire_focus_on_construct(wbp) if focus_on_construct else 'skipped'
        wbp.modify()
        report = bpu.compile_report(wbp, compile_first=True)
        if report['status'] == 'error':
            raise ToolError(Code.COMPILE_FAILED, 'Widget Blueprint failed to compile', target=wbp.get_path_name(),
                            likely_causes=[e['message'] for e in report['errors'][:5]], details=report)
        if not_focusable:
            ctx().warn(f'No is_focusable property on {not_focusable}; use Buttons or other focusable widgets.',
                       'NOT_FOCUSABLE')
        return {'navigation': result, 'initial_focus': initial, 'focus_on_construct': wired,
                'status': report['status']}
