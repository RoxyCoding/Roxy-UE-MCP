"""Background process jobs (UBT builds, UAT packaging) with log files under Saved/AgentToolkit/<kind>."""

from __future__ import annotations

import os
import subprocess
import time

from . import editor
from .errors import Code, ToolError

_JOBS: dict[str, dict] = {}


def start(kind: str, cmd: list[str]) -> dict:
    job_id = f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}"
    log_path = os.path.join(editor.toolkit_saved_dir(kind), f'{job_id}.log')
    log_file = open(log_path, 'w', encoding='utf-8', errors='replace')  # pylint: disable=consider-using-with
    proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT,  # pylint: disable=consider-using-with
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    _JOBS[job_id] = {'proc': proc, 'log': log_path, 'file': log_file, 'cmd': cmd, 'started': time.time(), 'kind': kind}
    return {'job_id': job_id, 'command': ' '.join(cmd), 'log_file': log_path}


def status(job_id: str, kind: str) -> tuple[str, int | None, list[str], float | None]:
    """Returns (state, exit_code, log_lines, elapsed_seconds)."""
    job = _JOBS.get(job_id)
    log_path = job['log'] if job else os.path.join(editor.toolkit_saved_dir(kind), f'{job_id}.log')
    if not os.path.isfile(log_path):
        raise ToolError(Code.OBJECT_NOT_FOUND, f'Unknown job {job_id!r}', target=job_id)
    code = job['proc'].poll() if job else None
    if job and code is not None and not job['file'].closed:
        job['file'].close()
    with open(log_path, encoding='utf-8', errors='replace') as f:
        lines = f.read().splitlines()
    state = 'running' if job and code is None else ('unknown' if code is None else ('succeeded' if code == 0 else 'failed'))
    elapsed = round(time.time() - job['started'], 1) if job else None
    return state, code, lines, elapsed


def cancel(job_id: str) -> bool:
    job = _JOBS.get(job_id)
    if not job:
        raise ToolError(Code.OBJECT_NOT_FOUND, f'Unknown or finished job {job_id!r}', target=job_id)
    if job['proc'].poll() is None:
        job['proc'].kill()
        return True
    return False
