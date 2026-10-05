"""Resolution of user/agent supplied identifiers into Unreal objects.

All resolvers raise ToolError with helpful candidates instead of returning None,
so agents can self-correct (e.g. a typo in an asset path yields similar assets).
"""

from __future__ import annotations

import re

import unreal

from . import editor
from .errors import Code, ToolError

_TYPED_PATH = re.compile(r"^\w+'(.*)'$")


def normalize_asset_path(path: str) -> tuple[str, str]:
    """Returns (package_name, object_path) for many accepted spellings.

    Accepts '/Game/A/B', '/Game/A/B.B', "Blueprint'/Game/A/B.B'", '/Game/A/B.B_C'
    (generated class) and paths with a trailing slash/whitespace.
    """
    if not isinstance(path, str) or not path.strip():
        raise ToolError(Code.INVALID_ARGUMENT, 'Asset path is empty.')
    p = path.strip().rstrip('/')
    m = _TYPED_PATH.match(p)
    if m:
        p = m.group(1)
    if not p.startswith('/'):
        raise ToolError(Code.INVALID_ARGUMENT,
                        f'Asset path must start with "/" (e.g. /Game/Folder/Asset): {path!r}',
                        target=path,
                        likely_causes=['Use a long package path such as /Game/Blueprints/BP_Player'])
    if ':' in p:  # subobject path, keep the asset part
        p = p.split(':', 1)[0]
    if '.' in p.rsplit('/', 1)[-1]:
        package, obj = p.rsplit('.', 1)
        if obj.endswith('_C'):
            obj = obj[:-2]
        return package, f'{package}.{obj}'
    name = p.rsplit('/', 1)[-1]
    return p, f'{p}.{name}'


def package_of(path: str) -> str:
    return normalize_asset_path(path)[0]


def similar_assets(path: str, limit: int = 5) -> list[str]:
    """Finds assets whose name resembles the last segment of path."""
    try:
        package, _ = normalize_asset_path(path)
    except ToolError:
        return []
    name = package.rsplit('/', 1)[-1].lower()
    root = '/' + package.strip('/').split('/', 1)[0]
    results: list[str] = []
    try:
        assets = editor.asset_registry().get_assets_by_path(root, recursive=True) or []
    except Exception:  # pylint: disable=broad-exception-caught
        return results
    key = name.replace('_', '')
    for a in assets:
        an = str(a.asset_name).lower()
        if name in an or an in name or (key and key in an.replace('_', '')):
            results.append(str(a.package_name))
            if len(results) >= limit:
                break
    return results


def asset_exists(path: str) -> bool:
    package, _ = normalize_asset_path(path)
    return bool(editor.asset_subsystem().does_asset_exist(package))


def load_asset(path: str, expected: type | tuple[type, ...] | None = None) -> unreal.Object:
    """Loads an asset or raises ToolError(ASSET_NOT_FOUND / WRONG_TYPE)."""
    package, object_path = normalize_asset_path(path)
    asset = None
    if editor.asset_subsystem().does_asset_exist(package):
        asset = unreal.load_asset(object_path) or editor.asset_subsystem().load_asset(package)
    if asset is None:
        similar = similar_assets(path)
        causes = ['Check spelling and folder; use search_assets to find the right path.']
        if similar:
            causes.insert(0, f'Similar assets: {similar}')
        raise ToolError(Code.ASSET_NOT_FOUND, f'Asset not found: {package}', target=package,
                        likely_causes=causes)
    if expected is not None and not isinstance(asset, expected):
        names = [t.__name__ for t in (expected if isinstance(expected, tuple) else (expected,))]
        raise ToolError(Code.WRONG_TYPE,
                        f'{package} is a {asset.get_class().get_name()}, expected {names}',
                        target=package)
    return asset


def asset_data(path: str) -> unreal.AssetData:
    """Asset registry data for path or ToolError."""
    _, object_path = normalize_asset_path(path)
    data = editor.asset_registry().k2_get_asset_by_object_path(unreal.SoftObjectPath(object_path))
    if not data or not data.is_valid():
        raise ToolError(Code.ASSET_NOT_FOUND, f'Asset not found in asset registry: {path}', target=path,
                        likely_causes=[f'Similar assets: {similar_assets(path)}'])
    return data


def resolve_class(name: str, base: type | None = None) -> unreal.Class:
    """Resolves a class from a native name ('StaticMeshActor'), script path
    ('/Script/Engine.StaticMeshActor') or Blueprint asset path ('/Game/BP_X').
    """
    if not name or not isinstance(name, str):
        raise ToolError(Code.INVALID_ARGUMENT, 'Class name is empty.')
    n = name.strip()
    m = _TYPED_PATH.match(n)
    if m:
        n = m.group(1)
    cls = None
    if n.startswith('/Script/'):
        cls = unreal.load_class(None, n)
    elif n.startswith('/'):
        package, object_path = normalize_asset_path(n)
        asset = unreal.load_asset(object_path) if editor.asset_subsystem().does_asset_exist(package) else None
        if isinstance(asset, unreal.Blueprint):
            cls = asset.generated_class()
        elif isinstance(asset, unreal.Class):
            cls = asset
        if cls is None:
            cls = unreal.load_class(None, f'{object_path}_C')
    else:
        py_type = getattr(unreal, n, None)
        if py_type is None and n[:1] in ('A', 'U') and n[1:2].isupper():
            py_type = getattr(unreal, n[1:], None)
        if isinstance(py_type, type) and hasattr(py_type, 'static_class'):
            try:
                cls = py_type.static_class()
            except Exception:  # pylint: disable=broad-exception-caught
                cls = None
        if cls is None:
            for module in ('Engine', 'CoreUObject', 'UMG', 'AIModule', 'EnhancedInput',
                           'Niagara', 'GameplayTasks', 'NavigationSystem'):
                cls = unreal.load_class(None, f'/Script/{module}.{n}')
                if cls:
                    break
    if cls is None:
        raise ToolError(Code.CLASS_NOT_FOUND, f'Class not found: {name}', target=name,
                        likely_causes=['Use a native class name (e.g. "Character", "StaticMeshActor"), '
                                       'a script path ("/Script/Engine.Character") or a Blueprint asset path.'])
    if base is not None and not is_child_of(cls, base):
        raise ToolError(Code.WRONG_TYPE, f'{cls.get_name()} is not a subclass of {base.__name__}', target=name)
    return cls


def is_child_of(cls: unreal.Class, base: type | unreal.Class) -> bool:
    base_cls = base.static_class() if isinstance(base, type) else base
    return bool(unreal.MathLibrary.class_is_child_of(cls, base_cls))


def all_level_actors() -> list[unreal.Actor]:
    return list(editor.actor_subsystem().get_all_level_actors() or [])


def find_actor(identifier: str) -> unreal.Actor:
    """Finds a level actor by label, object name or full path. Raises on missing/ambiguous."""
    if not identifier:
        raise ToolError(Code.INVALID_ARGUMENT, 'Actor identifier is empty.')
    actors = all_level_actors()
    exact_path = [a for a in actors if a.get_path_name() == identifier]
    if exact_path:
        return exact_path[0]
    by_name = [a for a in actors if a.get_name() == identifier]
    if len(by_name) == 1:
        return by_name[0]
    by_label = [a for a in actors if a.get_actor_label() == identifier]
    if len(by_label) == 1:
        return by_label[0]
    if len(by_label) > 1:
        raise ToolError(Code.AMBIGUOUS, f'{len(by_label)} actors are labeled {identifier!r}',
                        target=identifier,
                        likely_causes=['Use the object name or full path instead: ' +
                                       ', '.join(a.get_name() for a in by_label[:10])])
    lowered = identifier.lower()
    similar = [a.get_actor_label() for a in actors if lowered in a.get_actor_label().lower()][:10]
    raise ToolError(Code.ACTOR_NOT_FOUND, f'Actor not found in current level: {identifier}',
                    target=identifier,
                    likely_causes=[f'Similar actor labels: {similar}' if similar else
                                   'Use inspect_level or SceneTools.find_actors to list actors.'])


def find_actors(identifiers: list[str]) -> list[unreal.Actor]:
    return [find_actor(i) for i in identifiers]


def find_component(actor: unreal.Actor, name: str) -> unreal.ActorComponent:
    comps = list(actor.get_components_by_class(unreal.ActorComponent) or [])
    for c in comps:
        if c.get_name() == name:
            return c
    lowered = name.lower()
    matches = [c for c in comps if c.get_name().lower() == lowered]
    if matches:
        return matches[0]
    raise ToolError(Code.OBJECT_NOT_FOUND, f'Component {name!r} not found on {actor.get_actor_label()}',
                    target=actor.get_path_name(),
                    likely_causes=[f'Available components: {[c.get_name() for c in comps]}'])


def load_object(path: str) -> unreal.Object:
    """Loads any object by path (asset, subobject, or actor label/path in the editor world)."""
    obj = None
    if path.startswith('/'):
        obj = unreal.find_object(None, path) or unreal.load_object(None, path)
    if obj is not None:
        return obj
    try:
        return find_actor(path)
    except ToolError:
        pass
    if path.startswith('/'):
        try:
            return load_asset(path)
        except ToolError:
            pass
    raise ToolError(Code.OBJECT_NOT_FOUND, f'Object not found: {path}', target=path,
                    likely_causes=['Pass an asset path (/Game/...), a full object path, or an actor label.'])


def super_class(cls: unreal.Class) -> unreal.Class | None:
    getter = getattr(cls, 'get_super_class', None)
    if getter is not None:
        try:
            return getter()
        except Exception:  # pylint: disable=broad-exception-caught
            return None
    return None


def class_parent_chain(cls: unreal.Class, limit: int = 30) -> list[str]:
    """Names of the ancestors of cls, nearest first (falls back to the Python MRO)."""
    chain: list[str] = []
    current = super_class(cls)
    while current is not None and len(chain) < limit:
        chain.append(current.get_name())
        current = super_class(current)
    if chain:
        return chain
    get_type = getattr(unreal, 'get_type_from_class', None)
    if get_type is None:
        return chain
    try:
        py_type = get_type(cls)
    except Exception:  # pylint: disable=broad-exception-caught
        return chain
    for base in py_type.__mro__[1:]:
        if base.__name__.startswith('_') or not hasattr(base, 'static_class'):
            continue
        chain.append(base.__name__)
    return chain[:limit]
