"""ValidationTools: let an agent verify its own changes (and the project) and apply
safe, separate auto-fixes.

Every check returns `issues: [{severity, code, message, target}]` so results from
different checks can be merged. Epic's Data Validation framework is used where it
exists (validate_assets); the other checks are fast asset-registry/level scans.
"""

from __future__ import annotations

from collections import defaultdict
import json

import unreal

from agent_toolkit.core import bp as bpu
from agent_toolkit.core import deps, editor, resolve
from agent_toolkit.core.errors import Code, ToolError
from agent_toolkit.core.serialize import parse_json_arg, split_csv, to_jsonable
from agent_toolkit.core.tooling import agent_tool, confirmation_required, ctx

DEFAULT_PREFIXES = {
    'Blueprint': 'BP_', 'WidgetBlueprint': 'WBP_', 'AnimBlueprint': 'ABP_', 'Material': 'M_',
    'MaterialInstanceConstant': 'MI_', 'MaterialFunction': 'MF_', 'MaterialParameterCollection': 'MPC_',
    'Texture2D': 'T_', 'TextureCube': 'TC_', 'TextureRenderTarget2D': 'RT_', 'StaticMesh': 'SM_',
    'SkeletalMesh': 'SK_', 'Skeleton': 'SKEL_', 'PhysicsAsset': 'PHYS_', 'AnimSequence': 'A_',
    'AnimMontage': 'AM_', 'BlendSpace': 'BS_', 'BlendSpace1D': 'BS_', 'AimOffsetBlendSpace': 'AO_',
    'NiagaraSystem': 'NS_', 'NiagaraEmitter': 'NE_', 'SoundWave': 'S_', 'SoundCue': 'SC_',
    'MetaSoundSource': 'MS_', 'SoundAttenuation': 'ATT_', 'BehaviorTree': 'BT_', 'BlackboardData': 'BB_',
    'EnvQuery': 'EQS_', 'DataTable': 'DT_', 'CurveFloat': 'Curve_', 'InputAction': 'IA_',
    'InputMappingContext': 'IMC_', 'LevelSequence': 'LS_', 'PhysicalMaterial': 'PM_',
    'UserDefinedStruct': 'F_', 'UserDefinedEnum': 'E_', 'World': 'L_',
}
_DEFAULT_MATERIALS = ('/Engine/EngineMaterials/WorldGridMaterial', '/Engine/EngineMaterials/DefaultMaterial')


def _issue(severity: str, code: str, message: str, target: str) -> dict:
    return {'severity': severity, 'code': code, 'message': message, 'target': target}


def _summary(issues: list[dict], limit: int) -> dict:
    counts: dict[str, int] = defaultdict(int)
    for i in issues:
        counts[i['severity']] += 1
    return {'issue_count': len(issues), 'counts': dict(counts), 'truncated': len(issues) > limit,
            'issues': issues[:limit]}


# ---------------------------------------------------------------- individual checks

def check_missing_references(path: str, limit: int) -> list[dict]:
    issues = []
    for data in deps.assets_in_path(path, True):
        package = str(data.package_name)
        for missing in deps.missing_dependencies(package):
            issues.append(_issue('error', 'MISSING_REFERENCE', f'references missing package {missing}', package))
            if len(issues) >= limit * 5:
                return issues
    return issues


def check_broken_assets(path: str, load_assets: bool, limit: int) -> list[dict]:
    issues = []
    reds = deps.assets_in_path(path, True, ['ObjectRedirector'])
    for r in reds:
        package = str(r.package_name)
        targets = [d for d in deps.dependencies(package) if not deps.is_script_package(d)]
        if not targets or not all(deps.package_exists(t) for t in targets):
            issues.append(_issue('error', 'BROKEN_REDIRECTOR', 'redirector target does not exist', package))
    for data in deps.assets_in_path(path, True):
        cls_path = str(data.asset_class_path.package_name) + '.' + str(data.asset_class_path.asset_name)
        if cls_path.startswith('/Script/') and unreal.load_class(None, cls_path) is None:
            issues.append(_issue('error', 'UNKNOWN_ASSET_CLASS',
                                 f'asset class {cls_path} is not loaded (missing plugin/module?)', str(data.package_name)))
        elif load_assets and len(issues) < limit and data.get_asset() is None:
            issues.append(_issue('error', 'LOAD_FAILED', 'asset failed to load', str(data.package_name)))
    return issues


def check_naming(path: str, prefixes: dict[str, str]) -> list[dict]:
    issues = []
    for data in deps.assets_in_path(path, True):
        cls = deps.asset_class_name(data)
        prefix = prefixes.get(cls)
        name = str(data.asset_name)
        if prefix and not name.startswith(prefix):
            issues.append({**_issue('info', 'NAMING', f'{cls} should start with "{prefix}"', str(data.package_name)),
                           'suggested_name': prefix + name})
    return issues


def check_duplicate_names(path: str) -> list[dict]:
    by_name: dict[str, list[str]] = defaultdict(list)
    for data in deps.assets_in_path(path, True):
        if not data.is_redirector():
            by_name[str(data.asset_name).lower()].append(str(data.package_name))
    return [_issue('warning', 'DUPLICATE_NAME', f'{len(paths)} assets share the name {paths[0].rsplit("/", 1)[-1]}', p)
            for paths in by_name.values() if len(paths) > 1 for p in paths]


def check_placement(path: str, rules: dict[str, str]) -> list[dict]:
    issues = []
    root = path.rstrip('/')
    for data in deps.assets_in_path(path, True):
        package = str(data.package_name)
        folder = package.rsplit('/', 1)[0]
        cls = deps.asset_class_name(data)
        if folder == root and root == '/Game':
            issues.append(_issue('info', 'ASSET_AT_ROOT', 'asset placed directly in /Game', package))
        if '/Developers/' in package:
            refs = [r for r in deps.referencers(package) if '/Developers/' not in r]
            if refs:
                issues.append(_issue('warning', 'DEVELOPER_ASSET_REFERENCED',
                                     f'developer-folder asset referenced by {refs[:3]}', package))
        expected = rules.get(cls)
        if expected and not package.startswith(expected.rstrip('/') + '/'):
            issues.append(_issue('info', 'ASSET_PLACEMENT', f'{cls} expected under {expected}', package))
    return issues


def check_unsaved() -> list[dict]:
    return [_issue('warning', 'UNSAVED', 'package has unsaved changes', p) for p in sorted(editor.dirty_package_names())]


def blueprint_issues(bp: unreal.Blueprint, compile_first: bool) -> list[dict]:
    asset = bp.get_outermost().get_name()
    issues = []
    report = bpu.compile_report(bp, compile_first)
    for e in report['errors']:
        issues.append(_issue('error', 'COMPILE_ERROR', f"[{e['graph']}] {e['title']}: {e['message']}", asset))
    for w in report['warnings']:
        issues.append(_issue('warning', 'COMPILE_WARNING', f"[{w['graph']}] {w['title']}: {w['message']}", asset))
    if report['status'] == 'error' and not report['errors']:
        issues.append(_issue('error', 'COMPILE_ERROR', 'Blueprint status is error (see Output Log)', asset))
    if unreal.BlueprintEditorLibrary.get_blueprint_parent_class(bp) is None:
        issues.append(_issue('error', 'MISSING_PARENT_CLASS', 'parent class is missing', asset))
    used = set()
    for graph in bpu.graphs(bp):
        for node in bpu.graph_nodes(graph):
            cname = node.get_class().get_name()
            if cname in ('K2Node_VariableGet', 'K2Node_VariableSet'):
                used.add(str(node.get_node_title()).replace('Set ', '').replace('Get ', '').strip())
                for pin in node.list_all_pins() or []:
                    used.add(str(pin.get_pin_name()))
            if cname == 'K2Node_Event' and 'Tick' in str(node.get_node_title()):
                linked = any(p.list_connected_pins() for p in node.list_all_pins() or [])
                issues.append(_issue('info' if linked else 'warning', 'EVENT_TICK',
                                     'Event Tick is used' + ('' if linked else ' but not connected (remove it)'), asset))
    for var in bpu.variable_infos(bp, include_defaults=True):
        if var['name'] not in used:
            issues.append(_issue('info', 'UNUSED_VARIABLE', f"variable {var['name']} is never read or written", asset))
        if var.get('type', '').startswith(('object<', 'class<', 'softobject')) and var.get('default') is None:
            issues.append(_issue('info', 'NULL_PROPERTY', f"variable {var['name']} ({var['type']}) defaults to None", asset))
    return issues


def _simulates_physics(comp: unreal.PrimitiveComponent) -> bool:
    try:
        return bool(comp.get_editor_property('body_instance').get_editor_property('simulate_physics'))
    except Exception:  # pylint: disable=broad-exception-caught
        return bool(comp.is_simulating_physics())


def level_issues(limit: int) -> list[dict]:
    world = editor.editor_world()
    if world is None:
        raise ToolError(Code.EDITOR_STATE, 'No editor world is loaded.')
    issues = []
    actors = resolve.all_level_actors()
    ws = world.get_world_settings()
    kill_z = float(ws.get_editor_property('kill_z')) if ws else -1e9
    seen = {}
    for a in actors:
        label = a.get_actor_label()
        loc = a.get_actor_location()
        if loc.z < kill_z:
            issues.append(_issue('warning', 'BELOW_KILL_Z', f'actor is below KillZ ({kill_z})', label))
        for comp in a.get_components_by_class(unreal.StaticMeshComponent) or []:
            mesh = comp.get_editor_property('static_mesh')
            if mesh is None:
                issues.append(_issue('warning', 'MISSING_MESH', f'{comp.get_name()} has no static mesh', label))
                continue
            for i, m in enumerate(comp.get_materials() or []):
                if m is None or m.get_outermost().get_name() in _DEFAULT_MATERIALS:
                    issues.append(_issue('warning', 'MISSING_MATERIAL',
                                         f'{comp.get_name()} slot {i} uses no/default material', label))
            if _simulates_physics(comp):
                if comp.get_collision_enabled() == unreal.CollisionEnabled.NO_COLLISION:
                    issues.append(_issue('error', 'INVALID_COLLISION', 'simulates physics with collision disabled', label))
                try:
                    body = mesh.get_editor_property('body_setup')
                    agg = body.get_editor_property('agg_geom') if body else None
                    has_simple = agg is not None and any(len(agg.get_editor_property(k)) for k in
                                                         ('box_elems', 'sphere_elems', 'sphyl_elems', 'convex_elems'))
                    if not has_simple:
                        issues.append(_issue('error', 'INVALID_COLLISION',
                                             f'simulates physics but {mesh.get_name()} has no simple collision', label))
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
            key = (mesh.get_path_name(), round(loc.x), round(loc.y), round(loc.z))
            if key in seen:
                issues.append(_issue('warning', 'DUPLICATE_OVERLAP', f'same mesh at same location as {seen[key]}', label))
            else:
                seen[key] = label
        if len(issues) >= limit * 5:
            break
    gms = unreal.GameMapsSettings.get_game_maps_settings()
    if not any(isinstance(a, unreal.PlayerStart) for a in actors):
        issues.append(_issue('info', 'NO_PLAYER_START', 'level has no PlayerStart (pawn spawns at origin)',
                             world.get_outermost().get_name()))
    ai_pawns = [a for a in actors if isinstance(a, unreal.Pawn) and not isinstance(a, unreal.DefaultPawn)
                and a.get_editor_property('auto_possess_ai') != unreal.AutoPossessAI.DISABLED]
    if ai_pawns and not any(isinstance(a, unreal.NavMeshBoundsVolume) for a in actors):
        issues.append(_issue('warning', 'NO_NAVMESH', f'{len(ai_pawns)} AI pawns but no NavMeshBoundsVolume',
                             world.get_outermost().get_name()))
    del gms
    if world.get_outermost().get_name() in editor.dirty_package_names():
        issues.append(_issue('info', 'UNSAVED', 'level has unsaved changes', world.get_outermost().get_name()))
    return issues


@unreal.uclass()
class ValidationTools(unreal.ToolsetDefinition):
    """Checks for compile errors, missing/broken references, invalid assets, null properties,
    missing materials, invalid collision, duplicate names, naming/placement rules,
    redirectors and unsaved assets, plus explicit auto-fix tools. Run validate_project
    for a one-call health report."""

    @agent_tool()
    def validate_project(path: str = '/Game', compile_blueprints: bool = False, include_unused: bool = False,
                         limit_per_check: int = 20) -> dict:
        """One-call health report over a folder: missing references, broken assets,
        redirectors, duplicate names, naming, placement, unsaved assets, Blueprint compile
        problems (optionally recompiling) and optionally unused assets.

        Args:
            path: Folder to validate.
            compile_blueprints: Recompile every Blueprint (slower, but catches stale errors).
            include_unused: Also list unreferenced assets (slower).
            limit_per_check: Maximum issues listed per check (counts are complete).
        """
        ctx().set_target(path)
        sections = {
            'missing_references': check_missing_references(path, limit_per_check),
            'broken_assets': check_broken_assets(path, False, limit_per_check),
            'redirectors': [_issue('warning', 'REDIRECTOR', 'redirector should be fixed up', str(r.package_name))
                            for r in deps.assets_in_path(path, True, ['ObjectRedirector'])],
            'duplicate_names': check_duplicate_names(path),
            'naming': check_naming(path, DEFAULT_PREFIXES),
            'placement': check_placement(path, {}),
            'unsaved': check_unsaved(),
        }
        bp_issues = []
        for data in deps.assets_in_path(path, True, ['Blueprint', 'WidgetBlueprint', 'AnimBlueprint']):
            bp = data.get_asset() if (compile_blueprints or data.is_asset_loaded()) else None
            if isinstance(bp, unreal.Blueprint):
                bp_issues += [i for i in blueprint_issues(bp, compile_blueprints) if i['severity'] != 'info']
        sections['blueprints'] = bp_issues
        if include_unused:
            sections['unused'] = [_issue('info', 'UNUSED', 'no referencers', str(a.package_name))
                                  for a in deps.assets_in_path(path, True)
                                  if deps.asset_class_name(a) not in ('World', 'ObjectRedirector')
                                  and not deps.referencers(str(a.package_name))]
        report = {name: _summary(items, limit_per_check) for name, items in sections.items()}
        totals = defaultdict(int)
        for items in sections.values():
            for i in items:
                totals[i['severity']] += 1
        return {'path': path, 'totals': dict(totals), 'sections': report,
                'note': '' if compile_blueprints else 'Only loaded Blueprints were checked; pass compile_blueprints=true for all.'}

    @agent_tool()
    def validate_assets(asset_paths: list[str]) -> dict:
        """Runs Epic's Data Validation (all registered validators, incl. project-specific ones) on assets.

        Args:
            asset_paths: Assets to validate.
        """
        datas = [resolve.asset_data(p) for p in asset_paths]
        settings = unreal.ValidateAssetsSettings()
        settings.set_editor_property('show_if_no_failures', False)
        settings.set_editor_property('collect_per_asset_details', True)
        vs = unreal.get_editor_subsystem(unreal.EditorValidatorSubsystem)
        _, results = vs.validate_assets_with_settings(datas, settings)
        out = {k: to_jsonable(results.get_editor_property(k)) for k in
               ('num_requested', 'num_checked', 'num_valid', 'num_invalid', 'num_skipped', 'num_warnings',
                'num_unable_to_validate')}
        try:
            out['assets_details'] = to_jsonable(results.get_editor_property('assets_details'))
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        if out.get('num_invalid'):
            ctx().warn(f"{out['num_invalid']} assets are invalid; see assets_details / get_log_errors(category=LogContentValidation)",
                       'INVALID_ASSETS')
        return out

    @agent_tool()
    def validate_blueprint(asset_path: str, compile_first: bool = True) -> dict:
        """Validates one Blueprint: compile errors/warnings, missing parent, unused variables,
        object variables defaulting to None, and unconnected/used Event Tick.

        Args:
            asset_path: Blueprint asset path.
            compile_first: Recompile before checking.
        """
        bp = resolve.load_asset(asset_path, unreal.Blueprint)
        ctx().set_target(bp.get_outermost().get_name())
        return _summary(blueprint_issues(bp, compile_first), 200)

    @agent_tool()
    def validate_level(limit: int = 100) -> dict:
        """Validates the open level: missing meshes/materials, invalid physics collision,
        actors below KillZ, duplicated overlapping meshes, missing PlayerStart, AI without
        NavMesh, unsaved state.

        Args:
            limit: Maximum issues listed.
        """
        world = editor.editor_world()
        ctx().set_target(world.get_outermost().get_name() if world else '')
        return _summary(level_issues(limit), limit)

    @agent_tool()
    def find_missing_references(path: str = '/Game', limit: int = 200) -> dict:
        """Lists assets that reference packages which no longer exist (broken dependencies).

        Args:
            path: Folder to scan.
            limit: Maximum issues listed.
        """
        ctx().set_target(path)
        return _summary(check_missing_references(path, limit), limit)

    @agent_tool()
    def find_broken_assets(path: str = '/Game', load_assets: bool = False, limit: int = 200) -> dict:
        """Finds broken assets: redirectors pointing nowhere, assets whose class is no longer
        available (removed plugin/module) and, optionally, assets that fail to load.

        Args:
            path: Folder to scan.
            load_assets: Also try loading every asset (slow on large folders).
            limit: Maximum issues listed.
        """
        ctx().set_target(path)
        return _summary(check_broken_assets(path, load_assets, limit), limit)

    @agent_tool()
    def find_null_properties(target: str) -> dict:
        """Lists object/class references that are None on a Blueprint's defaults or on a level
        actor (its user variables and mesh components).

        Args:
            target: Blueprint asset path or actor label/path.
        """
        issues = []
        if target.startswith('/') and resolve.asset_exists(target):
            bp = resolve.load_asset(target, unreal.Blueprint)
            for var in bpu.variable_infos(bp, include_defaults=True):
                if var.get('type', '').startswith(('object<', 'class<', 'softobject', 'softclass')) and var.get('default') is None:
                    issues.append(_issue('warning', 'NULL_PROPERTY', f"{var['name']} ({var['type']}) is None", target))
            ctx().set_target(bp.get_outermost().get_name())
            return _summary(issues, 200)
        actor = resolve.find_actor(target)
        ctx().set_target(actor.get_path_name())
        bp = unreal.BlueprintEditorLibrary.get_blueprint_for_class(actor.get_class())
        bp = bp[0] if isinstance(bp, tuple) else bp
        if bp:
            for var in bpu.variable_infos(bp, include_defaults=False):
                if var.get('type', '').startswith(('object<', 'class<')):
                    try:
                        if actor.get_editor_property(var['name']) is None:
                            issues.append(_issue('warning', 'NULL_PROPERTY', f"{var['name']} is None on the instance",
                                                 actor.get_actor_label()))
                    except Exception:  # pylint: disable=broad-exception-caught
                        pass
        for comp in actor.get_components_by_class(unreal.StaticMeshComponent) or []:
            if comp.get_editor_property('static_mesh') is None:
                issues.append(_issue('warning', 'NULL_PROPERTY', f'{comp.get_name()}.static_mesh is None',
                                     actor.get_actor_label()))
        for comp in actor.get_components_by_class(unreal.SkeletalMeshComponent) or []:
            try:
                if comp.get_editor_property('skeletal_mesh_asset') is None:
                    issues.append(_issue('warning', 'NULL_PROPERTY', f'{comp.get_name()}.skeletal_mesh_asset is None',
                                         actor.get_actor_label()))
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        return _summary(issues, 200)

    @agent_tool()
    def find_missing_materials(path: str = '/Game', limit: int = 200) -> dict:
        """Finds static/skeletal mesh assets with empty material slots (loads meshes in the folder).

        Args:
            path: Folder to scan.
            limit: Maximum meshes to load.
        """
        issues = []
        for data in deps.assets_in_path(path, True, ['StaticMesh', 'SkeletalMesh'])[:limit]:
            mesh = data.get_asset()
            package = str(data.package_name)
            try:
                if isinstance(mesh, unreal.StaticMesh):
                    mats = [m.get_editor_property('material_interface') for m in mesh.get_editor_property('static_materials')]
                else:
                    mats = [m.get_editor_property('material_interface') for m in mesh.get_editor_property('materials')]
            except Exception:  # pylint: disable=broad-exception-caught
                continue
            for i, m in enumerate(mats):
                if m is None or m.get_outermost().get_name() in _DEFAULT_MATERIALS:
                    issues.append(_issue('warning', 'MISSING_MATERIAL', f'material slot {i} is empty/default', package))
        ctx().set_target(path)
        return _summary(issues, limit)

    @agent_tool()
    def find_invalid_collision(path: str = '/Game', limit: int = 200) -> dict:
        """Finds static meshes with no simple collision that are not set to use complex
        collision as simple (characters fall through / physics fails on them).

        Args:
            path: Folder to scan.
            limit: Maximum meshes to load.
        """
        issues = []
        for data in deps.assets_in_path(path, True, ['StaticMesh'])[:limit]:
            mesh = data.get_asset()
            if mesh is None:
                continue
            try:
                simple = unreal.EditorStaticMeshLibrary.get_simple_collision_count(mesh)
                complexity = unreal.EditorStaticMeshLibrary.get_collision_complexity(mesh)
            except Exception:  # pylint: disable=broad-exception-caught
                continue
            if simple == 0 and 'COMPLEX_AS_SIMPLE' not in str(complexity):
                issues.append(_issue('warning', 'NO_SIMPLE_COLLISION', 'mesh has no simple collision', str(data.package_name)))
        ctx().set_target(path)
        return _summary(issues, limit)

    @agent_tool()
    def find_duplicate_asset_names(path: str = '/Game', limit: int = 200) -> dict:
        """Finds assets that share a name across folders (ambiguous for humans and agents).

        Args:
            path: Folder to scan.
            limit: Maximum issues listed.
        """
        ctx().set_target(path)
        return _summary(check_duplicate_names(path), limit)

    @agent_tool()
    def check_naming_conventions(path: str = '/Game', prefixes_json: str | None = None, limit: int = 200) -> dict:
        """Checks asset name prefixes per class (BP_, M_, MI_, T_, SM_, SK_, ABP_, WBP_, NS_,
        BT_, BB_, IA_, IMC_...). Issues include suggested_name.

        Args:
            path: Folder to check.
            prefixes_json: Optional JSON {"ClassName": "Prefix_"} overriding/adding to the defaults.
            limit: Maximum issues listed.
        """
        prefixes = dict(DEFAULT_PREFIXES)
        prefixes.update(parse_json_arg(prefixes_json or '', 'prefixes_json', dict))
        ctx().set_target(path)
        return _summary(check_naming(path, prefixes), limit)

    @agent_tool()
    def check_asset_placement(path: str = '/Game', rules_json: str | None = None, limit: int = 200) -> dict:
        """Checks asset placement: assets in the /Game root, developer-folder assets referenced
        by shipping content, and optional class->folder rules.

        Args:
            path: Folder to check.
            rules_json: Optional JSON {"Texture2D": "/Game/Textures", "StaticMesh": "/Game/Meshes"}.
            limit: Maximum issues listed.
        """
        ctx().set_target(path)
        return _summary(check_placement(path, parse_json_arg(rules_json or '', 'rules_json', dict)), limit)

    @agent_tool()
    def find_unsaved_assets() -> dict:
        """Lists all packages (assets and levels) with unsaved changes."""
        issues = check_unsaved()
        return _summary(issues, 500)

    # ------------------------------------------------------------- auto-fix tools

    @agent_tool(mutates=True)
    def fix_naming_conventions(path: str = '/Game', prefixes_json: str | None = None, confirm: bool = False,
                               limit: int = 100) -> dict:
        """Renames assets to add the expected class prefix (references are fixed up; run
        AssetManagementTools.fix_redirectors afterwards). Without confirm=True, previews.

        Args:
            path: Folder to fix.
            prefixes_json: Optional JSON {"ClassName": "Prefix_"} overrides.
            confirm: Perform the renames.
            limit: Maximum assets renamed in one call.
        """
        prefixes = dict(DEFAULT_PREFIXES)
        prefixes.update(parse_json_arg(prefixes_json or '', 'prefixes_json', dict))
        issues = check_naming(path, prefixes)[:limit]
        plan = [{'asset': i['target'], 'new_name': i['suggested_name']} for i in issues]
        ctx().set_target(path)
        if not plan:
            ctx().mark_modified(False)
            return {'renamed': []}
        if not confirm:
            raise confirmation_required(f'{len(plan)} assets would be renamed.', {'plan': plan})
        renames, conflicts = [], []
        for item in plan:
            folder = item['asset'].rsplit('/', 1)[0]
            if resolve.asset_exists(f"{folder}/{item['new_name']}"):
                conflicts.append(item)
                continue
            renames.append(unreal.AssetRenameData(asset=resolve.load_asset(item['asset']), new_package_path=folder,
                                                  new_name=item['new_name']))
        if renames and not editor.asset_tools().rename_assets(renames):
            raise ToolError(Code.UE_OPERATION_FAILED, 'Renaming failed (see Output Log)', target=path)
        for c in conflicts:
            ctx().warn(f"skipped {c['asset']}: {c['new_name']} already exists", 'NAME_CONFLICT')
        return {'renamed': [p for p in plan if p not in conflicts], 'skipped': conflicts}

    @agent_tool(mutates=True)
    def remove_unused_blueprint_variables(asset_path: str, confirm: bool = False) -> dict:
        """Removes Blueprint variables that are not referenced by any graph. Without
        confirm=True, lists the candidates.

        Args:
            asset_path: Blueprint asset path.
            confirm: Remove them.
        """
        bp = resolve.load_asset(asset_path, unreal.Blueprint)
        ctx().set_target(bp.get_outermost().get_name())
        candidates = [i['message'].split(' ')[1] for i in blueprint_issues(bp, False) if i['code'] == 'UNUSED_VARIABLE']
        if not confirm:
            raise confirmation_required(f'{len(candidates)} variables look unused.', {'candidates': candidates})
        removed = unreal.BlueprintEditorLibrary.remove_unused_variables(bp)
        unreal.BlueprintEditorLibrary.compile_blueprint(bp)
        return {'removed_count': removed, 'candidates': candidates, 'status': bpu.status_name(bp)}

    @agent_tool(mutates=True)
    def add_simple_collision_to_meshes(asset_paths: list[str], shape: str = 'box') -> dict:
        """Adds a simple collision primitive to static meshes that have none.

        Args:
            asset_paths: StaticMesh assets.
            shape: box, sphere, capsule, ndop10_x, ndop18 or ndop26.
        """
        shapes = {'box': 'BOX', 'sphere': 'SPHERE', 'capsule': 'CAPSULE', 'ndop10_x': 'NDOP10_X',
                  'ndop18': 'NDOP18', 'ndop26': 'NDOP26'}
        if shape.lower() not in shapes:
            raise ToolError(Code.INVALID_ARGUMENT, f'shape must be one of {sorted(shapes)}')
        shape_type = getattr(unreal.ScriptCollisionShapeType, shapes[shape.lower()])
        changed, skipped = [], []
        for p in asset_paths:
            mesh = resolve.load_asset(p, unreal.StaticMesh)
            if unreal.EditorStaticMeshLibrary.get_simple_collision_count(mesh) > 0:
                skipped.append(p)
                continue
            unreal.EditorStaticMeshLibrary.add_simple_collisions(mesh, shape_type)
            changed.append(mesh.get_outermost().get_name())
        return {'changed': changed, 'skipped_already_had_collision': skipped}
