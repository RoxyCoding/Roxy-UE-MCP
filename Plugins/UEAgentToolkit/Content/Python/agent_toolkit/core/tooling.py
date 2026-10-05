"""The @agent_tool decorator: turns a plain function into a ToolsetRegistry tool.

Responsibilities (kept here so tool bodies stay small):
  * wraps the body's dict result into the JSON result envelope (see core/result.py),
  * converts ToolError / unexpected exceptions into structured errors (never raises),
  * opens an editor undo transaction for mutating tools,
  * reports packages newly dirtied by the call,
  * guards against running mutating tools during Play-In-Editor,
  * records mutating calls in a change journal (Saved/AgentToolkit/journal.jsonl).

Tool bodies return a dict (the `details` payload) and may use `ctx()` to set the
target, add warnings or flag modification.
"""

from __future__ import annotations

import contextvars
import functools
import inspect
import json
import os
import time
import traceback
from typing import Any, Callable

import unreal
import toolset_registry

from . import editor
from .errors import Code, ToolError
from .result import make_issue
from .serialize import dumps, to_jsonable

_TRANSACTION_PREFIX = 'AgentToolkit: '
_JOURNAL_LIMIT = 500


class ToolContext:
    """Per-call mutable state available to tool bodies via ctx()."""

    def __init__(self, tool_name: str):
        self.tool_name = tool_name
        self.target = ''
        self.modified: bool | None = None
        self.warnings: list = []

    def set_target(self, target: Any) -> None:
        self.target = to_jsonable(target) if not isinstance(target, str) else target

    def warn(self, message: str, code: str = 'WARNING', target: str = '',
             likely_causes: list[str] | None = None) -> None:
        self.warnings.append(make_issue(code, message, target, likely_causes=likely_causes))

    def mark_modified(self, modified: bool = True) -> None:
        self.modified = modified


_current: contextvars.ContextVar[ToolContext | None] = contextvars.ContextVar('agent_tool_ctx', default=None)


def ctx() -> ToolContext:
    """Returns the context of the running tool (a throwaway one outside tools)."""
    c = _current.get()
    return c if c is not None else ToolContext('<direct>')


class _Journal:
    """In-memory + on-disk record of mutating tool calls."""

    entries: list[dict] = []

    @classmethod
    def record(cls, entry: dict) -> None:
        cls.entries.append(entry)
        del cls.entries[:-_JOURNAL_LIMIT]
        try:
            path = os.path.join(editor.toolkit_saved_dir(), 'journal.jsonl')
            with open(path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except OSError:
            pass


def journal_entries(limit: int = 50) -> list[dict]:
    return _Journal.entries[-limit:]


def _issue_from_exception(e: BaseException, tool_name: str):
    if isinstance(e, ToolError):
        return make_issue(e.code, e.message, e.target, e.ue_error, e.likely_causes, e.retryable)
    tb = traceback.format_exception(type(e), e, e.__traceback__)
    where = ''.join(tb[-3:]).strip()
    text = str(e)
    causes = ['Unexpected error inside the tool; inspect ue_error and try inspect_* tools to verify state.']
    if 'NoneType' in text:
        causes.insert(0, 'A required object was None (asset/actor/component missing or failed to load).')
    if 'set_editor_property' in text or 'property' in text.lower():
        causes.insert(0, 'Property name/type mismatch: use ObjectTools.list_properties to get exact names.')
    return make_issue(Code.INTERNAL_ERROR, f'{type(e).__name__}: {text}', '', where, causes, False)


def agent_tool(mutates: bool = False, allow_during_pie: bool | None = None,
               transaction: bool | None = None) -> Callable:
    """Decorator for toolset methods. Use *instead of* @staticmethod/@tool_call.

    Args:
        mutates: The tool changes editor/asset state (enables undo transaction,
            dirty tracking, journal, and the PIE guard).
        allow_during_pie: Override the PIE guard (defaults to `not mutates`).
        transaction: Override whether an undo transaction is opened (defaults to `mutates`).
    """
    use_transaction = mutates if transaction is None else transaction
    pie_ok = (not mutates) if allow_during_pie is None else allow_during_pie

    def decorator(func: Callable) -> Any:
        # eval_str: toolset modules use `from __future__ import annotations`, and the
        # registry reads __signature__ verbatim, so annotations must be real types.
        sig = inspect.signature(func, eval_str=True)
        name = func.__name__

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> str:
            context = ToolContext(name)
            token = _current.set(context)
            success, errors = False, []
            details: Any = {}
            started = time.time()
            before = editor.dirty_package_names() if mutates else set()
            try:
                if not pie_ok and editor.is_pie_running():
                    raise ToolError(Code.EDITOR_STATE,
                                    'This tool modifies editor state and cannot run during Play-In-Editor.',
                                    likely_causes=['Stop PIE first (EditorAppToolset.StopPIE), then retry.'])
                if use_transaction:
                    with unreal.ScopedEditorTransaction(_TRANSACTION_PREFIX + name):
                        details = func(*args, **kwargs)
                else:
                    details = func(*args, **kwargs)
                success = True
            except Exception as e:  # pylint: disable=broad-exception-caught
                issue = _issue_from_exception(e, name)
                errors = [issue]
                if not context.target and issue.get('target'):
                    context.target = issue['target']
                if isinstance(e, ToolError) and e.details is not None:
                    details = e.details
            finally:
                _current.reset(token)

            dirtied = sorted(editor.dirty_package_names() - before) if mutates else []
            modified = bool(context.modified) if context.modified is not None else                 bool(mutates and success and (dirtied or details))
            envelope = {'success': success, 'tool': name, 'target': context.target or '', 'modified': modified,
                        'dirtied_packages': dirtied, 'errors': errors, 'warnings': context.warnings,
                        'details': details if details is not None else {}}
            try:
                text = dumps(envelope)
            except Exception as e:  # pylint: disable=broad-exception-caught
                envelope['details'] = {'unserializable_details': str(e)}
                text = dumps(envelope)
            if mutates and modified:
                _Journal.record({
                    'time': time.strftime('%Y-%m-%dT%H:%M:%S'), 'tool': name,
                    'target': envelope['target'], 'success': success,
                    'dirtied_packages': dirtied, 'duration_ms': int((time.time() - started) * 1000),
                })
            return text

        # The envelope is returned as a JSON string: the registry then emits a one-line output
        # schema instead of repeating the full envelope schema for every tool.
        wrapper.__signature__ = sig.replace(return_annotation=str)  # type: ignore[attr-defined]
        annotations = {p.name: p.annotation for p in sig.parameters.values()}
        annotations['return'] = str
        wrapper.__annotations__ = annotations
        return toolset_registry.tool_call(wrapper)

    return decorator


def confirmation_required(message: str, preview: Any, target: str = '') -> ToolError:
    """Builds the error returned by destructive tools called without confirm=True.

    The preview (what would happen) is placed in details so the agent can
    show/decide, then call again with confirm=True.
    """
    return ToolError(Code.CONFIRMATION_REQUIRED, message, target,
                     likely_causes=['Review details (dry-run preview), then call again with confirm=True.'],
                     retryable=True, details=preview)
