"""Agent skill: how to work safely and self-correct with the UE Agent Toolkit."""

import unreal

from toolset_registry.agent_skill import agent_skill

_INSTRUCTIONS = """\
RESULTS
- Every agent_toolkit tool returns {success, errors[], warnings[], target, modified,
  dirtied_packages[], details_json}. Never assume success: read `errors[0].code`,
  `likely_causes` and `retryable`. details_json still carries reports/previews on failure.
- CONFIRMATION_REQUIRED is not a failure of your plan: details_json holds the dry-run
  preview; review it, then repeat the call with confirm=true.

SELF-CORRECTION LOOP (Blueprints, Materials, Input)
1. Inspect before editing (inspect_blueprint / inspect_blueprint_graph / inspect_material).
2. Make all edits for one logical unit, then compile once.
3. On COMPILE_FAILED read details_json.errors (graph, node id, message), inspect only the
   offending nodes (only_nodes_with_messages=true), fix, recompile.
4. Validate (validate_blueprint / validate_level / validate_project) before declaring done.
- Node ids ("K2Node_CallFunction_3") and pins ("NodeId.PinName") from inspect output are the
  addresses used by connect/disconnect/set-default tools.
- Node menu strings are localized to the editor language. Create nodes by function path
  ("Character:Jump"), event function ("ReceiveBeginPlay"), variable or macro name instead.

SAFETY
- Wrap multi-step edits in begin_transaction/end_transaction so one undo reverts them.
- Asset deletion, saving and config writes are not undoable: rely on the automatic backups
  (list_backups / restore_backup) and create_save_point before risky batches.
- Asset-registry reference data reflects saved files only; save before trusting
  "unreferenced" or "safe to delete" results.
- Mutating tools refuse to run during Play-In-Editor; stop PIE first.

DEBUGGING
- clear_log before an action, then get_log_errors / get_runtime_errors since that mark.
- resolve_error_context turns an error line into assets and the next inspection call.
"""


@agent_skill
class AgentToolkitWorkflowSkill(unreal.AgentSkill):
    """How to use the UE Agent Toolkit toolsets safely: result envelopes, the
    edit-compile-fix loop, transactions/backups and log-based debugging. Apply whenever
    creating or modifying Unreal content with agent_toolkit tools."""

    instructions = _INSTRUCTIONS
