"""BuildDebugTools: Blueprint compilation reports, C++ builds, Output Log analysis,
runtime errors, asserts/ensures and error-to-asset tracing.

Reuse alongside Epic's toolsets: LogsToolset (raw log + verbosity), LiveCodingToolset
(CompileLiveCoding while the editor runs), AutomationTestToolset (RunTests /
RunTestsByFilter / GetTestResults, also for Functional Tests).
"""

from __future__ import annotations

import os
import re

import unreal

from agent_toolkit.core import bp as bpu
from agent_toolkit.core import deps, editor, jobs, logs, native, resolve
from agent_toolkit.core.errors import Code, ToolError
from agent_toolkit.core.tooling import agent_tool, ctx

_CPP_DIAG = re.compile(r'^(?P<file>[A-Za-z]:[^(]+|[^(:]+)\((?P<line>\d+)(?:,\d+)?\)\s*:\s*(?P<sev>error|warning|fatal error)\s+(?P<code>\w+)\s*:\s*(?P<msg>.*)$',
                       re.IGNORECASE)
_RUNTIME_PATTERNS = ('Accessed None', 'Blueprint Runtime Error', 'Script Msg', 'Infinite loop',
                     'Attempted to access', 'Script call stack', 'is pending kill', 'IsValid')
_ASSERT_PATTERNS = ('Ensure condition failed', 'Assertion failed', 'Handled ensure', '=== Critical error',
                    'Fatal error', 'Unhandled Exception', 'check(')


def _compact(entries: list[dict], limit: int, with_refs: bool = True) -> list[dict]:
    entries = entries[-limit:]
    if with_refs:
        logs.annotate(entries)
    for e in entries:
        if 'continuation' in e:
            e['continuation'] = e['continuation'][:15]
    return entries


def _log_query(max_severity: str, since_mark: str | None, category: str | None, contains: str | None,
               limit: int) -> dict:
    entries = logs.read_entries(since_mark)
    matched = logs.filter_entries(entries, max_severity, category, contains)
    return {'log_file': logs.current_log_file(), 'since_mark': since_mark, 'total_matched': len(matched),
            'entries': _compact(matched, limit)}


@unreal.uclass()
class BuildDebugTools(unreal.ToolsetDefinition):
    """Compile Blueprints with structured error reports, launch/poll C++ builds, read and
    search the Output Log by severity since a mark, extract runtime errors and
    asserts/ensures, and trace error text back to assets."""

    @agent_tool()
    def compile_blueprint(asset_path: str, ignore_stale_actors: bool = False) -> dict:
        """Compiles one Blueprint and returns status plus every node-level error/warning
        (graph, node id, title, message). Use inspect_blueprint_graph(only_nodes_with_messages)
        for pin-level context and fix, then compile again.

        Args:
            asset_path: Blueprint (Actor/Widget/Anim/...) asset path.
            ignore_stale_actors: Compile even if level actors still use an outdated class of a
                recreated Blueprint (otherwise refused with EDITOR_STATE and the actor list).
        """
        bp = resolve.load_asset(asset_path, unreal.Blueprint)
        ctx().set_target(bp.get_outermost().get_name())
        bpu.require_no_stale_actors(ignore_stale_actors)
        report = bpu.compile_report(bp, compile_first=True)
        for e in report['errors']:
            ctx().warn(e['message'], 'BLUEPRINT_COMPILE_ERROR', f"{report['asset']}:{e['graph']}:{e['node']}")
        if report['status'] == 'error':
            raise ToolError(Code.COMPILE_FAILED, f"{report['asset']} failed to compile with "
                            f"{len(report['errors'])} node errors", target=report['asset'],
                            likely_causes=[f"[{e['graph']}] {e['title']}: {e['message']}" for e in report['errors'][:10]]
                            or [report.get('hint', '')], details=report)
        return report

    @agent_tool()
    def compile_blueprints_in_folder(path: str = '/Game', only_report_problems: bool = True, limit: int = 500,
                                     ignore_stale_actors: bool = False) -> dict:
        """Compiles every Blueprint under a folder and summarises the results.

        Args:
            path: Folder to scan.
            only_report_problems: Omit Blueprints that compiled cleanly from the list.
            limit: Maximum Blueprints to compile.
            ignore_stale_actors: Compile even if outdated Blueprint instances are in the level.
        """
        bpu.require_no_stale_actors(ignore_stale_actors)
        classes = ['Blueprint', 'WidgetBlueprint', 'AnimBlueprint', 'EditorUtilityBlueprint',
                   'EditorUtilityWidgetBlueprint', 'ControlRigBlueprint']
        assets = deps.assets_in_path(path, True, classes)[:limit]
        ctx().set_target(path)
        results, counts = [], {'up_to_date': 0, 'up_to_date_with_warnings': 0, 'error': 0, 'other': 0}
        for data in assets:
            bp = data.get_asset()
            if not isinstance(bp, unreal.Blueprint):
                continue
            report = bpu.compile_report(bp, compile_first=True)
            counts[report['status'] if report['status'] in counts else 'other'] += 1
            if not only_report_problems or report['errors'] or report['warnings'] or report['status'] == 'error':
                results.append(report)
        return {'compiled': len(assets), 'counts': counts, 'results': results}

    @agent_tool()
    def get_blueprint_compile_status(asset_path: str) -> dict:
        """Returns the current compile status and node messages of a Blueprint WITHOUT recompiling.

        Args:
            asset_path: Blueprint asset path.
        """
        bp = resolve.load_asset(asset_path, unreal.Blueprint)
        ctx().set_target(bp.get_outermost().get_name())
        return bpu.compile_report(bp, compile_first=False)

    @agent_tool()
    def start_cpp_build(configuration: str = 'Development', target: str | None = None, clean: bool = False) -> dict:
        """Starts an Unreal Build Tool build of the project's C++ in the background and returns
        a job id for get_cpp_build_status. While the editor is running, prefer
        LiveCodingToolset.CompileLiveCoding (UBT refuses to rebuild modules loaded with Live Coding).

        Args:
            configuration: Development, DebugGame or Shipping.
            target: Build target name (default: <Project>Editor).
            clean: Run a clean before building.
        """
        project = unreal.Paths.get_project_file_path()
        project_name = os.path.splitext(os.path.basename(project))[0]
        source_dir = os.path.join(editor.project_dir(), 'Source')
        if not os.path.isdir(source_dir):
            raise ToolError(Code.NOT_SUPPORTED, 'This project has no Source folder (Blueprint-only project).',
                            likely_causes=['Add a C++ class via the editor first.'])
        tgt = target or f'{project_name}Editor'
        build_bat = os.path.join(os.path.abspath(unreal.Paths.engine_dir()), 'Build', 'BatchFiles',
                                 'Clean.bat' if clean else 'Build.bat')
        if not os.path.isfile(build_bat):
            raise ToolError(Code.NOT_SUPPORTED, f'Build script not found: {build_bat}')
        cmd = [build_bat, tgt, 'Win64', configuration, f'-Project={project}', '-WaitMutex', '-NoHotReloadFromIDE']
        ctx().set_target(tgt)
        return jobs.start('Builds', cmd)

    @agent_tool()
    def get_cpp_build_status(job_id: str, max_diagnostics: int = 50) -> dict:
        """Returns the state of a C++ build job: running/succeeded/failed, compiler errors and
        warnings parsed as {file, line, code, message}, and the log tail.

        Args:
            job_id: Id returned by start_cpp_build.
            max_diagnostics: Maximum diagnostics returned per severity.
        """
        state, code, lines, _ = jobs.status(job_id, 'Builds')
        errors, warnings = [], []
        for line in lines:
            m = _CPP_DIAG.match(line.strip())
            if m:
                item = {'file': m.group('file').strip(), 'line': int(m.group('line')), 'code': m.group('code'),
                        'message': m.group('msg').strip()}
                (errors if 'error' in m.group('sev').lower() else warnings).append(item)
        if any('Live Coding' in l for l in lines[-30:]):
            ctx().warn('UBT refused because Live Coding is active; use LiveCodingToolset.CompileLiveCoding '
                       'or close the editor.', 'LIVE_CODING_ACTIVE')
        ctx().set_target(job_id)
        return {'job_id': job_id, 'state': state, 'exit_code': code, 'error_count': len(errors),
                'warning_count': len(warnings), 'errors': errors[:max_diagnostics],
                'warnings': warnings[:max_diagnostics], 'log_tail': lines[-25:]}

    @agent_tool()
    def get_output_log(max_severity: str = 'Log', since_mark: str | None = 'default', category: str | None = None,
                       contains: str | None = None, limit: int = 200) -> dict:
        """Returns parsed Output Log entries (category, severity, message, continuation lines,
        referenced assets) newer than a mark set by clear_log.

        Args:
            max_severity: Least severe level to include: Fatal, Error, Warning, Display, Log, Verbose.
            since_mark: Mark name from clear_log ("default"); null reads the whole log.
            category: Only this log category, e.g. LogBlueprint.
            contains: Case-insensitive substring filter.
            limit: Maximum entries (latest kept).
        """
        return _log_query(max_severity, since_mark, category, contains, limit)

    @agent_tool()
    def get_log_errors(since_mark: str | None = 'default', category: str | None = None, limit: int = 100) -> dict:
        """Returns only Error/Fatal log entries (with referenced assets) since the mark.

        Args:
            since_mark: Mark name from clear_log; null reads the whole log.
            category: Optional log category filter.
            limit: Maximum entries.
        """
        return _log_query('Error', since_mark, category, None, limit)

    @agent_tool()
    def get_log_warnings(since_mark: str | None = 'default', category: str | None = None, limit: int = 100) -> dict:
        """Returns only Warning log entries since the mark.

        Args:
            since_mark: Mark name from clear_log; null reads the whole log.
            category: Optional log category filter.
            limit: Maximum entries.
        """
        entries = logs.filter_entries(logs.read_entries(since_mark), 'Warning', category)
        warnings = [e for e in entries if e['severity'] == 'Warning']
        return {'log_file': logs.current_log_file(), 'total_matched': len(warnings),
                'entries': _compact(warnings, limit)}

    @agent_tool()
    def search_log(pattern: str, since_mark: str | None = None, context_lines: int = 0, limit: int = 100) -> dict:
        """Searches the Output Log with a regular expression.

        Args:
            pattern: Python regular expression (case-insensitive).
            since_mark: Mark name to search after; null searches the whole log.
            context_lines: Entries of context before/after each match.
            limit: Maximum matches.
        """
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            raise ToolError(Code.INVALID_ARGUMENT, f'Invalid regex: {e}') from e
        entries = logs.read_entries(since_mark)
        hits = []
        for i, e in enumerate(entries):
            text = f"{e['category']}: {e['message']}"
            if rx.search(text):
                item = dict(e)
                if context_lines:
                    item['before'] = [f"{x['category']}: {x['message']}" for x in entries[max(0, i - context_lines):i]]
                    item['after'] = [f"{x['category']}: {x['message']}" for x in entries[i + 1:i + 1 + context_lines]]
                hits.append(item)
                if len(hits) >= limit:
                    break
        return {'pattern': pattern, 'matches': len(hits), 'entries': logs.annotate(hits)}

    @agent_tool()
    def clear_log(mark_name: str = 'default') -> dict:
        """Sets a log mark at the current end of the Output Log. Subsequent log queries with
        since_mark=<mark_name> only see newer entries (the log file itself is not erased).
        Call before an operation (compile, PIE, import) to isolate its messages.

        Args:
            mark_name: Name of the mark (default "default").
        """
        return logs.set_mark(mark_name)

    @agent_tool()
    def get_runtime_errors(since_mark: str | None = 'default', limit: int = 100) -> dict:
        """Extracts Blueprint/script runtime errors (Accessed None, Blueprint Runtime Error,
        infinite loops...) with parsed Blueprint/Function/Graph/Node context. Run PIE, then call.

        Args:
            since_mark: Mark name from clear_log; null reads the whole log.
            limit: Maximum entries.
        """
        entries = logs.read_entries(since_mark)
        hits = [e for e in entries if e['category'] in ('LogScript', 'LogBlueprint', 'PIE', 'LogBlueprintUserMessages')
                and (e['severity'] in ('Error', 'Warning') or any(p in e['message'] for p in _RUNTIME_PATTERNS))]
        hits += [e for e in entries if e not in hits and any(p in e['message'] for p in _RUNTIME_PATTERNS)]
        grouped: dict[str, dict] = {}
        for e in logs.annotate(hits):
            key = e['message'][:160]
            g = grouped.setdefault(key, {**e, 'count': 0})
            g['count'] += 1
        items = list(grouped.values())[-limit:]
        return {'total': len(hits), 'unique': len(grouped), 'errors': items,
                'hint': 'blueprint_context.blueprint is the Blueprint name; use search_assets to locate it.'}

    @agent_tool()
    def get_asserts_and_ensures(since_mark: str | None = None, limit: int = 50) -> dict:
        """Returns assertion failures, ensures and crashes recorded in the log, each with its
        callstack lines.

        Args:
            since_mark: Mark name; null reads the whole log.
            limit: Maximum entries.
        """
        entries = logs.read_entries(since_mark)
        hits = [e for e in entries if any(p in e['message'] for p in _ASSERT_PATTERNS)]
        return {'count': len(hits), 'entries': _compact(hits, limit)}

    @agent_tool()
    def get_message_log(log_name: str = 'BlueprintLog', max_messages: int = 200) -> dict:
        """Returns messages from an editor Message Log listing (BlueprintLog, MapCheck, PIE,
        AssetCheck, LoadErrors, EditorErrors, ...). Requires the UEAgentToolkitNative module.

        Args:
            log_name: Message log listing name.
            max_messages: Maximum messages returned.
        """
        lib = native.require('Message Log access')
        msgs = [str(m) for m in lib.get_message_log_messages(log_name) or []]
        ctx().set_target(log_name)
        return {'log_name': log_name, 'count': len(msgs), 'messages': msgs[-max_messages:]}

    @agent_tool()
    def resolve_error_context(error_text: str) -> dict:
        """Traces an error message back to project content: extracts asset paths and Blueprint
        script-stack fields, checks they exist, and finds assets matching mentioned names.

        Args:
            error_text: Any error/warning text (log line, compile message, crash summary).
        """
        refs = logs.extract_references(error_text)
        resolved = []
        for p in refs.get('asset_paths', []):
            package = p.split('.')[0]
            resolved.append({'path': package, 'exists': deps.package_exists(package)})
        names = set(re.findall(r'\b((?:BP|ABP|WBP|BT|BB|M|MI|NS|SK|SM|T|IA|IMC|DT|DA)_[A-Za-z0-9_]+)\b', error_text))
        bpctx = refs.get('blueprint_context', {})
        if 'blueprint' in bpctx:
            names.add(bpctx['blueprint'].split('.')[0])
        found: dict[str, list[str]] = {}
        if names:
            index: dict[str, list[str]] = {}
            for a in deps.assets_in_path('/Game', True):
                index.setdefault(str(a.asset_name), []).append(str(a.package_name))
            found = {name: index.get(name, [])[:5] for name in sorted(names)}
        next_steps = []
        for name, matches in found.items():
            for m in matches:
                next_steps.append(f'InspectorTools.inspect_blueprint_graph(asset_path="{m}", '
                                  f'graph_name="{bpctx.get("graph", "")}", only_nodes_with_messages=true)')
        return {'references': refs, 'paths': resolved, 'assets_by_name': found, 'suggested_next_calls': next_steps[:5]}
