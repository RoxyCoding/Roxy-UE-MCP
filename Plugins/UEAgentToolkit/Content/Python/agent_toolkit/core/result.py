"""Result envelope returned by every UE Agent Toolkit tool.

Tools return the envelope as a compact JSON string:
    {"success": bool, "tool": str, "target": str, "modified": bool,
     "dirtied_packages": [str], "errors": [issue], "warnings": [issue], "details": {...}}
issue = {"code", "message", "target", "ue_error", "likely_causes": [str], "retryable": bool}

A string return keeps each tool's output schema to one line in describe_toolset (a ustruct
return would repeat the full envelope schema for every tool) and lets `details` be a real
nested object instead of an escaped JSON string.
"""

from __future__ import annotations


def make_issue(code: str, message: str, target: str = '', ue_error: str = '',
               likely_causes: list[str] | None = None, retryable: bool = False) -> dict:
    issue: dict = {'code': code, 'message': message, 'retryable': bool(retryable)}
    if target:
        issue['target'] = target
    if ue_error:
        issue['ue_error'] = ue_error
    if likely_causes:
        issue['likely_causes'] = [str(c) for c in likely_causes]
    return issue
