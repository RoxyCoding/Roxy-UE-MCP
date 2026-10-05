"""Output log access: parsing, severity filtering, marks ("clear" baselines) and
extraction of asset/blueprint references from log text."""

from __future__ import annotations

import glob
import json
import os
import re

import unreal

from . import editor

_LINE = re.compile(
    r'^(?:\[(?P<time>[\d.\-:]+)\]\[\s*(?P<frame>\d+)\])?'
    r'(?P<cat>[A-Za-z][A-Za-z0-9_]*): (?:(?P<sev>Fatal|Error|Warning|Display|Verbose|VeryVerbose|Log): )?(?P<msg>.*)$')
_ASSET_PATH = re.compile(r"(?<![\w/])(/(?:Game|Engine|[A-Z][A-Za-z0-9_]+)/[A-Za-z0-9_/\-]+(?:\.[A-Za-z0-9_]+)?(?::[A-Za-z0-9_.]+)?)")
_BP_FIELDS = re.compile(r'(Node|Graph|Function|Blueprint):\s+(.+?)(?=\s+(?:Node|Graph|Function|Blueprint):|$)')
SEVERITY_RANK = {'Fatal': 0, 'Error': 1, 'Warning': 2, 'Display': 3, 'Log': 4, 'Verbose': 5, 'VeryVerbose': 6}
_MAX_READ = 32 * 1024 * 1024


def current_log_file() -> str | None:
    cmd = unreal.SystemLibrary.get_command_line()
    m = re.search(r'-abslog=("?)([^"\s]+)\1', cmd, re.IGNORECASE)
    if m and os.path.isfile(m.group(2)):
        return m.group(2)
    log_dir = os.path.abspath(unreal.Paths.project_log_dir())
    project = os.path.splitext(os.path.basename(unreal.Paths.get_project_file_path()))[0]
    preferred = os.path.join(log_dir, f'{project}.log')
    if os.path.isfile(preferred):
        return preferred
    logs = sorted(glob.glob(os.path.join(log_dir, '*.log')), key=os.path.getmtime, reverse=True)
    return logs[0] if logs else None


def flush() -> None:
    """Forces buffered log output to disk so reads see the latest lines."""
    try:
        unreal.SystemLibrary.execute_console_command(None, 'FlushLog')
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def _marks_file() -> str:
    return os.path.join(editor.toolkit_saved_dir(), 'log_marks.json')


def _load_marks() -> dict:
    try:
        with open(_marks_file(), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def set_mark(name: str = 'default') -> dict:
    flush()
    path = current_log_file()
    size = os.path.getsize(path) if path else 0
    marks = _load_marks()
    marks[name] = {'file': path, 'offset': size}
    with open(_marks_file(), 'w', encoding='utf-8') as f:
        json.dump(marks, f)
    return {'mark': name, 'file': path, 'offset': size}


def mark_offset(name: str | None) -> int:
    if not name:
        return 0
    mark = _load_marks().get(name)
    if not mark or mark.get('file') != current_log_file():
        return 0
    return int(mark.get('offset', 0))


def read_entries(since_mark: str | None = 'default') -> list[dict]:
    """Parses log entries after the given mark (None = whole file, capped to last 32MB)."""
    flush()
    path = current_log_file()
    if not path:
        return []
    size = os.path.getsize(path)
    start = mark_offset(since_mark)
    if start > size:
        start = 0
    start = max(start, size - _MAX_READ)
    with open(path, 'rb') as f:
        f.seek(start)
        data = f.read()
    entries: list[dict] = []
    for raw in data.decode('utf-8', errors='replace').splitlines():
        line = raw.rstrip()
        if not line:
            continue
        m = _LINE.match(line)
        if m:
            entries.append({'time': m.group('time'), 'category': m.group('cat'),
                            'severity': m.group('sev') or 'Log', 'message': m.group('msg')})
        elif entries:
            entries[-1].setdefault('continuation', []).append(line.strip())
        else:
            entries.append({'category': 'Unknown', 'severity': 'Log', 'message': line})
    return entries


def filter_entries(entries: list[dict], max_severity: str = 'VeryVerbose', category: str | None = None,
                   contains: str | None = None) -> list[dict]:
    rank = SEVERITY_RANK.get(max_severity, 6)
    out = []
    for e in entries:
        if SEVERITY_RANK.get(e['severity'], 4) > rank:
            continue
        if category and e['category'].lower() != category.lower():
            continue
        if contains and contains.lower() not in e['message'].lower():
            continue
        out.append(e)
    return out


def extract_references(text: str) -> dict:
    """Asset paths and Blueprint script-stack fields mentioned in a log message."""
    refs: dict = {}
    paths = sorted({p.split(':')[0] for p in _ASSET_PATH.findall(text)})
    if paths:
        refs['asset_paths'] = paths
    fields = {k.lower(): v.strip() for k, v in _BP_FIELDS.findall(text)}
    if fields:
        refs['blueprint_context'] = fields
    return refs


def annotate(entries: list[dict]) -> list[dict]:
    for e in entries:
        refs = extract_references(e['message'] + ' ' + ' '.join(e.get('continuation', [])[:5]))
        if refs:
            e['refs'] = refs
    return entries
