"""PackagingTools: cook / package the project with UAT BuildCookRun as a background job and
read structured results (stage, errors, warnings, output folder)."""

from __future__ import annotations

import os
import re

import unreal

from agent_toolkit.core import editor, jobs
from agent_toolkit.core.errors import Code, ToolError
from agent_toolkit.core.serialize import split_csv
from agent_toolkit.core.tooling import agent_tool, ctx

_PLATFORMS = {'win64': 'Win64', 'windows': 'Win64', 'linux': 'Linux', 'android': 'Android', 'ios': 'IOS', 'mac': 'Mac'}
_CONFIGS = {'debuggame': 'DebugGame', 'development': 'Development', 'test': 'Test', 'shipping': 'Shipping'}
_STAGE = re.compile(r'\*{5,} (\w+) COMMAND (STARTED|COMPLETED)')
_ERROR = re.compile(r'(?:^|\s)(?:Error|ERROR)(?::|\s-)|LogCook: Error|LogInit: Error|error C\d+|: error |Exception:|BUILD FAILED')
_WARNING = re.compile(r'(?:^|\s)Warning:|LogCook: Warning|warning C\d+')


def _uat() -> str:
    path = os.path.join(os.path.abspath(unreal.Paths.engine_dir()), 'Build', 'BatchFiles', 'RunUAT.bat')
    if not os.path.isfile(path):
        raise ToolError(Code.NOT_SUPPORTED, f'RunUAT not found: {path}')
    return path


@unreal.uclass()
class PackagingTools(unreal.ToolsetDefinition):
    """Project packaging via UAT BuildCookRun (build, cook, stage, pak, archive) as background
    jobs with stage tracking and parsed errors/warnings, plus cook-only runs."""

    @agent_tool()
    def start_packaging(platform: str = 'Win64', configuration: str = 'Development',
                        output_directory: str | None = None, maps: str | None = None, cook_only: bool = False,
                        clean: bool = False, compressed: bool = True, skip_build: bool = False) -> dict:
        """Starts packaging (or cooking) in the background and returns a job id. Save all assets
        first: UAT cooks what is on disk. Poll with get_packaging_status.

        Args:
            platform: Win64, Linux, Android, IOS or Mac (SDK must be installed).
            configuration: Development, DebugGame, Test or Shipping.
            output_directory: Archive folder (default <Project>/Saved/AgentToolkit/Packages/<platform>).
            maps: Comma-separated map paths to cook (default: project settings).
            cook_only: Cook content without staging/packaging.
            clean: Clean build and cook.
            compressed: Compress pak files.
            skip_build: Do not compile (only if the game binaries are already built).
        """
        plat = _PLATFORMS.get(platform.lower())
        conf = _CONFIGS.get(configuration.lower())
        if plat is None or conf is None:
            raise ToolError(Code.INVALID_ARGUMENT, f'platform must be one of {sorted(set(_PLATFORMS.values()))}, '
                            f'configuration one of {sorted(_CONFIGS.values())}')
        dirty = sorted(p for p in editor.dirty_package_names() if not p.startswith('/Temp/'))
        if dirty:
            ctx().warn(f'{len(dirty)} unsaved packages will NOT be included (UAT cooks files on disk).',
                       'UNSAVED_CHANGES', likely_causes=['Run AssetManagementTools.save_dirty_assets first.'])
        project = unreal.Paths.get_project_file_path()
        out = output_directory or os.path.join(editor.toolkit_saved_dir('Packages'), plat)
        editor_cmd = os.path.join(os.path.abspath(unreal.Paths.engine_dir()), 'Binaries', 'Win64', 'UnrealEditor-Cmd.exe')
        cmd = [_uat(), 'BuildCookRun', f'-project={project}', '-noP4', '-utf8output', f'-platform={plat}',
               f'-clientconfig={conf}', '-cook', '-unattended', f'-unrealexe={editor_cmd}']
        # Projects without Source are still built when an enabled plugin has runtime code
        # (UAT then generates temporary targets), so build unless explicitly skipped.
        cmd.append('-skipbuild' if skip_build else '-build')
        if not cook_only:
            cmd += ['-stage', '-pak', '-archive', f'-archivedirectory={out}']
            if compressed:
                cmd.append('-compressed')
        if clean:
            cmd.append('-clean')
        map_list = split_csv(maps)
        if map_list:
            cmd.append('-map=' + '+'.join(map_list))
        ctx().set_target(project)
        job = jobs.start('Packages', cmd)
        job['output_directory'] = None if cook_only else out
        return job

    @agent_tool()
    def get_packaging_status(job_id: str, max_messages: int = 40) -> dict:
        """Returns the state of a packaging job: running/succeeded/failed, current stage, parsed
        errors and warnings, log tail and the archive folder contents when finished.

        Args:
            job_id: Id returned by start_packaging.
            max_messages: Maximum errors/warnings returned.
        """
        state, code, lines, elapsed = jobs.status(job_id, 'Packages')
        stage = 'starting'
        for line in lines:
            m = _STAGE.search(line)
            if m:
                stage = m.group(1).lower() + ('' if m.group(2) == 'STARTED' else '_done')
        errors = [l.strip() for l in lines if _ERROR.search(l)]
        warnings = [l.strip() for l in lines if _WARNING.search(l) and not _ERROR.search(l)]
        result = {'job_id': job_id, 'state': state, 'exit_code': code, 'stage': stage, 'elapsed_seconds': elapsed,
                  'error_count': len(errors), 'warning_count': len(warnings), 'errors': errors[:max_messages],
                  'warnings': warnings[:max_messages], 'log_tail': lines[-20:]}
        arch = next((a.split('=', 1)[1] for l in lines[:5] for a in l.split() if a.startswith('-archivedirectory=')), None)
        if state == 'succeeded' and arch and os.path.isdir(arch):
            result['archive'] = arch
            result['archive_files'] = sorted(os.listdir(arch))[:50]
        if state == 'failed' and not errors:
            ctx().warn('Failed without parsed errors; read log_tail / the log file.', 'NO_PARSED_ERRORS')
        ctx().set_target(job_id)
        return result

    @agent_tool()
    def cancel_packaging(job_id: str) -> dict:
        """Stops a running packaging job.

        Args:
            job_id: Id returned by start_packaging.
        """
        return {'job_id': job_id, 'cancelled': jobs.cancel(job_id)}
