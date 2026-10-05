"""Landscape sculpt/paint, World Partition Data Layers and Behavior Tree subtrees."""

import os

import unreal

from agent_toolkit.core import editor, native
from agent_toolkit.tests.base import TEST_ROOT, ToolTestCase, call_tool
from agent_toolkit.toolsets.ai import AITools
from agent_toolkit.toolsets.level import LevelTools
from agent_toolkit.toolsets.world import WorldTools
from agent_toolkit.toolsets.world_partition import WorldPartitionTools


class _NativeCase(ToolTestCase):
    def setUp(self):
        if not native.graph_library():
            self.skipTest('UEAgentToolkitNative not loaded')


class TestLandscapeSculptPaint(_NativeCase):
    toolset = WorldTools

    def tearDown(self):
        eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        for a in eas.get_all_level_actors():
            if isinstance(a, unreal.LandscapeProxy) and a.get_actor_label().startswith('Landscape'):
                if a.get_actor_label() == getattr(self, 'label', None):
                    eas.destroy_actor(a)
        super().tearDown()

    def z(self, x, y):
        d = self.assertOk(self.call('sample_landscape', points_json=f'[[{x}, {y}]]', landscape=self.label))
        return d['samples'][0]['z']

    def test_sculpt_and_paint(self):
        d = self.assertOk(self.call('create_landscape', location=[50000, 0, 0], components_x=2, components_y=2,
                                    quads_per_section=7))
        self.label = d['landscape']
        base = self.z(50000, 0)
        up = self.assertOk(self.call('sculpt_landscape', center=[50000, 0], radius=300, mode='raise', strength=200,
                                     falloff=0.5, landscape=self.label))
        self.assertGreater(up['vertices'], 0)
        self.assertAlmostEqual(self.z(50000, 0), base + 200, delta=2)
        self.assertAlmostEqual(self.z(50000 + 600, 0), base, delta=2)  # outside the brush: unchanged
        self.assertOk(self.call('sculpt_landscape', center=[50000, 0], radius=400, mode='flatten', target_height=base + 50,
                                landscape=self.label))
        self.assertAlmostEqual(self.z(50000, 0), base + 50, delta=2)
        road = self.assertOk(self.call('sculpt_landscape', center=[49500, -400], radius=150, mode='lower', strength=30,
                                       path_json='[[50500, -400]]', landscape=self.label))
        self.assertGreater(road['stamps'], 5)
        self.assertFails(self.call('sculpt_landscape', center=[0, 900000], radius=100, landscape=self.label),
                         'INVALID_ARGUMENT')
        self.assertFails(self.call('sculpt_landscape', center=[50000, 0], radius=100, mode='dig', landscape=self.label),
                         'INVALID_ARGUMENT')

        p = self.assertOk(self.call('paint_landscape_layer', layer_name='Grass', center=[50000, 0], radius=300,
                                    strength=1.0, falloff=0.2, layer_info_folder=TEST_ROOT, landscape=self.label))
        self.assertTrue(p['created_layer_info'])
        self.assertTrue(p['layer_info'].startswith(TEST_ROOT))
        s = self.assertOk(self.call('sample_landscape', points_json='[[50000, 0], [50650, 0]]', layer_name='Grass',
                                    landscape=self.label))
        self.assertAlmostEqual(s['samples'][0]['weight'], 1.0, delta=0.01)
        self.assertEqual(s['samples'][1]['weight'], 0.0)
        self.assertIn('Grass', [l['name'] for l in s['layers']])
        again = self.assertOk(self.call('paint_landscape_layer', layer_name='Grass', center=[50000, 0], radius=100,
                                        strength=-1.0, falloff=0, landscape=self.label))
        self.assertFalse(again['created_layer_info'])
        s = self.assertOk(self.call('sample_landscape', points_json='[[50000, 0]]', layer_name='Grass',
                                    landscape=self.label))
        self.assertEqual(s['samples'][0]['weight'], 0.0)


class TestWorldPartition(ToolTestCase):
    toolset = WorldPartitionTools

    def setUp(self):
        world = editor.editor_world()
        self.original = world.get_outermost().get_name() if world else ''

    def tearDown(self):
        les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        if self.original and unreal.EditorAssetLibrary.does_asset_exist(self.original):
            les.load_level(self.original)
        else:
            les.load_level('/Engine/Maps/Templates/OpenWorld')
        unreal.SystemLibrary.collect_garbage()  # release the test level packages so they can be deleted
        for folder in ('/Game/__ExternalActors__/__AgentToolkitTests__', '/Game/__ExternalObjects__/__AgentToolkitTests__'):
            if unreal.EditorAssetLibrary.does_directory_exist(folder):
                unreal.EditorAssetLibrary.delete_directory(folder)
        for root in ('__ExternalActors__', '__ExternalObjects__'):
            path = os.path.join(unreal.Paths.project_content_dir(), root)
            if os.path.isdir(path) and not os.listdir(path):
                os.rmdir(path)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        # Levels opened during the test can stay referenced (undo history) and survive asset deletion.
        folder = os.path.join(unreal.Paths.project_content_dir(), '__AgentToolkitTests__')
        for name in ('L_WP.umap', 'L_Plain.umap'):
            path = os.path.join(folder, name)
            if os.path.exists(path):
                os.remove(path)
        if os.path.isdir(folder) and not os.listdir(folder):
            os.rmdir(folder)

    def test_data_layers(self):
        if editor.is_pie_running():
            self.skipTest('PIE')
        lv = call_tool(LevelTools, 'create_level', asset_path=f'{TEST_ROOT}/L_WP', template='empty', world_partition=True,
                       confirm_discard_unsaved=True)
        self.assertTrue(lv['success'], lv['errors'])
        self.assertTrue(self.assertOk(self.call('inspect_world_partition'))['world_partition'])
        a = call_tool(LevelTools, 'spawn_actor', class_or_asset='StaticMeshActor', label='WP_Rock', location=[0, 0, 0], rotation=[0, 0, 0], scale=[1, 1, 1])
        self.assertTrue(a['success'], a['errors'])
        call_tool(LevelTools, 'spawn_actor', class_or_asset='StaticMeshActor', label='WP_Tree', location=[100, 0, 0], rotation=[0, 0, 0], scale=[1, 1, 1])

        dl = self.assertOk(self.call('create_data_layer', name='DL_Night', folder=TEST_ROOT,
                                     initial_runtime_state='loaded'))
        self.assertEqual(dl['type'], 'runtime')
        self.assertEqual(dl['initial_runtime_state'], 'loaded')
        self.assertFails(self.call('create_data_layer', name='DL_Night', folder=TEST_ROOT), 'ALREADY_EXISTS')
        self.assertOk(self.call('create_data_layer', name='DL_EditorOnly', folder=TEST_ROOT, layer_type='editor'))
        self.assertFails(self.call('create_data_layer', name='DL_X', layer_type='magic'), 'INVALID_ARGUMENT')

        r = self.assertOk(self.call('assign_actors_to_data_layer', data_layer='DL_Night', actors='WP_Rock,WP_Tree'))
        self.assertEqual(r['actor_count'], 2)
        r = self.assertOk(self.call('assign_actors_to_data_layer', data_layer='DL_Night', actors='WP_Tree', remove=True))
        self.assertEqual(r['actor_count'], 1)
        self.assertFails(self.call('assign_actors_to_data_layer', data_layer='DL_Nope', actors='WP_Rock'),
                         'OBJECT_NOT_FOUND')

        st = self.assertOk(self.call('set_data_layer_state', data_layer='DL_Night', initial_runtime_state='activated',
                                     visible_in_editor=False))
        self.assertEqual(st['data_layer']['initial_runtime_state'], 'activated')
        self.assertFalse(st['data_layer']['visible_in_editor'])
        self.assertFails(self.call('set_data_layer_state', data_layer='DL_EditorOnly', initial_runtime_state='loaded'),
                         'INVALID_ARGUMENT')

        s = self.assertOk(self.call('set_actor_streaming', actors='WP_Tree', is_spatially_loaded=False))
        self.assertFalse(s['actors'][0]['is_spatially_loaded'])
        info = self.assertOk(self.call('inspect_world_partition'))
        self.assertIn('WP_Tree', info['always_loaded_actors'])
        self.assertEqual({d['name'] for d in info['data_layers']}, {'DL_Night', 'DL_EditorOnly'})

        self.assertFails(self.call('delete_data_layer', data_layer='DL_Night'), 'CONFIRMATION_REQUIRED')
        gone = self.assertOk(self.call('delete_data_layer', data_layer='DL_Night', confirm=True))
        self.assertEqual(gone['unassigned_actors'], ['WP_Rock'])
        self.assertEqual(len(self.assertOk(self.call('inspect_world_partition'))['data_layers']), 1)

    def test_not_partitioned(self):
        if editor.is_pie_running():
            self.skipTest('PIE')
        lv = call_tool(LevelTools, 'create_level', asset_path=f'{TEST_ROOT}/L_Plain', template='empty',
                       confirm_discard_unsaved=True)
        self.assertTrue(lv['success'], lv['errors'])
        self.assertFalse(self.assertOk(self.call('inspect_world_partition'))['world_partition'])
        self.assertFails(self.call('create_data_layer', name='DL_A', folder=TEST_ROOT), 'EDITOR_STATE')


class TestBTSubtree(_NativeCase):
    toolset = AITools

    def _bt(self, name, bb=None):
        bt = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, TEST_ROOT, unreal.BehaviorTree,
                                                                     unreal.BehaviorTreeFactory())
        if bb is not None:
            bt.set_editor_property('blackboard_asset', bb)
        return f'{TEST_ROOT}/{name}'

    def test_subtree(self):
        tools = unreal.AssetToolsHelpers.get_asset_tools()
        bb = tools.create_asset('BB_Main', TEST_ROOT, unreal.BlackboardData, unreal.BlackboardDataFactory())
        other = tools.create_asset('BB_Other', TEST_ROOT, unreal.BlackboardData, unreal.BlackboardDataFactory())
        main = self._bt('BT_Main', bb)
        patrol = self._bt('BT_Patrol', bb)
        odd = self._bt('BT_Odd', other)
        seq = self.assertOk(self.call('add_bt_node', behavior_tree_path=main, node_class='Selector'))['node']
        d = self.assertOk(self.call('add_bt_subtree', behavior_tree_path=main, subtree_path=patrol, parent_node=seq))
        self.assertEqual(d['subtree'], patrol)
        tree = self.assertOk(self.call('inspect_bt_graph', behavior_tree_path=main))
        child = tree['root']['children'][0]['children'][0]
        self.assertIn('RunBehavior', child['class'])
        runtime = unreal.load_asset(main).get_editor_property('root_node').get_editor_property('children')[0]
        task = runtime.get_editor_property('child_task')
        self.assertEqual(task.get_editor_property('behavior_asset').get_path_name(), unreal.load_asset(patrol).get_path_name())
        env = self.call('add_bt_subtree', behavior_tree_path=main, subtree_path=odd, parent_node=seq)
        self.assertOk(env)
        self.assertIn('BLACKBOARD_MISMATCH', [w['code'] for w in env['warnings']])
        self.assertFails(self.call('add_bt_subtree', behavior_tree_path=main, subtree_path=main, parent_node=seq),
                         'INVALID_ARGUMENT')
        self.assertFails(self.call('add_bt_subtree', behavior_tree_path=main, subtree_path=f'{TEST_ROOT}/BT_None',
                                   parent_node=seq), 'ASSET_NOT_FOUND')
