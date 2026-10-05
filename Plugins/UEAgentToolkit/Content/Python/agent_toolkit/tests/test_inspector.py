import unreal

from agent_toolkit.tests.base import TEST_ROOT, ToolTestCase
from agent_toolkit.toolsets.inspector import InspectorTools


class TestInspector(ToolTestCase):
    toolset = InspectorTools

    def test_get_editor_state(self):
        d = self.assertOk(self.call('get_editor_state'))
        self.assertIn('engine_version', d)
        self.assertFalse(d['is_pie_running'])

    def test_inspect_actor_and_component(self):
        actor = self.spawn(label='InspectMe')
        try:
            d = self.assertOk(self.call('inspect_actor', actor='InspectMe'))
            self.assertEqual(d['label'], 'InspectMe')
            comp = d['components'][0]['name']
            c = self.assertOk(self.call('inspect_component', actor='InspectMe', component=comp,
                                        property_names='Mobility'))
            self.assertIn('properties', c)
        finally:
            self.destroy(actor)

    def test_inspect_actor_missing(self):
        err = self.assertFails(self.call('inspect_actor', actor='DoesNotExist_123'), 'ACTOR_NOT_FOUND')
        self.assertTrue(err['likely_causes'])

    def test_inspect_blueprint_and_graph(self):
        bp = self.make_blueprint('BP_Inspect', unreal.Character)
        unreal.BlueprintEditorLibrary.add_member_variable(
            bp, 'Health', unreal.BlueprintEditorLibrary.get_basic_type_by_name('real'))
        unreal.BlueprintEditorLibrary.compile_blueprint(bp)
        path = f'{TEST_ROOT}/BP_Inspect'
        d = self.assertOk(self.call('inspect_blueprint', asset_path=path))
        self.assertEqual(d['parent_class'], '/Script/Engine.Character')
        self.assertIn('Health', [v['name'] for v in d['variables']])
        self.assertTrue(any(c['name'] == 'CharacterMovement' for c in d['components']), d['components'])
        g = self.assertOk(self.call('inspect_blueprint_graph', asset_path=path))
        self.assertGreater(g['node_count'], 0)
        self.assertIn('pins', g['nodes'][0])

    def test_inspect_blueprint_wrong_type_and_missing(self):
        self.assertFails(self.call('inspect_blueprint', asset_path='/Game/Nope/BP_Nope'), 'ASSET_NOT_FOUND')
        self.assertFails(self.call('inspect_blueprint', asset_path='not/a/path'), 'INVALID_ARGUMENT')
        self.assertFails(self.call('inspect_blueprint', asset_path='/Engine/BasicShapes/Cube'), 'WRONG_TYPE')

    def test_inspect_material(self):
        d = self.assertOk(self.call('inspect_material', asset_path='/Engine/BasicShapes/BasicShapeMaterial'))
        self.assertIn('parameters', d)
        self.assertIn('expressions', d)

    def test_inspect_asset_and_dependencies(self):
        d = self.assertOk(self.call('inspect_asset', asset_path='/Engine/BasicShapes/Cube'))
        self.assertEqual(d['class'], 'StaticMesh')
        t = self.assertOk(self.call('get_asset_dependencies', asset_path='/Engine/BasicShapes/Cube'))
        self.assertIn('nodes', t)
        self.assertOk(self.call('get_asset_referencers', asset_path='/Engine/BasicShapes/BasicShapeMaterial'))

    def test_inspect_level_world_project(self):
        d = self.assertOk(self.call('inspect_level', include_actor_list=True))
        self.assertIn('actor_count', d)
        self.assertOk(self.call('inspect_world_settings'))
        p = self.assertOk(self.call('inspect_project_settings'))
        self.assertIn('maps_and_modes', p)

    def test_class_hierarchy(self):
        d = self.assertOk(self.call('get_class_hierarchy', class_name='Character'))
        self.assertIn('Pawn', d['parents'])
        self.assertFails(self.call('get_class_hierarchy', class_name='NoSuchClassXYZ'), 'CLASS_NOT_FOUND')

    def test_inspect_skeleton_wrong_type(self):
        self.assertFails(self.call('inspect_skeleton', asset_path='/Engine/BasicShapes/Cube'), 'WRONG_TYPE')

    def test_inspect_behavior_tree_and_blackboard(self):
        bb = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            'BB_Test', TEST_ROOT, unreal.BlackboardData, unreal.BlackboardDataFactory())
        bt = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            'BT_Test', TEST_ROOT, unreal.BehaviorTree, unreal.BehaviorTreeFactory())
        bt.set_editor_property('blackboard_asset', bb)
        d = self.assertOk(self.call('inspect_behavior_tree', asset_path=f'{TEST_ROOT}/BT_Test'))
        self.assertEqual(d['blackboard'], f'{TEST_ROOT}/BB_Test')
        self.assertOk(self.call('inspect_blackboard', asset_path=f'{TEST_ROOT}/BB_Test'))
