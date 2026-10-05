import unreal

from agent_toolkit.tests.base import TEST_ROOT, ToolTestCase
from agent_toolkit.toolsets.model_import import ModelImportTools, classify_texture

EAL = unreal.EditorAssetLibrary


class TestModelImport(ToolTestCase):
    toolset = ModelImportTools

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
