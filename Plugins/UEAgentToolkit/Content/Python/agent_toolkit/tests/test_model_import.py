import unreal
import os
import tempfile
from unittest.mock import patch

from agent_toolkit.core import editor, mesh_quality
from agent_toolkit.core.errors import ToolError
from agent_toolkit.tests.base import TEST_ROOT, ToolTestCase, call_tool, requires_editor_ui
from agent_toolkit.toolsets.model_import import ModelImportTools, classify_texture
from agent_toolkit.toolsets.assets import AssetManagementTools

EAL = unreal.EditorAssetLibrary


class TestModelImport(ToolTestCase):
    toolset = ModelImportTools

    @requires_editor_ui
    def test_mesh_basis_gate(self):
        mesh = EAL.duplicate_asset('/Engine/BasicShapes/Cube', f'{TEST_ROOT}/SM_Basis')
        self.assertEqual(mesh_quality.basis_errors(mesh), [])
        self.assertTrue(mesh_quality.basis_errors(unreal.StaticMesh()))
        subsystem = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
        with patch.object(mesh_quality, 'basis_errors', return_value=[]), \
                patch.object(mesh_quality.unreal, 'get_editor_subsystem') as get_subsystem:
            report = mesh_quality.check_meshes([mesh], repair=True)
            self.assertFalse(report[0]['repaired'])
            get_subsystem.assert_not_called()
        with patch.object(mesh_quality, 'basis_errors', side_effect=[['invalid normal'], []]):
            report = mesh_quality.check_meshes([mesh], repair=True)
            self.assertTrue(report[0]['repaired'])
        settings = subsystem.get_lod_build_settings(mesh, 0)
        self.assertTrue(settings.get_editor_property('recompute_normals'))
        self.assertTrue(settings.get_editor_property('recompute_tangents'))
        self.assertTrue(settings.get_editor_property('remove_degenerates'))
        with patch.object(mesh_quality, 'basis_errors', return_value=['collapsed UV basis']):
            with self.assertRaises(ToolError) as caught:
                mesh_quality.check_meshes([mesh], repair=True)
            self.assertTrue(caught.exception.details['mesh_basis_checks'][0]['errors'])
            self.assertFails(self.call('repair_mesh_tangent_basis', asset_paths=mesh.get_path_name()),
                             'UE_OPERATION_FAILED')
            self.assertFails(self.call('inspect_imported_meshes', asset_paths=mesh.get_path_name()),
                             'UE_OPERATION_FAILED')
            package = mesh.get_outermost().get_name()
            self.assertIn(package, editor.dirty_package_names())
            self.assertFails(call_tool(AssetManagementTools, 'save_dirty_assets', path_prefix=TEST_ROOT),
                             'UE_OPERATION_FAILED')
            self.assertIn(package, editor.dirty_package_names())
        self.assertFails(self.call('repair_mesh_tangent_basis', asset_paths=''), 'INVALID_ARGUMENT')

    @requires_editor_ui
    def test_import_does_not_complete_with_invalid_basis(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, 'BasisTriangle.obj')
            with open(source, 'w', encoding='ascii') as stream:
                stream.write('v 0 0 0\nv 100 0 0\nv 0 100 0\n'
                             'vt 0 0\nvt 1 0\nvt 0 1\nvn 0 0 1\nf 1/1/1 2/2/1 3/3/1\n')
            with patch.object(mesh_quality, 'basis_errors', return_value=['invalid tangent']):
                env = call_tool(AssetManagementTools, 'import_files', source_files=[source],
                                destination_folder=f'{TEST_ROOT}/BasisImport')
                self.assertFails(env, 'UE_OPERATION_FAILED')
                path = env['details']['mesh_basis_checks'][0]['asset']
                self.assertFails(call_tool(AssetManagementTools, 'reimport_assets', asset_paths=[path]),
                                 'UE_OPERATION_FAILED')
                self.assertFails(self.call('import_blender_model', source_file=source,
                                           destination_folder=f'{TEST_ROOT}/BasisBlender', create_materials=False),
                                 'UE_OPERATION_FAILED')

    def test_classify_texture(self):
        self.assertEqual(classify_texture('T_Rock_BaseColor'), ('basecolor', 'Rock'))
        self.assertEqual(classify_texture('T_Rock_Normal_GL'), ('normal', 'Rock'))
        self.assertEqual(classify_texture('Chair_ORM'), ('orm', 'Chair'))
        self.assertEqual(classify_texture('T_Foo')[0], None)

    def test_textures_and_materials(self):
        folder = f'{TEST_ROOT}/ModelImport'
        EAL.duplicate_asset('/Engine/EngineResources/WhiteSquareTexture', f'{folder}/T_Crate_BaseColor')
        EAL.duplicate_asset('/Engine/EngineMaterials/DefaultNormal', f'{folder}/T_Crate_Normal')
        EAL.duplicate_asset('/Engine/BasicShapes/Cube', f'{folder}/Crate')
        fixed = self.assertOk(self.call('fix_texture_settings', folder=folder))
        normal = unreal.load_asset(f'{folder}/T_Crate_Normal')
        self.assertTrue(normal.get_editor_property('flip_green_channel'))
        self.assertFalse(normal.get_editor_property('srgb'))
        self.assertEqual(len(fixed['textures']), 2)
        made = self.assertOk(self.call('create_materials_from_textures', texture_folder=folder,
                                       assign_to_meshes=f'{folder}/Crate'))
        self.assertIn('Crate', made['materials'])
        self.assertTrue(made['assigned'])
        mesh = unreal.load_asset(f'{folder}/Crate')
        self.assertIn('MI_Crate', mesh.get_material(0).get_name())

    def test_inspect_meshes(self):
        folder = f'{TEST_ROOT}/ModelInspect'
        EAL.duplicate_asset('/Engine/BasicShapes/Cube', f'{folder}/SM_Box')
        d = self.assertOk(self.call('inspect_imported_meshes', asset_paths=f'{folder}/SM_Box'))
        box = d['meshes'][0]
        self.assertAlmostEqual(box['size_cm'][0], 100, delta=1)
        codes = [i['code'] for i in box['issues']]
        self.assertNotIn('SCALE_TOO_SMALL', codes)
        self.assertNotIn('SCALE_TOO_LARGE', codes)
        self.assertFails(self.call('inspect_imported_meshes', folder=f'{TEST_ROOT}/Nothing'), 'OBJECT_NOT_FOUND')
        self.assertFails(self.call('import_blender_model', source_file='C:/nope.fbx',
                                   destination_folder=folder), 'INVALID_ARGUMENT')
