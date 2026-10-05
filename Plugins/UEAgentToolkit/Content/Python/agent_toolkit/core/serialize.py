"""Conversion of Unreal values into JSON-friendly Python values."""

from __future__ import annotations

import json
from typing import Any

import unreal

_MAX_DEPTH = 64  # deep enough for nested trees (BT, widget, dependency graphs)


def _round(v: float) -> float:
    return round(float(v), 4)


def to_jsonable(value: Any, depth: int = 0) -> Any:
    """Converts common Unreal types into JSON-serializable values.

    Objects become their path name, math structs become compact dicts/lists,
    enums become their name and containers are converted recursively.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return _round(value)
    if depth > _MAX_DEPTH:
        return str(value)
    if isinstance(value, (unreal.Name, unreal.Text)):
        return str(value)
    if isinstance(value, unreal.Vector):
        return [_round(value.x), _round(value.y), _round(value.z)]
    if isinstance(value, unreal.Vector2D):
        return [_round(value.x), _round(value.y)]
    if isinstance(value, unreal.Vector4):
        return [_round(value.x), _round(value.y), _round(value.z), _round(value.w)]
    if isinstance(value, unreal.IntPoint):
        return [value.x, value.y]
    if isinstance(value, unreal.Rotator):
        return {'pitch': _round(value.pitch), 'yaw': _round(value.yaw), 'roll': _round(value.roll)}
    if isinstance(value, unreal.Quat):
        return to_jsonable(value.rotator(), depth + 1)
    if isinstance(value, unreal.Transform):
        return {
            'location': to_jsonable(value.translation, depth + 1),
            'rotation': to_jsonable(value.rotation.rotator(), depth + 1),
            'scale': to_jsonable(value.scale3d, depth + 1),
        }
    if isinstance(value, unreal.LinearColor):
        return {'r': _round(value.r), 'g': _round(value.g), 'b': _round(value.b), 'a': _round(value.a)}
    if isinstance(value, unreal.Color):
        return {'r': value.r, 'g': value.g, 'b': value.b, 'a': value.a}
    if isinstance(value, unreal.SoftObjectPath):
        return str(value.export_text())
    if isinstance(value, unreal.Key):
        return str(value.get_editor_property('key_name'))
    if isinstance(value, unreal.EnumBase):
        return value.name
    if isinstance(value, unreal.Class):
        return value.get_path_name()
    if isinstance(value, unreal.Object):
        return value.get_path_name()
    if isinstance(value, unreal.StructBase):
        try:
            return {str(k): to_jsonable(v, depth + 1) for k, v in value.to_dict().items()}
        except Exception:  # pylint: disable=broad-exception-caught
            return str(value.export_text())
    if isinstance(value, (list, tuple, set, unreal.Array, unreal.Set)):
        return [to_jsonable(v, depth + 1) for v in value]
    if isinstance(value, (dict, unreal.Map)):
        return {str(to_jsonable(k, depth + 1)): to_jsonable(v, depth + 1) for k, v in value.items()}
    return str(value)


def dumps(value: Any) -> str:
    """Serializes a payload compactly for details_json."""
    return json.dumps(to_jsonable(value), ensure_ascii=False, separators=(',', ':'), default=str)


def parse_json_arg(text: str, arg_name: str, expected: type | tuple[type, ...] = (dict, list)) -> Any:
    """Parses a JSON string tool argument with a descriptive ToolError on failure."""
    # pylint: disable-next=import-outside-toplevel
    from .errors import Code, ToolError
    if text is None or text == '':
        return expected() if isinstance(expected, type) else expected[0]()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as e:
        raise ToolError(Code.INVALID_ARGUMENT, f'{arg_name} is not valid JSON: {e}',
                        likely_causes=[f'Pass {arg_name} as a JSON string, e.g. {{"key": 1}}']) from e
    if not isinstance(value, expected):
        raise ToolError(Code.INVALID_ARGUMENT, f'{arg_name} must be JSON of type {expected}, got {type(value).__name__}')
    return value


def _vec(v: Any, cls: type, fields: tuple[str, ...]) -> Any:
    if isinstance(v, dict):
        return cls(*[float(v.get(f, 0.0)) for f in fields])
    if isinstance(v, (list, tuple)) and len(v) == len(fields):
        return cls(*[float(x) for x in v])
    raise ValueError(f'expected list of {len(fields)} numbers or dict with {fields}')


def from_jsonable(value: Any, current: Any, hint: str = '') -> Any:
    """Converts a JSON value into the Unreal type of `current` (the existing property value).

    Supports bool/int/float/str, Name/Text, Vector/Vector2D/Rotator/LinearColor/Color,
    Transform, enums (by name), object references (asset path or None) and arrays of these.
    `hint` is used for error messages only.
    """
    if value is None:
        return None
    if isinstance(current, unreal.Class) and isinstance(value, str):
        # pylint: disable-next=import-outside-toplevel
        from .resolve import resolve_class
        return resolve_class(value)
    if isinstance(current, bool):
        return bool(value)
    if isinstance(current, int) and not isinstance(current, unreal.EnumBase):
        return int(value)
    if isinstance(current, float):
        return float(value)
    if isinstance(current, unreal.Name):
        return unreal.Name(str(value))
    if isinstance(current, unreal.Text):
        return unreal.Text(str(value))
    if isinstance(current, str):
        return str(value)
    if isinstance(current, unreal.Vector):
        return _vec(value, unreal.Vector, ('x', 'y', 'z'))
    if isinstance(current, unreal.Vector2D):
        return _vec(value, unreal.Vector2D, ('x', 'y'))
    if isinstance(current, unreal.Rotator):
        if isinstance(value, dict):
            return unreal.Rotator(roll=float(value.get('roll', 0)), pitch=float(value.get('pitch', 0)),
                                  yaw=float(value.get('yaw', 0)))
        # list form is [pitch, yaw, roll] (matches to_jsonable dict order)
        return unreal.Rotator(roll=float(value[2]), pitch=float(value[0]), yaw=float(value[1]))
    if isinstance(current, unreal.LinearColor):
        if isinstance(value, dict):
            return unreal.LinearColor(float(value.get('r', 0)), float(value.get('g', 0)),
                                      float(value.get('b', 0)), float(value.get('a', 1)))
        vals = [float(x) for x in value] + [1.0] * (4 - len(value))
        return unreal.LinearColor(*vals[:4])
    if isinstance(current, unreal.Color):
        if isinstance(value, dict):
            return unreal.Color(int(value.get('r', 0)), int(value.get('g', 0)), int(value.get('b', 0)), int(value.get('a', 255)))
        vals = [int(x) for x in value] + [255] * (4 - len(value))
        return unreal.Color(*vals[:4])
    if isinstance(current, unreal.Transform):
        t = unreal.Transform()
        if 'location' in value:
            t.translation = _vec(value['location'], unreal.Vector, ('x', 'y', 'z'))
        if 'rotation' in value:
            t.rotation = from_jsonable(value['rotation'], unreal.Rotator(), hint).quaternion()
        if 'scale' in value:
            t.scale3d = _vec(value['scale'], unreal.Vector, ('x', 'y', 'z'))
        return t
    if isinstance(current, unreal.EnumBase):
        enum_cls = type(current)
        name = str(value).upper()
        for member_name in dir(enum_cls):
            if member_name.upper() == name or member_name.upper().endswith('_' + name):
                return getattr(enum_cls, member_name)
        raise ValueError(f'{value!r} is not a valid {enum_cls.__name__} '
                         f'(valid: {[m for m in dir(enum_cls) if m.isupper()]})')
    if isinstance(current, unreal.StructBase):
        if isinstance(value, (bool, int, float)):
            # Value wrappers such as FValueOrBBKey_Float expose the literal as default_value/value.
            new = type(current)()
            for field in ('default_value', 'value'):
                try:
                    inner = new.get_editor_property(field)
                except Exception:  # pylint: disable=broad-exception-caught
                    continue
                new.set_editor_property(field, from_jsonable(value, inner, f'{hint}.{field}'))
                return new
            raise ValueError(f'{hint}: cannot assign {value!r} to struct {type(current).__name__}')
        if isinstance(value, str):
            new = type(current)()
            new.import_text(value)
            return new
        new = type(current)()
        for k, v in value.items():
            new.set_editor_property(k, from_jsonable(v, new.get_editor_property(k), f'{hint}.{k}'))
        return new
    if isinstance(current, (unreal.Array, list)):
        template = current[0] if len(current) else None
        return [from_jsonable(v, template, hint) if template is not None else v for v in value]
    # Object references (current may be None): load by path.
    if isinstance(value, str) and value.startswith('/'):
        obj = unreal.load_object(None, value) or unreal.load_asset(value)
        if obj is None:
            raise ValueError(f'object/asset {value!r} could not be loaded for {hint}')
        return obj
    return value


def split_csv(text: str | None) -> list[str]:
    """Splits an optional comma-separated tool argument (optional list params are not
    supported by the ToolsetRegistry JSON converter, so optional multi-values use CSV)."""
    if not text:
        return []
    return [part.strip() for part in text.split(',') if part.strip()]
