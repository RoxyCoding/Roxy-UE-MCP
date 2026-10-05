import os
import tempfile

import unreal

from agent_toolkit.tests.base import TEST_ROOT, ToolTestCase
from agent_toolkit.toolsets.assets import AssetManagementTools


class TestAssets(ToolTestCase):
    toolset = AssetManagementTools

    def test_create_asset_generic_and_duplicate_refused(self):
        d = self.assertOk(self.call('create_asset', asset_path=f'{TEST_ROOT}/IA_TestJump', asset_class='InputAction'))
        self.assertEqual(d['class'], 'InputAction')
        self.assertFails(self.call('create_asset', asset_path=f'{TEST_ROOT}/IA_TestJump', asset_class='InputAction'),
                         'ALREADY_EXISTS')

    def test_create_blueprint_with_parent(self):
        env = self.call('create_asset', asset_path=f'{TEST_ROOT}/BP_TestChar', asset_class='Blueprint',
                        factory_properties_json='{"parent_class": "Character"}')
        self.assertOk(env)
        self.assertTrue(env['modified'])
        self.assertIn(f'{TEST_ROOT}/BP_TestChar', env['dirtied_packages'])
        bp = unreal.load_asset(f'{TEST_ROOT}/BP_TestChar')
        self.assertEqual(unreal.BlueprintEditorLibrary.get_blueprint_parent_class(bp).get_name(), 'Character')

    def test_create_asset_bad_class(self):
        self.assertFails(self.call('create_asset', asset_path=f'{TEST_ROOT}/X_Bad', asset_class='NoSuchClassABC'),
                         'CLASS_NOT_FOUND')
        self.assertFails(self.call('create_asset', asset_path=f'{TEST_ROOT}/X_Bad', asset_class='Actor'),
                         'NOT_SUPPORTED')

    def test_search_rename_move(self):
        self.assertOk(self.call('create_asset', asset_path=f'{TEST_ROOT}/IA_Move', asset_class='InputAction'))
        s = self.assertOk(self.call('search_assets', query='ia_move', path=TEST_ROOT))
        self.assertEqual(s['total_matches'], 1)
        self.assertOk(self.call('rename_asset', asset_path=f'{TEST_ROOT}/IA_Move', new_name='IA_Moved'))
        self.assertTrue(unreal.EditorAssetLibrary.does_asset_exist(f'{TEST_ROOT}/IA_Moved'))
        dry = self.assertOk(self.call('move_assets', asset_paths=[f'{TEST_ROOT}/IA_Moved'],
                                      destination_folder=f'{TEST_ROOT}/Sub', dry_run=True))
        self.assertTrue(dry['dry_run'])
        self.assertOk(self.call('move_assets', asset_paths=[f'{TEST_ROOT}/IA_Moved'], destination_folder=f'{TEST_ROOT}/Sub'))
        self.assertTrue(unreal.EditorAssetLibrary.does_asset_exist(f'{TEST_ROOT}/Sub/IA_Moved'))

    def test_delete_requires_confirmation_and_refuses_referenced(self):
        self.assertOk(self.call('create_asset', asset_path=f'{TEST_ROOT}/IA_Del', asset_class='InputAction'))
        err = self.assertFails(self.call('delete_assets', asset_paths=[f'{TEST_ROOT}/IA_Del']), 'CONFIRMATION_REQUIRED')
        self.assertTrue(err['retryable'])
        self.assertTrue(unreal.EditorAssetLibrary.does_asset_exist(f'{TEST_ROOT}/IA_Del'))
        d = self.assertOk(self.call('delete_assets', asset_paths=[f'{TEST_ROOT}/IA_Del'], confirm=True, backup_first=False))
        self.assertEqual(d['deleted'], [f'{TEST_ROOT}/IA_Del'])
        self.assertFails(self.call('delete_assets', asset_paths=[f'{TEST_ROOT}/IA_Nope']), 'ASSET_NOT_FOUND')

    def test_import_and_export_texture(self):
        # Write a tiny valid PNG to disk and import it.
        png = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde'
               b'\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82')
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, 'T_AgentTest.png')
        with open(path, 'wb') as f:
            f.write(png)
        d = self.assertOk(self.call('import_files', source_files=[path], destination_folder=TEST_ROOT))
        self.assertEqual(len(d['imported']), 1)
        self.assertFails(self.call('import_files', source_files=[path], destination_folder=TEST_ROOT), 'ALREADY_EXISTS')
        self.assertFails(self.call('import_files', source_files=['C:/nope/none.png'], destination_folder=TEST_ROOT),
                         'INVALID_ARGUMENT')
        e = self.assertOk(self.call('export_assets', asset_paths=[f'{TEST_ROOT}/T_AgentTest'],
                                    export_directory=os.path.join(folder, 'out')))
        self.assertIn('exported_files', e)

    def test_redirectors_and_unused(self):
        self.assertOk(self.call('find_redirectors', path=TEST_ROOT))
        self.assertOk(self.call('create_asset', asset_path=f'{TEST_ROOT}/IA_Unused', asset_class='InputAction'))
        u = self.assertOk(self.call('find_unused_assets', path=TEST_ROOT))
        self.assertIn(f'{TEST_ROOT}/IA_Unused', [x['path'] for x in u['unused']])
        f = self.assertOk(self.call('fix_redirectors', path=TEST_ROOT))
        self.assertEqual(f.get('redirectors', 0), 0)

    def test_save_dirty_dry_run(self):
        d = self.assertOk(self.call('save_dirty_assets', path_prefix=TEST_ROOT, dry_run=True))
        self.assertTrue(d['dry_run'])
