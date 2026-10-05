"""Minimal, conservative editing of project Config/Default*.ini files.

Only the given keys inside the given section are touched; all other lines are kept
verbatim. Every write backs up the original file first (Saved/AgentToolkit/Backups/config-*).
"""

from __future__ import annotations

import os
import shutil
import time

from . import editor


def ini_path(name: str) -> str:
    """Absolute path of Config/<name>.ini in the project (e.g. 'DefaultEngine')."""
    return os.path.join(editor.project_dir(), 'Config', f'{name}.ini')


def read_section(name: str, section: str) -> list[str]:
    path = ini_path(name)
    if not os.path.isfile(path):
        return []
    lines, inside = [], False
    with open(path, encoding='utf-8-sig') as f:
        for raw in f:
            line = raw.rstrip('\r\n')
            if line.startswith('['):
                inside = line.strip() == f'[{section}]'
                continue
            if inside and line.strip():
                lines.append(line)
    return lines


def set_values(name: str, section: str, values: dict[str, str | list[str]], dry_run: bool = False) -> dict:
    """Sets keys in a section. A list value writes `+Key=` array entries (replacing existing
    `Key`/`+Key`/`-Key`/`!Key` lines for that key). Returns the before/after lines."""
    path = ini_path(name)
    original = []
    if os.path.isfile(path):
        with open(path, encoding='utf-8-sig') as f:
            original = [l.rstrip('\r\n') for l in f]

    def key_of(line: str) -> str:
        return line.split('=', 1)[0].lstrip('+-.!').strip()

    new_entries = []
    for key, value in values.items():
        if isinstance(value, list):
            new_entries.append(f'!{key}=ClearArray')
            new_entries += [f'+{key}={v}' for v in value]
        else:
            new_entries.append(f'{key}={value}')

    out, inside, found, before = [], False, False, []
    for line in original:
        if line.startswith('['):
            if inside:
                out.extend(new_entries)
            inside = line.strip() == f'[{section}]'
            found = found or inside
            out.append(line)
            continue
        if inside and '=' in line and key_of(line) in values:
            before.append(line)
            continue
        out.append(line)
    if inside:
        out.extend(new_entries)
    if not found:
        if out and out[-1].strip():
            out.append('')
        out.append(f'[{section}]')
        out.extend(new_entries)

    result = {'file': path, 'section': section, 'removed_lines': before, 'added_lines': new_entries}
    if dry_run:
        return result
    if os.path.isfile(path):
        backup_dir = editor.toolkit_saved_dir('Backups', 'config-' + time.strftime('%Y%m%d-%H%M%S'))
        shutil.copy2(path, os.path.join(backup_dir, os.path.basename(path)))
        result['backup'] = os.path.join(backup_dir, os.path.basename(path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out) + '\n')
    return result
