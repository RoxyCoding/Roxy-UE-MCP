"""Thin accessors for editor subsystems and common editor state queries."""

from __future__ import annotations

import os

import unreal


def asset_subsystem() -> unreal.EditorAssetSubsystem:
    return unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)


def actor_subsystem() -> unreal.EditorActorSubsystem:
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def level_subsystem() -> unreal.LevelEditorSubsystem:
    return unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)


def editor_subsystem() -> unreal.UnrealEditorSubsystem:
    return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)


def asset_registry() -> unreal.AssetRegistry:
    return unreal.AssetRegistryHelpers.get_asset_registry()


def asset_tools() -> unreal.AssetTools:
    return unreal.AssetToolsHelpers.get_asset_tools()


def editor_world() -> unreal.World | None:
    return editor_subsystem().get_editor_world()


def is_pie_running() -> bool:
    # LevelEditorSubsystem.is_in_play_in_editor crashes without the Level Editor UI
    # (commandlets), so detect PIE through the game world instead.
    try:
        return editor_subsystem().get_game_world() is not None
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def is_commandlet() -> bool:
    """True when running headless (no Level Editor UI); some editor APIs are unsafe there."""
    try:
        return '-run=' in unreal.SystemLibrary.get_command_line().lower()
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def dirty_package_names() -> set[str]:
    """Names of all unsaved (dirty) content and map packages."""
    names: set[str] = set()
    for getter in (unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages,
                   unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages):
        try:
            for pkg in getter():
                if pkg:
                    names.add(pkg.get_name())
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    return names


def project_dir() -> str:
    return os.path.abspath(unreal.Paths.project_dir())


def saved_dir() -> str:
    return os.path.abspath(unreal.Paths.project_saved_dir())


def toolkit_saved_dir(*parts: str) -> str:
    """Directory under Saved/AgentToolkit used for journals, backups and log marks."""
    path = os.path.join(saved_dir(), 'AgentToolkit', *parts)
    os.makedirs(path, exist_ok=True)
    return path


def package_filename(package_name: str) -> str | None:
    """Absolute on-disk file for a long package name, or None if unknown."""
    try:
        base = unreal.PackageTools.package_name_to_filename(package_name)
    except Exception:  # pylint: disable=broad-exception-caught
        base = ''
    if base:
        base = os.path.abspath(base)
        for candidate in (base, base + '.uasset', base + '.umap', os.path.splitext(base)[0] + '.uasset',
                          os.path.splitext(base)[0] + '.umap'):
            if os.path.isfile(candidate):
                return candidate
    # Fallback: map well-known roots.
    if package_name.startswith('/Game/'):
        base = os.path.join(project_dir(), 'Content', *package_name[len('/Game/'):].split('/'))
        for ext in ('.uasset', '.umap'):
            if os.path.isfile(base + ext):
                return base + ext
    return None
