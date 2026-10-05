"""Typed result envelope returned by every UE Agent Toolkit tool.

The ToolsetRegistry serializes ustruct return values to JSON, so the agent sees
`success`, `errors`, `warnings` etc. as real JSON fields. Tool-specific payloads
live in `details_json` (a JSON document) because their shape varies per tool.
"""

import unreal


@unreal.ustruct()
class AgentToolIssue(unreal.StructBase):
    """A single error or warning produced by a tool."""
    code = unreal.uproperty(str, meta={'ToolTip': 'Stable machine-readable code, e.g. ASSET_NOT_FOUND'})
    message = unreal.uproperty(str, meta={'ToolTip': 'What went wrong'})
    target = unreal.uproperty(str, meta={'ToolTip': 'Asset / actor / object the issue refers to'})
    ue_error = unreal.uproperty(str, meta={'ToolTip': 'Raw error text reported by Unreal, if any'})
    likely_causes = unreal.uproperty(unreal.Array(str), meta={'ToolTip': 'Candidate causes / suggested fixes'})
    retryable = unreal.uproperty(bool, meta={'ToolTip': 'True if retrying (after fixing state) can succeed'})


@unreal.ustruct()
class AgentToolResult(unreal.StructBase):
    """Standard result of a UE Agent Toolkit tool call."""
    success = unreal.uproperty(bool)
    tool = unreal.uproperty(str)
    target = unreal.uproperty(str, meta={'ToolTip': 'Primary asset/actor operated on'})
    modified = unreal.uproperty(bool, meta={'ToolTip': 'True if the tool changed editor state'})
    dirtied_packages = unreal.uproperty(unreal.Array(str), meta={'ToolTip': 'Packages newly marked dirty (unsaved) by this call'})
    errors = unreal.uproperty(unreal.Array(AgentToolIssue))
    warnings = unreal.uproperty(unreal.Array(AgentToolIssue))
    details_json = unreal.uproperty(str, meta={'ToolTip': 'Tool-specific JSON payload'})


def make_issue(code: str, message: str, target: str = '', ue_error: str = '',
               likely_causes: list[str] | None = None, retryable: bool = False) -> AgentToolIssue:
    issue = AgentToolIssue()
    issue.code = code
    issue.message = message
    issue.target = target or ''
    issue.ue_error = ue_error or ''
    issue.likely_causes = [str(c) for c in (likely_causes or [])]
    issue.retryable = bool(retryable)
    return issue
