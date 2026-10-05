"""UMGTools: widget layout (Canvas slot anchors/position/size/alignment), widget properties by
name and Widget Animations (keyframed opacity / render transform / color).

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


@unreal.uclass()
class UMGTools(unreal.ToolsetDefinition):
    """UMG helpers: Canvas Panel slot layout (anchor presets, position, size, alignment, z-order,
    auto size), widget properties by name, and Widget Animations with keyframes."""

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
                'render_opacity': to_jsonable(widget.get_editor_property('render_opacity'))}
        if isinstance(slot, unreal.CanvasPanelSlot):
            info['layout'] = to_jsonable(slot.get_layout())
            info['z_order'] = slot.get_z_order()
            info['auto_size'] = slot.get_auto_size()
        return info
