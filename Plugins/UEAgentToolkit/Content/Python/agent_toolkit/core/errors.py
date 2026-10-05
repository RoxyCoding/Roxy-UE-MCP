"""Structured tool errors.

Every failure raised from a tool body should be a ToolError so the agent receives
a machine-readable code, the target, likely causes and whether a retry can help.
"""

from __future__ import annotations


class Code:
    """Stable error codes returned to agents."""
    INVALID_ARGUMENT = 'INVALID_ARGUMENT'
    ASSET_NOT_FOUND = 'ASSET_NOT_FOUND'
    ACTOR_NOT_FOUND = 'ACTOR_NOT_FOUND'
    OBJECT_NOT_FOUND = 'OBJECT_NOT_FOUND'
    CLASS_NOT_FOUND = 'CLASS_NOT_FOUND'
    ALREADY_EXISTS = 'ALREADY_EXISTS'
    WRONG_TYPE = 'WRONG_TYPE'
    AMBIGUOUS = 'AMBIGUOUS'
    COMPILE_FAILED = 'COMPILE_FAILED'
    EDITOR_STATE = 'EDITOR_STATE'
    CONFIRMATION_REQUIRED = 'CONFIRMATION_REQUIRED'
    NOT_SUPPORTED = 'NOT_SUPPORTED'
    UE_OPERATION_FAILED = 'UE_OPERATION_FAILED'
    INTERNAL_ERROR = 'INTERNAL_ERROR'


# Codes where retrying the same call (possibly after fixing state) is reasonable.
_RETRYABLE_DEFAULT = {
    Code.EDITOR_STATE: True,
    Code.UE_OPERATION_FAILED: True,
    Code.CONFIRMATION_REQUIRED: True,
}


class ToolError(Exception):
    """An expected, explainable tool failure."""

    def __init__(self, code: str, message: str, target: str = '',
                 likely_causes: list[str] | None = None,
                 retryable: bool | None = None, ue_error: str = '', details: object = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.target = target
        self.likely_causes = list(likely_causes or [])
        self.retryable = _RETRYABLE_DEFAULT.get(code, False) if retryable is None else retryable
        self.ue_error = ue_error
        self.details = details  # optional payload returned in details_json (reports, previews)


def require(condition: object, code: str, message: str, target: str = '',
            likely_causes: list[str] | None = None) -> None:
    """Raises ToolError when condition is falsy."""
    if not condition:
        raise ToolError(code, message, target, likely_causes)
