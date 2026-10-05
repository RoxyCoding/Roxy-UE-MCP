"""File-level backups of asset packages (Saved/AgentToolkit/Backups/<id>/...).

Backups copy the on-disk .uasset/.umap (+ .uexp/.ubulk side files). Unsaved in-memory
edits are not part of a backup; save first if they must be captured.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import time

import unreal

from . import editor
from .errors import Code, ToolError

_SIDE_EXTS = ('.uexp', '.ubulk', '.uptnl')


def _backup_root() -> str:
    return editor.toolkit_saved_dir('Backups')


def _manifest_path(backup_id: str) -> str:
    return os.path.join(_backup_root(), backup_id, 'manifest.json')


def create(packages: list[str], label: str = '') -> dict:
    backup_id = time.strftime('%Y%m%d-%H%M%S') + f'-{int(time.time() * 1000) % 1000:03d}'
    root = os.path.join(_backup_root(), backup_id)
    os.makedirs(root, exist_ok=True)
    entries, skipped = [], []
    project = editor.project_dir()
    for package in sorted(set(packages)):
        src = editor.package_filename(package)
        if not src:
            skipped.append({'package': package, 'reason': 'no file on disk (never saved?)'})
            continue
        files = [src] + [os.path.splitext(src)[0] + ext for ext in _SIDE_EXTS
                         if os.path.isfile(os.path.splitext(src)[0] + ext)]
        copied = []
        for f in files:
            rel = os.path.relpath(f, project) if f.startswith(project) else os.path.basename(f)
            dst = os.path.join(root, 'files', rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(f, dst)
            copied.append({'original': f, 'backup': dst})
        entries.append({'package': package, 'files': copied})
    manifest = {'id': backup_id, 'label': label, 'time': time.strftime('%Y-%m-%dT%H:%M:%S'),
                'entries': entries, 'skipped': skipped}
    with open(_manifest_path(backup_id), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=1, ensure_ascii=False)
    return manifest


def list_all(limit: int = 50) -> list[dict]:
    out = []
    for path in sorted(glob.glob(os.path.join(_backup_root(), '*', 'manifest.json')), reverse=True)[:limit]:
        try:
            with open(path, encoding='utf-8') as f:
                m = json.load(f)
            out.append({'id': m['id'], 'label': m.get('label', ''), 'time': m.get('time'),
                        'packages': [e['package'] for e in m.get('entries', [])]})
        except (OSError, ValueError, KeyError):
            continue
    return out


def load(backup_id: str) -> dict:
    try:
        with open(_manifest_path(backup_id), encoding='utf-8') as f:
            return json.load(f)
    except OSError as e:
        raise ToolError(Code.OBJECT_NOT_FOUND, f'Backup {backup_id!r} not found', target=backup_id,
                        likely_causes=['Use list_backups to get valid ids.']) from e


def restore(backup_id: str, packages: list[str] | None = None) -> dict:
    """Copies backed-up files over the originals, rescans and reloads loaded packages."""
    manifest = load(backup_id)
    wanted = set(packages or [])
    world = editor.editor_world()
    current_map = world.get_outermost().get_name() if world else ''
    restored, skipped, files = [], [], []
    for entry in manifest['entries']:
        package = entry['package']
        if wanted and package not in wanted:
            continue
        if package == current_map:
            skipped.append({'package': package, 'reason': 'is the currently open level; load another level first'})
            continue
        for f in entry['files']:
            os.makedirs(os.path.dirname(f['original']), exist_ok=True)
            shutil.copy2(f['backup'], f['original'])
            files.append(f['original'])
        restored.append(package)
    if files:
        editor.asset_registry().scan_files_synchronous([f for f in files if f.endswith(('.uasset', '.umap'))], True)
    loaded = [unreal.find_package(p) if hasattr(unreal, 'find_package') else None for p in restored]
    loaded = [p for p in loaded if p]
    reload_error = ''
    if loaded:
        try:
            _, err = unreal.EditorLoadingAndSavingUtils.reload_packages(
                loaded, unreal.ReloadPackagesInteractionMode.ASSUME_POSITIVE)
            reload_error = str(err) if err else ''
        except Exception as e:  # pylint: disable=broad-exception-caught
            reload_error = str(e)
    return {'backup_id': backup_id, 'restored': restored, 'skipped': skipped, 'reload_error': reload_error}
