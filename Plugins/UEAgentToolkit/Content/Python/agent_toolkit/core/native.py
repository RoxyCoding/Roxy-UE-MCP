"""Access to the optional C++ helper library (UEAgentToolkitNative module).

Python covers almost everything; the C++ module only fills gaps the Python API
cannot reach (Blueprint interfaces, macro graphs, full compiler/message logs).
When the module is not compiled, callers get None and must degrade gracefully.
"""

from __future__ import annotations

import unreal

from .errors import Code, ToolError


def library():
    """The AgentToolkitNativeLibrary class, or None when the C++ module is not built."""
    return getattr(unreal, 'AgentToolkitNativeLibrary', None)


def require(feature: str):
    lib = library()
    if lib is None:
        raise ToolError(Code.NOT_SUPPORTED,
                        f'{feature} requires the UEAgentToolkitNative C++ plugin, which is not loaded.',
                        likely_causes=['Enable the UEAgentToolkitNative plugin (prebuilt for Win64, or run '
                                       'Plugins/UEAgentToolkitNative/build.sh) and restart the editor.'])
    return lib


def graph_library():
    """The AgentToolkitGraphLibrary class (BT / AnimBP / Blackboard / montage / blend space helpers) or None."""
    return getattr(unreal, 'AgentToolkitGraphLibrary', None)


def require_graph(feature: str):
    lib = graph_library()
    if lib is None:
        require(feature)  # raises the standard NOT_SUPPORTED error
    return lib


def flatten_properties(props: dict, prefix: str = '') -> list[tuple[str, str]]:
    """{"A": {"B": 1}, "C": true} -> [("A.B", "1"), ("C", "True")] for set_property_from_text."""
    out = []
    for key, value in props.items():
        path = f'{prefix}.{key}' if prefix else key
        if isinstance(value, dict):
            out += flatten_properties(value, path)
        elif isinstance(value, bool):
            out.append((path, 'True' if value else 'False'))
        else:
            out.append((path, str(value)))
    return out


def set_properties_from_json(obj, props: dict, target: str = '') -> list[str]:
    """Sets reflected properties (C++ names, dotted struct paths) via Unreal text import."""
    lib = getattr(unreal, 'AgentToolkitWorldLibrary', None)
    if lib is None:
        require('Generic property editing')
    applied = []
    for path, text in flatten_properties(props):
        check(lib.set_property_from_text(obj, path, text), target, Code.INVALID_ARGUMENT)
        applied.append(path)
    return applied


def check(result: str, target: str = '', code: str = Code.UE_OPERATION_FAILED) -> str:
    """Raises ToolError for native 'ERROR: ...' results, otherwise returns the result."""
    text = str(result or '')
    if text.startswith('ERROR: '):
        message = text[len('ERROR: '):]
        lowered = message.lower()
        if 'not found' in lowered or ' has no ' in lowered or 'does not exist' in lowered:
            code = Code.OBJECT_NOT_FOUND
        elif 'already' in lowered:
            code = Code.ALREADY_EXISTS
        elif any(w in lowered for w in ('must', 'required', 'not a ', 'invalid', 'outside', 'incompatible', 'needs',
                                         'mismatch', 'unsupported', 'not connected')):
            code = Code.INVALID_ARGUMENT
        raise ToolError(code, message, target)
    return text
