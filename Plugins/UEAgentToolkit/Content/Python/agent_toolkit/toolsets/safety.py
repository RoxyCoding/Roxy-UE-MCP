"""SafetyTools: undo transactions, undo/redo, modified-asset tracking, file backups,
named save points, deletion previews and the change journal.

Every mutating UE Agent Toolkit tool already runs inside its own undo transaction.
Use begin_transaction/end_transaction to group several tool calls into ONE undo step.
"""

from __future__ import annotations

import unreal

from agent_toolkit.core import backup, deps, editor, resolve
from agent_toolkit.core.errors import Code, ToolError
from agent_toolkit.core.serialize import split_csv
from agent_toolkit.core.tooling import agent_tool, confirmation_required, ctx, journal_entries

_OPEN_TRANSACTIONS: list[dict] = []
_SAVE_POINT_PREFIX = 'savepoint:'


def _undo_count() -> int:
    try:
        return int(unreal.ToolsetLibrary.get_active_undo_count())
    except Exception:  # pylint: disable=broad-exception-caught
        return -1


@unreal.uclass()
class SafetyTools(unreal.ToolsetDefinition):
    """Transactions (group edits into one undo step), undo/redo, list of modified assets,
    file backups and named save points with restore, deletion previews and a journal of
    every change made through UE Agent Toolkit tools."""

    @agent_tool(allow_during_pie=False)
    def begin_transaction(description: str) -> dict:
        """Opens an undo transaction so that all following edits (from any tool) undo as one step.
        Always pair with end_transaction.

        Args:
            description: Text shown in Edit > Undo History, e.g. "Create player character".
        """
        index = unreal.SystemLibrary.begin_transaction('AgentToolkit', description, None)
        _OPEN_TRANSACTIONS.append({'description': description, 'index': index})
        return {'transaction_index': index, 'open_transactions': len(_OPEN_TRANSACTIONS)}

    @agent_tool()
    def end_transaction() -> dict:
        """Closes the transaction opened by begin_transaction, committing it as one undo step."""
        if not _OPEN_TRANSACTIONS:
            raise ToolError(Code.EDITOR_STATE, 'No transaction opened by begin_transaction is active.')
        info = _OPEN_TRANSACTIONS.pop()
        result = unreal.SystemLibrary.end_transaction()
        return {'closed': info['description'], 'end_result': result, 'undo_count': _undo_count()}

    @agent_tool(allow_during_pie=False)
    def undo(steps: int = 1) -> dict:
        """Undoes the most recent editor transactions (also those made by the user).

        Args:
            steps: Number of undo steps.
        """
        if _OPEN_TRANSACTIONS:
            raise ToolError(Code.EDITOR_STATE, 'Close the open transaction with end_transaction before undoing.')
        done = 0
        for _ in range(max(1, steps)):
            if not unreal.ToolsetLibrary.undo_transaction(True):
                break
            done += 1
        if done == 0:
            raise ToolError(Code.EDITOR_STATE, 'Nothing to undo.', retryable=False,
                            likely_causes=['Asset deletions, saves and some imports are not undoable; '
                                           'use restore_backup instead.'])
        ctx().mark_modified(True)
        return {'undone': done, 'remaining_undo_count': _undo_count()}

    @agent_tool(allow_during_pie=False)
    def redo(steps: int = 1) -> dict:
        """Redoes previously undone transactions.

        Args:
            steps: Number of redo steps.
        """
        world = editor.editor_world()
        before = _undo_count()
        for _ in range(max(1, steps)):
            unreal.SystemLibrary.execute_console_command(world, 'TRANSACTION REDO')
        after = _undo_count()
        if after <= before:
            raise ToolError(Code.EDITOR_STATE, 'Nothing was redone (redo stack empty or cleared by a new edit).')
        ctx().mark_modified(True)
        return {'redone': after - before, 'undo_count': after}

    @agent_tool()
    def get_undo_state(journal_limit: int = 20) -> dict:
        """Returns the undo stack depth, open toolkit transactions and the latest journal entries.

        Args:
            journal_limit: Number of recent journal entries to include.
        """
        return {'undo_count': _undo_count(), 'open_transactions': [t['description'] for t in _OPEN_TRANSACTIONS],
                'recent_changes': journal_entries(journal_limit)}

    @agent_tool()
    def list_modified_assets() -> dict:
        """Lists unsaved (dirty) packages, split into content and levels, flagging never-saved ones."""
        content = sorted(p.get_name() for p in unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages() or [])
        maps = sorted(p.get_name() for p in unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages() or [])
        never_saved = [p for p in content + maps if p.startswith('/Temp/') or editor.package_filename(p) is None]
        return {'content': content, 'levels': maps, 'never_saved': never_saved, 'count': len(content) + len(maps)}

    @agent_tool()
    def get_change_journal(limit: int = 50) -> dict:
        """Returns the journal of modifications made through UE Agent Toolkit tools in this
        editor session (tool, target, dirtied packages, time). Also persisted to
        Saved/AgentToolkit/journal.jsonl.

        Args:
            limit: Number of most recent entries.
        """
        return {'entries': journal_entries(limit)}

    @agent_tool()
    def preview_asset_deletion(asset_paths: list[str]) -> dict:
        """Read-only report used before deleting assets: existence, external referencers,
        dependencies, dirty state and file size of each asset.

        Args:
            asset_paths: Assets you plan to delete.
        """
        packages = sorted({resolve.package_of(p) for p in asset_paths})
        dirty = editor.dirty_package_names()
        out = []
        for p in packages:
            exists = deps.package_exists(p)
            refs = [r for r in deps.referencers(p) if r not in packages] if exists else []
            out.append({'package': p, 'exists': exists, 'external_referencers': refs, 'safe_to_delete': exists and not refs,
                        'dependencies': deps.dependencies(p)[:20] if exists else [], 'is_dirty': p in dirty})
        return {'assets': out, 'safe_count': sum(1 for o in out if o['safe_to_delete'])}

    @agent_tool()
    def create_backup(asset_paths: list[str], label: str | None = None) -> dict:
        """Copies the on-disk files of assets to Saved/AgentToolkit/Backups/<id>. Unsaved
        in-memory changes are not included (save first if needed).

        Args:
            asset_paths: Assets (or levels) to back up.
            label: Optional description.
        """
        packages = [resolve.package_of(p) for p in asset_paths]
        manifest = backup.create(packages, label or '')
        for s in manifest['skipped']:
            ctx().warn(f"{s['package']}: {s['reason']}", 'NOT_BACKED_UP')
        return {'backup_id': manifest['id'], 'backed_up': [e['package'] for e in manifest['entries']],
                'skipped': manifest['skipped']}

    @agent_tool()
    def list_backups(limit: int = 30) -> dict:
        """Lists backups and save points, newest first.

        Args:
            limit: Maximum entries.
        """
        return {'backups': backup.list_all(limit)}

    @agent_tool(mutates=True, transaction=False)
    def restore_backup(backup_id: str, asset_paths: str | None = None, confirm: bool = False) -> dict:
        """Restores files from a backup over the current assets and reloads them. Without
        confirm=True, previews which packages would be overwritten. Current unsaved changes
        to those assets are lost.

        Args:
            backup_id: Id from create_backup / list_backups / delete_assets.
            asset_paths: Optional comma-separated subset of packages to restore.
            confirm: Perform the restore.
        """
        manifest = backup.load(backup_id)
        wanted = [resolve.package_of(p) for p in split_csv(asset_paths)]
        packages = [e['package'] for e in manifest['entries'] if not wanted or e['package'] in wanted]
        ctx().set_target(backup_id)
        if not confirm:
            dirty = editor.dirty_package_names()
            raise confirmation_required(f'{len(packages)} packages would be overwritten from backup {backup_id}.',
                                        {'packages': packages, 'unsaved_changes_lost': [p for p in packages if p in dirty]})
        result = backup.restore(backup_id, wanted or None)
        if result['reload_error']:
            ctx().warn(result['reload_error'], 'RELOAD_FAILED',
                       likely_causes=['Restart the editor to pick up restored files.'])
        ctx().mark_modified(bool(result['restored']))
        return result

    @agent_tool(mutates=True, transaction=False)
    def create_save_point(name: str, asset_paths: list[str], include_dirty_assets: bool = True,
                          save_dirty_first: bool = False) -> dict:
        """Creates a named save point: a backup of the given assets plus (optionally) every
        currently dirty asset. With save_dirty_first, dirty packages are saved before the
        backup so their current state is captured.

        Args:
            name: Save point name, e.g. "before-enemy-ai".
            asset_paths: Additional assets to include (may be empty []).
            include_dirty_assets: Include all currently unsaved packages.
            save_dirty_first: Save dirty packages to disk first (makes them part of the snapshot).
        """
        packages = {resolve.package_of(p) for p in asset_paths}
        dirty = {p for p in editor.dirty_package_names() if not p.startswith('/Temp/')}
        if include_dirty_assets:
            packages |= dirty
        if save_dirty_first and dirty:
            pkgs = [unreal.find_package(p) for p in dirty if hasattr(unreal, 'find_package')]
            unreal.EditorLoadingAndSavingUtils.save_packages([p for p in pkgs if p], True)
            ctx().mark_modified(True)
        elif include_dirty_assets and dirty:
            ctx().warn('Dirty assets were backed up in their last SAVED state (save_dirty_first=false).',
                       'UNSAVED_STATE_NOT_CAPTURED')
        if not packages:
            raise ToolError(Code.INVALID_ARGUMENT, 'Nothing to snapshot: pass asset_paths or have dirty assets.')
        manifest = backup.create(sorted(packages), _SAVE_POINT_PREFIX + name)
        ctx().set_target(name)
        return {'save_point': name, 'backup_id': manifest['id'],
                'packages': [e['package'] for e in manifest['entries']], 'skipped': manifest['skipped']}

    @agent_tool(mutates=True, transaction=False)
    def restore_save_point(name: str, confirm: bool = False) -> dict:
        """Restores the most recent save point with this name (see create_save_point).

        Args:
            name: Save point name.
            confirm: Perform the restore (otherwise preview).
        """
        matches = [b for b in backup.list_all(500) if b['label'] == _SAVE_POINT_PREFIX + name]
        if not matches:
            raise ToolError(Code.OBJECT_NOT_FOUND, f'No save point named {name!r}',
                            likely_causes=['Use list_backups to see save points (label "savepoint:<name>").'])
        latest = matches[0]
        ctx().set_target(name)
        if not confirm:
            raise confirmation_required(f"Save point {name!r} ({latest['time']}) would overwrite {len(latest['packages'])} packages.",
                                        {'backup_id': latest['id'], 'packages': latest['packages']})
        result = backup.restore(latest['id'])
        ctx().mark_modified(bool(result['restored']))
        return result
