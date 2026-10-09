"""AssetManagementTools: search, create, rename/move, delete (safely), import/export,
redirectors and unused-asset detection.

Complements Epic's editor_toolset AssetTools (create_folder, duplicate, move, delete,
find_assets, metadata, save_assets, referencers...) and the per-type import tools.
Destructive operations here always support a dry-run preview and need confirm=True.
"""

from __future__ import annotations

import os

import unreal

from agent_toolkit.core import backup, deps, editor, mesh_quality, resolve
from agent_toolkit.core.errors import Code, ToolError
from agent_toolkit.core.serialize import from_jsonable, parse_json_arg, split_csv, to_jsonable
from agent_toolkit.core.tooling import agent_tool, confirmation_required, ctx

_FACTORY_CACHE: dict[str, list[str]] = {}
_NEVER_UNUSED_CLASSES = {'World', 'MapBuildDataRegistry', 'GameFeatureData', 'PrimaryAssetLabel'}


def _factory_names_for(cls: unreal.Class) -> list[str]:
    """Names of factory classes whose SupportedClass is cls and that can create new assets."""
    if not _FACTORY_CACHE:
        for name in dir(unreal):
            if not (name.endswith('Factory') or name.endswith('FactoryNew') or name.endswith('_Factory')):
                continue
            py = getattr(unreal, name, None)
            if not isinstance(py, type) or not issubclass(py, unreal.Factory):
                continue
            try:
                f = py()
                supported = f.get_editor_property('supported_class')
                if supported is not None and f.get_editor_property('create_new'):
                    _FACTORY_CACHE.setdefault(supported.get_path_name(), []).append(name)
            except Exception:  # pylint: disable=broad-exception-caught
                continue
    return _FACTORY_CACHE.get(cls.get_path_name(), [])


def _asset_entry(data: unreal.AssetData) -> dict:
    return {'path': str(data.package_name), 'name': str(data.asset_name), 'class': deps.asset_class_name(data)}


def _deletion_preview(packages: list[str]) -> list[dict]:
    dirty = editor.dirty_package_names()
    preview = []
    for p in packages:
        exists = deps.package_exists(p)
        refs = [r for r in deps.referencers(p) if r not in packages] if exists else []
        file = editor.package_filename(p) if exists else None
        preview.append({'package': p, 'exists': exists, 'external_referencers': refs[:50],
                        'referencer_count': len(refs), 'is_dirty': p in dirty, 'file': file,
                        'size_bytes': os.path.getsize(file) if file else None})
    return preview


@unreal.uclass()
class AssetManagementTools(unreal.ToolsetDefinition):
    """Asset lifecycle tools with safety rails: search, generic create, rename/move with
    reference fix-up, guarded deletion with backups, import/reimport/export, redirector
    detection/fix-up and unused asset detection."""

    @agent_tool()
    def search_assets(query: str | None = None, class_names: str | None = None, path: str = '/Game',
                      recursive: bool = True, limit: int = 100) -> dict:
        """Searches the asset registry by name substring, class and folder.

        Args:
            query: Case-insensitive substring of the asset name (omit for all).
            class_names: Comma-separated asset class names, e.g. "Blueprint,StaticMesh".
            path: Folder to search, e.g. /Game/Characters.
            recursive: Include subfolders.
            limit: Maximum results.
        """
        assets = deps.assets_in_path(path, recursive, split_csv(class_names))
        q = (query or '').lower()
        matches = [a for a in assets if q in str(a.asset_name).lower()] if q else assets
        ctx().set_target(path)
        return {'total_matches': len(matches), 'truncated': len(matches) > limit,
                'assets': [_asset_entry(a) for a in matches[:limit]]}

    @agent_tool(mutates=True)
    def create_asset(asset_path: str, asset_class: str, factory_properties_json: str | None = None) -> dict:
        """Creates a new asset of any class that has a "create new" factory (Blueprint,
        Material, MaterialInstanceConstant, DataAsset, DataTable, BehaviorTree,
        BlackboardData, InputAction, InputMappingContext, LevelSequence, NiagaraSystem,
        SoundCue, AnimMontage, BlendSpace, WidgetBlueprint, CurveFloat, ...).

        Args:
            asset_path: Full path of the new asset, e.g. /Game/Input/IA_Jump.
            asset_class: Asset class name, e.g. "InputAction", "Blueprint", "DataTable".
            factory_properties_json: JSON object of factory properties, e.g.
                {"parent_class": "/Script/Engine.Character"} for Blueprint,
                {"data_asset_class": "/Script/MyGame.MyDataAsset"} for DataAsset,
                {"struct": "/Script/MyGame.MyRow"} for DataTable,
                {"target_skeleton": "/Game/Mannequin/SK_Mannequin_Skeleton"} for AnimBlueprint/Montage.
        """
        package, _ = resolve.normalize_asset_path(asset_path)
        ctx().set_target(package)
        if resolve.asset_exists(package):
            raise ToolError(Code.ALREADY_EXISTS, f'Asset already exists: {package}', target=package,
                            likely_causes=['Pick another name, or delete/rename the existing asset first.'])
        cls = resolve.resolve_class(asset_class)
        factories = _factory_names_for(cls)
        if not factories:
            raise ToolError(Code.NOT_SUPPORTED, f'No "create new" factory found for class {cls.get_name()}',
                            target=package, likely_causes=['Use a dedicated tool (e.g. import_files for '
                                                           'textures/meshes/audio) for imported asset types.'])
        factory = getattr(unreal, factories[0])()
        props = parse_json_arg(factory_properties_json or '', 'factory_properties_json', dict)
        for key, value in props.items():
            try:
                current = factory.get_editor_property(key)
            except Exception as e:  # pylint: disable=broad-exception-caught
                raise ToolError(Code.INVALID_ARGUMENT, f'{factories[0]} has no property {key!r}',
                                target=package) from e
            if isinstance(value, str) and (isinstance(current, unreal.Class) or
                                           (value.startswith('/') and (current is None or isinstance(current, unreal.Object)))):
                # Class properties (parent_class, data_asset_class) first, then any object (skeleton, struct).
                try:
                    value = resolve.resolve_class(value)
                except ToolError:
                    value = resolve.load_object(value)
            else:
                value = from_jsonable(value, current, key)
            factory.set_editor_property(key, value)
        folder, name = package.rsplit('/', 1)
        asset = editor.asset_tools().create_asset(name, folder, cls, factory)
        if asset is None:
            raise ToolError(Code.UE_OPERATION_FAILED, f'Factory {factories[0]} failed to create {package}',
                            target=package, likely_causes=['A required factory property is missing '
                                                           '(e.g. parent_class / struct / target_skeleton).'])
        return {'asset': package, 'class': asset.get_class().get_name(), 'factory': factories[0]}

    @agent_tool(mutates=True)
    def rename_asset(asset_path: str, new_name: str) -> dict:
        """Renames an asset in place and fixes up references (a redirector may be left behind;
        use fix_redirectors afterwards).

        Args:
            asset_path: Existing asset path.
            new_name: New asset name (no path).
        """
        asset = resolve.load_asset(asset_path)
        package = asset.get_outermost().get_name()
        ctx().set_target(package)
        if '/' in new_name or not new_name:
            raise ToolError(Code.INVALID_ARGUMENT, 'new_name must be a bare asset name without "/"')
        folder = package.rsplit('/', 1)[0]
        if resolve.asset_exists(f'{folder}/{new_name}'):
            raise ToolError(Code.ALREADY_EXISTS, f'{folder}/{new_name} already exists', target=package)
        data = unreal.AssetRenameData(asset=asset, new_package_path=folder, new_name=new_name)
        if not editor.asset_tools().rename_assets([data]):
            raise ToolError(Code.UE_OPERATION_FAILED, f'Rename of {package} failed', target=package,
                            likely_causes=['The asset may be read-only, checked out by someone else, or open '
                                           'in an editor that blocks renaming.'])
        return {'old': package, 'new': f'{folder}/{new_name}'}

    @agent_tool(mutates=True)
    def move_assets(asset_paths: list[str], destination_folder: str, dry_run: bool = False) -> dict:
        """Moves assets to another folder, fixing up references (redirectors may remain).

        Args:
            asset_paths: Assets to move.
            destination_folder: Target folder, e.g. /Game/Characters/Enemies.
            dry_run: Only report what would be moved.
        """
        dest = destination_folder.rstrip('/')
        if not dest.startswith('/'):
            raise ToolError(Code.INVALID_ARGUMENT, 'destination_folder must start with "/"')
        plan, conflicts = [], []
        for p in asset_paths:
            package = resolve.package_of(p)
            name = package.rsplit('/', 1)[-1]
            target = f'{dest}/{name}'
            if resolve.asset_exists(target):
                conflicts.append(target)
            plan.append({'from': package, 'to': target})
        ctx().set_target(dest)
        if conflicts:
            raise ToolError(Code.ALREADY_EXISTS, f'{len(conflicts)} destination assets already exist',
                            target=dest, likely_causes=[f'Conflicts: {conflicts[:10]}'])
        if dry_run:
            ctx().mark_modified(False)
            return {'dry_run': True, 'plan': plan}
        renames = [unreal.AssetRenameData(asset=resolve.load_asset(m['from']), new_package_path=dest,
                                          new_name=m['from'].rsplit('/', 1)[-1]) for m in plan]
        if not editor.asset_tools().rename_assets(renames):
            raise ToolError(Code.UE_OPERATION_FAILED, 'Move failed (see Output Log)', target=dest)
        return {'moved': plan}

    @agent_tool(mutates=True, transaction=False)
    def delete_assets(asset_paths: list[str], confirm: bool = False, force: bool = False,
                      backup_first: bool = True) -> dict:
        """Deletes assets safely. Without confirm=True this is a dry run that returns, for each
        asset, its external referencers, dirty state and file size. Referenced assets are
        refused unless force=True. Files are backed up first (restore with restore_backup).

        Note: Unreal asset deletion is not undoable via Undo; the backup is the safety net.

        Args:
            asset_paths: Assets to delete.
            confirm: Actually delete (otherwise preview only).
            force: Delete even if other assets still reference them (breaks those references).
            backup_first: Copy files to Saved/AgentToolkit/Backups before deleting.
        """
        packages = sorted({resolve.package_of(p) for p in asset_paths})
        preview = _deletion_preview(packages)
        missing = [p['package'] for p in preview if not p['exists']]
        if missing:
            raise ToolError(Code.ASSET_NOT_FOUND, f'{len(missing)} assets do not exist: {missing[:10]}')
        referenced = [p for p in preview if p['referencer_count']]
        unsaved = sorted(editor.dirty_package_names() - set(packages))
        if unsaved:
            ctx().warn(f'{len(unsaved)} unsaved packages exist; references from unsaved edits are not visible to '
                       'the asset registry. Save first for an accurate referencer check.', 'UNSAVED_REFERENCERS_UNKNOWN')
        if not confirm:
            raise confirmation_required(
                f'Deleting {len(packages)} assets ({len(referenced)} still referenced). Dry run only.',
                {'preview': preview})
        if referenced and not force:
            raise ToolError(Code.INVALID_ARGUMENT,
                            f'{len(referenced)} assets are still referenced; refusing without force=True',
                            likely_causes=[f"{p['package']} <- {p['external_referencers'][:5]}" for p in referenced[:10]])
        backup_info = backup.create(packages, 'pre-delete') if backup_first else None
        deleted, failed = [], []
        for p in packages:
            (deleted if editor.asset_subsystem().delete_asset(p) else failed).append(p)
        if failed:
            ctx().warn(f'{len(failed)} assets could not be deleted: {failed}', 'DELETE_FAILED')
        return {'deleted': deleted, 'failed': failed,
                'backup_id': backup_info['id'] if backup_info else None}

    @agent_tool(mutates=True, transaction=False)
    def save_dirty_assets(path_prefix: str | None = None, include_maps: bool = True, dry_run: bool = False) -> dict:
        """Saves all unsaved (dirty) packages ("Save All"), optionally limited to a folder.

        Args:
            path_prefix: Only save packages under this folder, e.g. /Game/Characters.
            include_maps: Also save dirty levels.
            dry_run: Only list what would be saved.
        """
        pkgs = list(unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages() or [])
        if include_maps:
            pkgs += list(unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages() or [])
        if path_prefix:
            pkgs = [p for p in pkgs if p.get_name().startswith(path_prefix)]
        names = sorted(p.get_name() for p in pkgs)
        temp = [n for n in names if n.startswith('/Temp/')]
        if temp:
            ctx().warn(f'{len(temp)} packages are untitled (/Temp) and need save_current_level_as first: {temp}',
                       'UNTITLED_PACKAGE')
        pkgs = [p for p in pkgs if not p.get_name().startswith('/Temp/')]
        if dry_run:
            ctx().mark_modified(False)
            return {'dry_run': True, 'would_save': [p.get_name() for p in pkgs]}
        mesh_quality.check_meshes(data.get_asset() for p in pkgs
                                 for data in editor.asset_registry().get_assets_by_package_name(p.get_name()))
        ok = unreal.EditorLoadingAndSavingUtils.save_packages(pkgs, True) if pkgs else True
        still_dirty = sorted(editor.dirty_package_names() & {p.get_name() for p in pkgs})
        if not ok or still_dirty:
            raise ToolError(Code.UE_OPERATION_FAILED, f'{len(still_dirty)} packages failed to save',
                            likely_causes=['Files may be read-only or locked by source control.',
                                           f'Still dirty: {still_dirty[:10]}'])
        ctx().mark_modified(bool(pkgs))
        return {'saved': [p.get_name() for p in pkgs]}

    @agent_tool(mutates=True, transaction=False)
    def import_files(source_files: list[str], destination_folder: str, replace_existing: bool = False,
                     destination_name: str | None = None) -> dict:
        """Imports files from disk (FBX/OBJ/glTF meshes, PNG/TGA/EXR textures, WAV/OGG audio,
        CSV/JSON tables...) into a content folder using the engine's default importers.

        Args:
            source_files: Absolute file paths on disk.
            destination_folder: Content folder, e.g. /Game/Imported.
            replace_existing: Overwrite assets with the same name (otherwise refused).
            destination_name: Asset name override (only valid with a single file).
        """
        if destination_name and len(source_files) != 1:
            raise ToolError(Code.INVALID_ARGUMENT, 'destination_name requires exactly one source file')
        missing = [f for f in source_files if not os.path.isfile(f)]
        if missing:
            raise ToolError(Code.INVALID_ARGUMENT, f'Source files not found: {missing}',
                            likely_causes=['Use absolute paths on the machine running the editor.'])
        tasks = []
        for f in source_files:
            name = destination_name or os.path.splitext(os.path.basename(f))[0]
            target = f'{destination_folder.rstrip("/")}/{name}'
            if resolve.asset_exists(target) and not replace_existing:
                raise ToolError(Code.ALREADY_EXISTS, f'{target} exists; pass replace_existing=True to overwrite',
                                target=target)
            task = unreal.AssetImportTask()
            task.filename = f
            task.destination_path = destination_folder
            if destination_name:
                task.destination_name = destination_name
            task.replace_existing = replace_existing
            task.automated = True
            task.save = False
            tasks.append(task)
        editor.asset_tools().import_asset_tasks(tasks)
        results, failed = [], []
        for f, task in zip(source_files, tasks):
            paths = [str(p) for p in task.get_editor_property('imported_object_paths')]
            (results.append({'file': f, 'assets': paths}) if paths else failed.append(f))
        ctx().set_target(destination_folder)
        if failed and not results:
            raise ToolError(Code.UE_OPERATION_FAILED, f'No assets were imported from {failed}',
                            likely_causes=['Unsupported/corrupt file; check BuildDebugTools.get_log_errors.'])
        for f in failed:
            ctx().warn(f'Import produced no assets: {f}', 'IMPORT_FAILED')
        checks = mesh_quality.check_paths([p for result in results for p in result['assets']], repair=True)
        return {'imported': results, 'failed': failed, 'mesh_basis_checks': checks}

    @agent_tool(mutates=True, transaction=False)
    def import_fbx(source_file: str, destination_folder: str, mesh_type: str = 'static',
                   skeleton_path: str | None = None, import_materials: bool = True,
                   import_animations: bool = True, combine_meshes: bool = True,
                   replace_existing: bool = False) -> dict:
        """Imports an FBX with explicit options (legacy FBX importer).

        Args:
            source_file: Absolute .fbx path.
            destination_folder: Content folder.
            mesh_type: "static", "skeletal" or "animation" (animation requires skeleton_path).
            skeleton_path: Existing Skeleton to bind skeletal meshes/animations to.
            import_materials: Create materials/textures from the FBX.
            import_animations: Import animations contained in a skeletal FBX.
            combine_meshes: Combine static mesh parts into one asset.
            replace_existing: Overwrite existing assets.
        """
        if not os.path.isfile(source_file) or not source_file.lower().endswith('.fbx'):
            raise ToolError(Code.INVALID_ARGUMENT, f'Not an existing .fbx file: {source_file}')
        kind = mesh_type.lower()
        if kind not in ('static', 'skeletal', 'animation'):
            raise ToolError(Code.INVALID_ARGUMENT, 'mesh_type must be static, skeletal or animation')
        ui = unreal.FbxImportUI()
        ui.import_mesh = kind != 'animation'
        ui.import_as_skeletal = kind in ('skeletal', 'animation')
        ui.import_animations = kind == 'animation' or (kind == 'skeletal' and import_animations)
        ui.import_materials = import_materials
        ui.import_textures = import_materials
        ui.automated_import_should_detect_type = False
        ui.mesh_type_to_import = {'static': unreal.FBXImportType.FBXIT_STATIC_MESH,
                                  'skeletal': unreal.FBXImportType.FBXIT_SKELETAL_MESH,
                                  'animation': unreal.FBXImportType.FBXIT_ANIMATION}[kind]
        if skeleton_path:
            ui.skeleton = resolve.load_asset(skeleton_path, unreal.Skeleton)
        elif kind == 'animation':
            raise ToolError(Code.INVALID_ARGUMENT, 'mesh_type=animation requires skeleton_path')
        if kind == 'static':
            ui.static_mesh_import_data.combine_meshes = combine_meshes
        task = unreal.AssetImportTask()
        task.filename = source_file
        task.destination_path = destination_folder
        task.replace_existing = replace_existing
        task.automated = True
        task.save = False
        task.options = ui
        task.factory = unreal.FbxFactory()
        editor.asset_tools().import_asset_tasks([task])
        paths = [str(p) for p in task.get_editor_property('imported_object_paths')]
        ctx().set_target(destination_folder)
        if not paths:
            raise ToolError(Code.UE_OPERATION_FAILED, f'FBX import produced no assets: {source_file}',
                            likely_causes=['Wrong mesh_type for the file content, missing skeleton, or an asset '
                                           'with the same name exists (replace_existing=False).'])
        return {'imported': paths, 'mesh_basis_checks': mesh_quality.check_paths(paths, repair=True)}

    @agent_tool(mutates=True, transaction=False)
    def reimport_assets(asset_paths: list[str]) -> dict:
        """Reimports assets from their original source files (meshes, textures, audio, tables).

        Args:
            asset_paths: Imported assets to refresh from disk.
        """
        done, failed, imported_paths = [], [], []
        for p in asset_paths:
            asset = resolve.load_asset(p)
            src = ''
            try:
                src = asset.get_editor_property('asset_import_data').get_first_filename()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            package = asset.get_outermost().get_name()
            if not src or not os.path.isfile(src):
                failed.append({'asset': package, 'reason': f'source file missing: {src or "<none recorded>"}'})
                continue
            task = unreal.AssetImportTask()
            task.filename = src
            task.destination_path = package.rsplit('/', 1)[0]
            task.destination_name = package.rsplit('/', 1)[1]
            task.replace_existing = True
            task.automated = True
            task.save = False
            editor.asset_tools().import_asset_tasks([task])
            imported_paths.extend(str(path) for path in task.get_editor_property('imported_object_paths'))
            (done.append(package) if task.get_editor_property('imported_object_paths') else
             failed.append({'asset': package, 'reason': 'importer produced no output'}))
        if failed and not done:
            raise ToolError(Code.UE_OPERATION_FAILED, 'Reimport failed for all assets',
                            likely_causes=[f"{f['asset']}: {f['reason']}" for f in failed[:10]])
        return {'reimported': done, 'failed': failed,
                'mesh_basis_checks': mesh_quality.check_paths(imported_paths, repair=True)}

    @agent_tool()
    def export_assets(asset_paths: list[str], export_directory: str) -> dict:
        """Exports assets to files on disk using their default exporters (e.g. FBX, PNG, T3D).

        Args:
            asset_paths: Assets to export.
            export_directory: Absolute directory on disk.
        """
        os.makedirs(export_directory, exist_ok=True)
        before = set(os.listdir(export_directory))
        packages = [resolve.load_asset(p).get_outermost().get_name() for p in asset_paths]
        editor.asset_tools().export_assets(packages, export_directory)
        created = []
        for root, _, files in os.walk(export_directory):
            for f in files:
                full = os.path.join(root, f)
                if os.path.relpath(full, export_directory).split(os.sep)[0] not in before:
                    created.append(full)
        if not created:
            ctx().warn('No new files were written; the asset types may have no exporter.', 'NO_EXPORT_OUTPUT')
        return {'exported_files': created}

    @agent_tool()
    def find_redirectors(path: str = '/Game', limit: int = 500) -> dict:
        """Lists ObjectRedirector assets (left by renames/moves) and their referencers.

        Args:
            path: Folder to scan.
            limit: Maximum redirectors to list.
        """
        reds = deps.assets_in_path(path, True, ['ObjectRedirector'])
        ctx().set_target(path)
        out = []
        for r in reds[:limit]:
            package = str(r.package_name)
            out.append({'redirector': package, 'referencers': deps.referencers(package)[:20],
                        'target': [d for d in deps.dependencies(package) if not deps.is_script_package(d)][:1]})
        return {'count': len(reds), 'redirectors': out}

    @agent_tool(mutates=True, transaction=False)
    def fix_redirectors(path: str = '/Game', confirm: bool = False) -> dict:
        """Fixes up redirectors: re-saves every referencer so it points at the real asset, then
        deletes redirectors that are no longer referenced. Without confirm=True, previews.

        Args:
            path: Folder to process.
            confirm: Perform the fix-up (saves referencing packages to disk).
        """
        reds = [str(r.package_name) for r in deps.assets_in_path(path, True, ['ObjectRedirector'])]
        plan = [{'redirector': r, 'referencers': deps.referencers(r)} for r in reds]
        ctx().set_target(path)
        if not reds:
            ctx().mark_modified(False)
            return {'redirectors': 0}
        if not confirm:
            raise confirmation_required(f'{len(reds)} redirectors would be fixed (referencers re-saved).',
                                        {'plan': plan})
        referencers = sorted({ref for item in plan for ref in item['referencers'] if ref not in reds})
        resaved, failed = [], []
        for ref in referencers:
            try:
                asset_list = editor.asset_registry().get_assets_by_package_name(ref) or []
                for data in asset_list:
                    obj = data.get_asset()
                    if obj:
                        obj.modify()
                pkg = unreal.find_package(ref) if hasattr(unreal, 'find_package') else None
                if pkg and unreal.EditorLoadingAndSavingUtils.save_packages([pkg], False):
                    resaved.append(ref)
                else:
                    failed.append(ref)
            except Exception as e:  # pylint: disable=broad-exception-caught
                failed.append(f'{ref}: {e}')
        deleted = []
        for r in reds:
            if not deps.referencers(r) and editor.asset_subsystem().delete_asset(r):
                deleted.append(r)
        remaining = [r for r in reds if r not in deleted]
        if remaining:
            ctx().warn(f'{len(remaining)} redirectors remain (still referenced or locked)', 'REDIRECTORS_REMAIN',
                       likely_causes=['Referencers may be in other plugins/maps not loaded; open them and save.'])
        return {'resaved_referencers': resaved, 'failed': failed, 'deleted_redirectors': deleted,
                'remaining': remaining}

    @agent_tool()
    def find_unused_assets(path: str = '/Game', class_names: str | None = None, limit: int = 300) -> dict:
        """Finds assets that no other asset references (candidates for cleanup). Levels and
        primary/feature assets are excluded. Assets used only from C++, config (.ini) or
        loaded by string path will also appear here, so review before deleting.

        Args:
            path: Folder to scan.
            class_names: Comma-separated class filter, e.g. "Texture2D,StaticMesh".
            limit: Maximum candidates listed.
        """
        assets = deps.assets_in_path(path, True, split_csv(class_names))
        if not assets:
            ctx().warn(f'No assets found under {path}: nothing was checked.', 'EMPTY_SCOPE', target=path)
        unused = []
        for a in assets:
            cls = deps.asset_class_name(a)
            if cls in _NEVER_UNUSED_CLASSES or a.is_redirector():
                continue
            package = str(a.package_name)
            if not deps.referencers(package):
                unused.append({'path': package, 'class': cls})
        ctx().set_target(path)
        ctx().warn('Unreferenced does not guarantee unused: check C++/config/string references.',
                   'REVIEW_BEFORE_DELETE')
        return {'scanned': len(assets), 'unused_count': len(unused), 'truncated': len(unused) > limit,
                'unused': unused[:limit]}
