import unreal

from agent_toolkit.tests.base import TEST_ROOT, ToolTestCase, call_tool, requires_editor_ui
from agent_toolkit.toolsets.safety import SafetyTools
from agent_toolkit.toolsets.validation import ValidationTools


class TestValidation(ToolTestCase):
    toolset = ValidationTools

    def test_validate_project_report_shape(self):
        unreal.AssetToolsHelpers.get_asset_tools().create_asset('BadName', TEST_ROOT, unreal.InputAction,
                                                                unreal.InputAction_Factory())
        d = self.assertOk(self.call('validate_project', path=TEST_ROOT))
        for section in ('missing_references', 'broken_assets', 'redirectors', 'duplicate_names', 'naming', 'unsaved'):
            self.assertIn(section, d['sections'])
        naming = d['sections']['naming']['issues']
        self.assertTrue(any(i['target'].endswith('/BadName') and i['suggested_name'] == 'IA_BadName' for i in naming))

    def test_empty_and_missing_scope(self):
        empty = f'{TEST_ROOT}/EmptyFolder'
        unreal.get_editor_subsystem(unreal.EditorAssetSubsystem).make_directory(empty)
        env = self.call('validate_project', path=empty)
        d = self.assertOk(env)
        self.assertEqual(d['scanned_assets'], 0)
        self.assertTrue(any(w['code'] == 'EMPTY_SCOPE' for w in env['warnings']), env['warnings'])
        self.assertEqual(self.assertOk(self.call('find_missing_references', path=empty))['scanned_assets'], 0)
        self.assertFails(self.call('validate_project', path='/Game/NoSuchFolderXYZ'), 'OBJECT_NOT_FOUND')

    def test_fix_naming_requires_confirm_then_renames(self):
        unreal.AssetToolsHelpers.get_asset_tools().create_asset('Jump', TEST_ROOT, unreal.InputAction,
                                                                unreal.InputAction_Factory())
        self.assertFails(self.call('fix_naming_conventions', path=TEST_ROOT), 'CONFIRMATION_REQUIRED')
        d = self.assertOk(self.call('fix_naming_conventions', path=TEST_ROOT, confirm=True))
        self.assertTrue(any(r['new_name'] == 'IA_Jump' for r in d['renamed']))
        self.assertTrue(unreal.EditorAssetLibrary.does_asset_exist(f'{TEST_ROOT}/IA_Jump'))

    def test_validate_blueprint_finds_unused_and_null(self):
        bp = self.make_blueprint('BP_Val')
        bel = unreal.BlueprintEditorLibrary
        bel.add_member_variable(bp, 'UnusedCount', bel.get_basic_type_by_name('int'))
        bel.add_member_variable(bp, 'TargetActor', bel.get_object_reference_type(unreal.Actor.static_class()))
        d = self.assertOk(self.call('validate_blueprint', asset_path=f'{TEST_ROOT}/BP_Val'))
        codes = {i['code'] for i in d['issues']}
        self.assertIn('UNUSED_VARIABLE', codes)
        self.assertIn('NULL_PROPERTY', codes)
        n = self.assertOk(self.call('find_null_properties', target=f'{TEST_ROOT}/BP_Val'))
        self.assertTrue(n['issues'])
        self.assertFails(self.call('remove_unused_blueprint_variables', asset_path=f'{TEST_ROOT}/BP_Val'),
                         'CONFIRMATION_REQUIRED')
        r = self.assertOk(self.call('remove_unused_blueprint_variables', asset_path=f'{TEST_ROOT}/BP_Val', confirm=True))
        self.assertGreaterEqual(r['removed_count'], 1)

    def test_validate_level_and_scans(self):
        eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        a = eas.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(0, 0, 0))
        a.set_actor_label('VAL_EmptyMesh')
        try:
            d = self.assertOk(self.call('validate_level'))
            self.assertTrue(any(i['code'] == 'MISSING_MESH' and i['target'] == 'VAL_EmptyMesh' for i in d['issues']))
            n = self.assertOk(self.call('find_null_properties', target='VAL_EmptyMesh'))
            self.assertTrue(n['issues'])
        finally:
            eas.destroy_actor(a)
        for tool in ('find_missing_references', 'find_broken_assets', 'find_missing_materials',
                     'find_invalid_collision', 'find_duplicate_asset_names', 'check_naming_conventions',
                     'check_asset_placement'):
            self.assertOk(self.call(tool, path=TEST_ROOT))
        self.assertOk(self.call('find_unsaved_assets'))
        self.assertFails(self.call('check_naming_conventions', path=TEST_ROOT, prefixes_json='{bad'), 'INVALID_ARGUMENT')

    def test_validate_assets_epic_validators(self):
        self.make_blueprint('BP_EpicVal')
        d = self.assertOk(self.call('validate_assets', asset_paths=[f'{TEST_ROOT}/BP_EpicVal']))
        self.assertIn('num_checked', d)

    def test_add_simple_collision(self):
        self.assertFails(self.call('add_simple_collision_to_meshes', asset_paths=['/Engine/BasicShapes/Cube'],
                                   shape='hexagon'), 'INVALID_ARGUMENT')


class TestSafety(ToolTestCase):
    toolset = SafetyTools

    @requires_editor_ui
    def test_transaction_groups_edits_into_one_undo(self):
        from agent_toolkit.toolsets.level import LevelTools
        self.assertOk(self.call('begin_transaction', description='group test'))
        for i in range(2):
            env = call_tool(LevelTools, 'add_player_start', location=[i * 100, 0, 100], label=f'SAFE_{i}')
            self.assertTrue(env['success'], env['errors'])
        self.assertOk(self.call('end_transaction'))
        labels = lambda: [a.get_actor_label() for a in
                          unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()]
        self.assertIn('SAFE_1', labels())
        self.assertOk(self.call('undo'))
        self.assertNotIn('SAFE_0', labels())
        self.assertNotIn('SAFE_1', labels())
        self.assertOk(self.call('redo'))
        self.assertIn('SAFE_1', labels())
        self.assertFails(self.call('end_transaction'), 'EDITOR_STATE')

    def test_state_journal_modified(self):
        self.assertOk(self.call('get_undo_state'))
        self.assertOk(self.call('get_change_journal'))
        self.assertIn('content', self.assertOk(self.call('list_modified_assets')))

    def test_preview_deletion_and_backups(self):
        d = self.assertOk(self.call('preview_asset_deletion', asset_paths=['/Engine/BasicShapes/BasicShapeMaterial',
                                                                            '/Game/DoesNot/Exist']))
        self.assertEqual({a['package'] for a in d['assets']}, {'/Engine/BasicShapes/BasicShapeMaterial', '/Game/DoesNot/Exist'})
        self.assertFalse(next(a for a in d['assets'] if a['package'] == '/Game/DoesNot/Exist')['safe_to_delete'])
        b = self.assertOk(self.call('create_backup', asset_paths=['/Engine/BasicShapes/Cube'], label='test'))
        self.assertTrue(b['backup_id'])
        listed = self.assertOk(self.call('list_backups'))
        self.assertIn(b['backup_id'], [x['id'] for x in listed['backups']])
        self.assertFails(self.call('restore_backup', backup_id=b['backup_id']), 'CONFIRMATION_REQUIRED')
        self.assertFails(self.call('restore_backup', backup_id='nope'), 'OBJECT_NOT_FOUND')
        self.assertFails(self.call('restore_save_point', name='never-created'), 'OBJECT_NOT_FOUND')
