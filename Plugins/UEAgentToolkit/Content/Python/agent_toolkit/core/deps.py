"""Asset registry queries: dependencies, referencers, folder listings, existence checks."""

from __future__ import annotations

import unreal

from . import editor

_SCRIPT_PREFIXES = ('/Script/', '/Engine/', '/Temp/', '/Memory/')


def _options(hard: bool = True, soft: bool = True) -> unreal.AssetRegistryDependencyOptions:
    opts = unreal.AssetRegistryDependencyOptions()
    opts.include_hard_package_references = hard
    opts.include_soft_package_references = soft
    opts.include_searchable_names = False
    opts.include_soft_management_references = False
    opts.include_hard_management_references = False
    return opts


def dependencies(package: str, hard: bool = True, soft: bool = True) -> list[str]:
    deps = editor.asset_registry().get_dependencies(package, _options(hard, soft)) or []
    return sorted({str(d) for d in deps})


def referencers(package: str, hard: bool = True, soft: bool = True) -> list[str]:
    refs = editor.asset_registry().get_referencers(package, _options(hard, soft)) or []
    return sorted({str(r) for r in refs if str(r) != package})


def is_script_package(package: str) -> bool:
    return package.startswith('/Script/')


def package_exists(package: str) -> bool:
    """True if the package is a script package or exists on disk / in the registry."""
    if is_script_package(package):
        return True
    try:
        if editor.asset_registry().get_assets_by_package_name(package):
            return True
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    return bool(unreal.EditorAssetLibrary.does_asset_exist(package))


def assets_in_path(path: str, recursive: bool = True, class_names: list[str] | None = None) -> list[unreal.AssetData]:
    assets = list(editor.asset_registry().get_assets_by_path(path, recursive=recursive) or [])
    if class_names:
        wanted = {c.lower() for c in class_names}
        assets = [a for a in assets if str(a.asset_class_path.asset_name).lower() in wanted]
    return assets


def asset_class_name(data: unreal.AssetData) -> str:
    try:
        return str(data.asset_class_path.asset_name)
    except Exception:  # pylint: disable=broad-exception-caught
        return str(data.asset_class)


def missing_dependencies(package: str) -> list[str]:
    """Dependencies of package that do not exist (broken references)."""
    return [d for d in dependencies(package) if not d.startswith(_SCRIPT_PREFIXES[1:]) and not package_exists(d)]


def tree(package: str, direction: str, depth: int, limit: int) -> dict:
    """Breadth-first dependency/referencer tree with class + existence for each node."""
    fetch = dependencies if direction == 'dependencies' else referencers
    seen: dict[str, dict] = {}
    frontier = [package]
    edges: list[list[str]] = []
    for level in range(max(1, depth)):
        nxt = []
        for p in frontier:
            for child in fetch(p):
                edges.append([p, child])
                if child in seen or child == package:
                    continue
                if len(seen) >= limit:
                    break
                info: dict = {'depth': level + 1, 'exists': package_exists(child)}
                if not is_script_package(child):
                    data = editor.asset_registry().get_assets_by_package_name(child) or []
                    if data:
                        info['class'] = asset_class_name(data[0])
                else:
                    info['class'] = 'script'
                seen[child] = info
                nxt.append(child)
        frontier = [p for p in nxt if not is_script_package(p)]
        if not frontier:
            break
    return {'root': package, 'direction': direction, 'nodes': seen, 'edges': edges[:limit * 2],
            'truncated': len(seen) >= limit,
            'missing': sorted(p for p, i in seen.items() if not i['exists'])}
