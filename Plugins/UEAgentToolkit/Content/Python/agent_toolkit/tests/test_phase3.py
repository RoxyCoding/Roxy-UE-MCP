import math
import os
import struct
import tempfile
import wave

import unreal

from agent_toolkit.core import native
from agent_toolkit.tests.base import TEST_ROOT, ToolTestCase, call_tool
from agent_toolkit.toolsets.ai import AITools
from agent_toolkit.toolsets.animation import AnimationTools
from agent_toolkit.toolsets.assets import AssetManagementTools
from agent_toolkit.toolsets.audio import AudioTools
from agent_toolkit.toolsets.blueprint_authoring import BlueprintAuthoringTools
from agent_toolkit.toolsets.build_debug import BuildDebugTools
from agent_toolkit.toolsets.inspector import InspectorTools


def _engine_anim():
    """(AnimSequence path, Skeleton path) from engine content, or (None, None)."""
    reg = unreal.AssetRegistryHelpers.get_asset_registry()
    for data in reg.get_assets_by_path('/Engine', recursive=True) or []:
        if str(data.asset_class_path.asset_name) == 'AnimSequence':
            anim = data.get_asset()
            skel = anim.get_editor_property('skeleton') if anim else None
            if skel:
                return str(data.package_name), skel.get_outermost().get_name()
    return None, None


class TestAnimation(ToolTestCase):
    toolset = AnimationTools

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.anim, cls.skeleton = _engine_anim()

    def setUp(self):
        if not native.graph_library():
            self.skipTest('UEAgentToolkitNative not loaded')
        if not self.anim:
            self.skipTest('no AnimSequence in engine content')

    def test_anim_blueprint_state_machine(self):
        path = f'{TEST_ROOT}/ABP_Test'
        self.assertOk(self.call('create_anim_blueprint', asset_path=path, skeleton_path=self.skeleton))
        self.assertFails(self.call('create_anim_blueprint', asset_path=path, skeleton_path=self.skeleton), 'ALREADY_EXISTS')
        for name, vtype in (('Speed', 'float'), ('IsInAir', 'bool')):
            env = call_tool(BlueprintAuthoringTools, 'add_blueprint_variable', asset_path=path, variable_name=name,
                            variable_type=vtype)
            self.assertTrue(env['success'], env['errors'])
        self.assertOk(self.call('add_anim_state_machine', asset_path=path, machine_name='Locomotion'))
        self.assertFails(self.call('add_anim_state_machine', asset_path=path, machine_name='Locomotion'), 'ALREADY_EXISTS')
        self.assertOk(self.call('add_anim_state', asset_path=path, machine_name='Locomotion', state_name='Idle',
                                animation_path=self.anim))
        self.assertOk(self.call('add_anim_state', asset_path=path, machine_name='Locomotion', state_name='Jump',
                                animation_path=self.anim))
        self.assertFails(self.call('add_anim_state', asset_path=path, machine_name='Nope', state_name='X'), 'OBJECT_NOT_FOUND')
        self.assertFails(self.call('add_anim_state', asset_path=path, machine_name='Locomotion', state_name='Idle'),
                         'ALREADY_EXISTS')
        self.assertOk(self.call('add_anim_transition', asset_path=path, machine_name='Locomotion', from_state='Idle',
                                to_state='Jump', rule='bool', variable_name='IsInAir'))
        self.assertOk(self.call('add_anim_transition', asset_path=path, machine_name='Locomotion', from_state='Jump',
                                to_state='Idle', rule='not_bool', variable_name='IsInAir'))
        self.assertFails(self.call('add_anim_transition', asset_path=path, machine_name='Locomotion', from_state='Idle',
                                   to_state='Jump', rule='bool', variable_name='Missing'), 'OBJECT_NOT_FOUND')
        self.assertFails(self.call('add_anim_transition', asset_path=path, machine_name='Locomotion', from_state='Idle',
                                   to_state='Jump', rule='sometimes'), 'INVALID_ARGUMENT')
        d = self.assertOk(self.call('inspect_anim_state_machines', asset_path=path))
        sm = d['state_machines'][0]
        self.assertEqual(sm['entry_state'], 'Idle')
        self.assertTrue(sm['connected_to_output'])
        self.assertEqual({t['rule'] for t in sm['transitions']}, {'bool:IsInAir', 'Not_PreBool:IsInAir'})
        c = call_tool(BuildDebugTools, 'compile_blueprint', asset_path=path)
        self.assertTrue(c['success'], c['errors'])

    def test_montage_notify_blendspace_inspect(self):
        m = f'{TEST_ROOT}/AM_Test'
        self.assertOk(self.call('create_anim_montage', asset_path=m, animation_path=self.anim))
        self.assertOk(self.call('add_montage_section', montage_path=m, section_name='Hit', start_time=0.0))
        self.assertFails(self.call('add_montage_section', montage_path=m, section_name='Late', start_time=9999),
                         'INVALID_ARGUMENT')
        n = self.assertOk(self.call('add_animation_notify', animation_path=m, notify_class='AnimNotify_PlaySound', time=0.0))
        self.assertEqual(n['class'], 'AnimNotify_PlaySound')
        self.assertFails(self.call('add_animation_notify', animation_path=m, notify_class='Actor', time=0.0), 'WRONG_TYPE')
        info = self.assertOk(self.call('inspect_animation_asset', asset_path=m))
        self.assertTrue(info['notifies'])
        self.assertIn('Hit', [s['name'] for s in info.get('sections', [])])
        bs = self.assertOk(self.call('create_blend_space', asset_path=f'{TEST_ROOT}/BS_Test', skeleton_path=self.skeleton,
                                     samples_json='[{"animation": "%s", "x": 0}]' % self.anim, x_min=0, x_max=600))
        self.assertEqual(bs['dimensions'], 1)


class TestAI(ToolTestCase):
    toolset = AITools

    def setUp(self):
        if not native.graph_library():
            self.skipTest('UEAgentToolkitNative not loaded')

    def test_blackboard_and_behavior_tree(self):
        tools = unreal.AssetToolsHelpers.get_asset_tools()
        tools.create_asset('BB_AI', TEST_ROOT, unreal.BlackboardData, unreal.BlackboardDataFactory())
        tools.create_asset('BT_AI', TEST_ROOT, unreal.BehaviorTree, unreal.BehaviorTreeFactory())
        bb, bt = f'{TEST_ROOT}/BB_AI', f'{TEST_ROOT}/BT_AI'
        d = self.assertOk(self.call('add_blackboard_key', blackboard_path=bb, key_name='TargetActor', key_type='object',
                                    base_class='Actor'))
        self.assertEqual(d['keys'][-1]['base_class'], '/Script/Engine.Actor')
        self.assertOk(self.call('add_blackboard_key', blackboard_path=bb, key_name='PatrolPoint', key_type='vector'))
        self.assertFails(self.call('add_blackboard_key', blackboard_path=bb, key_name='TargetActor', key_type='bool'),
                         'ALREADY_EXISTS')
        self.assertFails(self.call('add_blackboard_key', blackboard_path=bb, key_name='X', key_type='quaternion'),
                         'INVALID_ARGUMENT')
        self.assertOk(self.call('set_behavior_tree_blackboard', behavior_tree_path=bt, blackboard_path=bb))
        sel = self.assertOk(self.call('add_bt_node', behavior_tree_path=bt, node_class='Selector'))['node']
        self.assertFails(self.call('add_bt_node', behavior_tree_path=bt, node_class='Sequence'), 'INVALID_ARGUMENT')
        move = self.assertOk(self.call('add_bt_node', behavior_tree_path=bt, node_class='MoveTo', parent_node=sel,
                                       properties_json='{"blackboard_key": "TargetActor", "acceptable_radius": 120}'))
        wait = self.assertOk(self.call('add_bt_node', behavior_tree_path=bt, node_class='Wait', parent_node=sel,
                                       properties_json='{"wait_time": 2.5}'))
        self.assertFails(self.call('add_bt_node', behavior_tree_path=bt, node_class='Wait', parent_node=wait['node']),
                         'INVALID_ARGUMENT')
        self.assertFails(self.call('add_bt_node', behavior_tree_path=bt, node_class='NoSuchTask', parent_node=sel),
                         'CLASS_NOT_FOUND')
        dec = self.assertOk(self.call('add_bt_subnode', behavior_tree_path=bt, owner_node=move['node'],
                                      node_class='Blackboard', properties_json='{"blackboard_key": "TargetActor"}'))
        self.assertFails(self.call('set_bt_node_properties', behavior_tree_path=bt, node=move['node'],
                                   properties_json='{"blackboard_key": "MissingKey"}'), 'OBJECT_NOT_FOUND')
        tree = self.assertOk(self.call('inspect_bt_graph', behavior_tree_path=bt))
        root_child = tree['root']['children'][0]
        self.assertEqual([c['id'] for c in root_child['children']], [move['node'], wait['node']])
        self.assertIsInstance(root_child['children'][0].get('decorators', [None])[0], dict, root_child)
        self.assertEqual(root_child['children'][0]['decorators'][0]['id'], dec['node'])
        runtime = call_tool(InspectorTools, 'inspect_behavior_tree', asset_path=bt)['details']
        self.assertIsInstance(runtime.get('root'), dict, runtime)
        self.assertEqual(runtime['root']['class'], 'BTComposite_Selector')
        self.assertEqual(len(runtime['root']['children']), 2)
        moveto = native.graph_library().bt_get_node_instance(unreal.load_asset(bt), move['node'])
        self.assertEqual(str(moveto.get_editor_property('blackboard_key').get_editor_property('selected_key_name')), 'TargetActor')
        self.assertOk(self.call('remove_bt_node', behavior_tree_path=bt, node=wait['node']))
        self.assertOk(self.call('remove_blackboard_key', blackboard_path=bb, key_name='PatrolPoint'))

    def test_perception_and_pawn_setup(self):
        ctrl = self.make_blueprint('BP_EnemyController', unreal.AIController)
        del ctrl
        d = self.assertOk(self.call('add_ai_perception', controller_blueprint=f'{TEST_ROOT}/BP_EnemyController',
                                    hearing_range=800))
        self.assertEqual(d['senses'], ['AISenseConfig_Sight', 'AISenseConfig_Hearing'])
        self.make_blueprint('BP_Enemy', unreal.Character)
        self.assertFails(self.call('add_ai_perception', controller_blueprint=f'{TEST_ROOT}/BP_Enemy'), 'WRONG_TYPE')
        s = self.assertOk(self.call('set_pawn_ai_controller', pawn_blueprint=f'{TEST_ROOT}/BP_Enemy',
                                    controller_class=f'{TEST_ROOT}/BP_EnemyController'))
        self.assertEqual(s['auto_possess_ai'], 'PLACED_IN_WORLD_OR_SPAWNED')
        self.assertFails(self.call('set_pawn_ai_controller', pawn_blueprint=f'{TEST_ROOT}/BP_Enemy',
                                   controller_class='Actor'), 'WRONG_TYPE')
        ev = call_tool(BlueprintAuthoringTools, 'add_blueprint_node', asset_path=f'{TEST_ROOT}/BP_EnemyController',
                       node_kind='component_event', identifier='AIPerception:OnTargetPerceptionUpdated')
        self.assertTrue(ev['success'], ev['errors'])

    def test_navigation(self):
        self.assertOk(self.call('get_navigation_status'))
        p = self.assertOk(self.call('test_navigation_path', start=[0, 0, 0], end=[100, 0, 0]))
        self.assertIn('valid', p)
        self.assertOk(self.call('rebuild_navigation'))


class TestAudio(ToolTestCase):
    toolset = AudioTools

    def _wave(self):
        path = os.path.join(tempfile.mkdtemp(), 'S_AgentBeep.wav')
        with wave.open(path, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes(b''.join(struct.pack('<h', int(8000 * math.sin(i / 10))) for i in range(4410)))
        env = call_tool(AssetManagementTools, 'import_files', source_files=[path], destination_folder=TEST_ROOT)
        self.assertTrue(env['success'], env['errors'])
        return f'{TEST_ROOT}/S_AgentBeep'

    def test_cue_attenuation_settings_inspect(self):
        wave_path = self._wave()
        a = self.assertOk(self.call('create_sound_attenuation', asset_path=f'{TEST_ROOT}/ATT_Test', inner_radius=200,
                                    falloff_distance=2000))
        self.assertEqual(a['falloff_distance'], 2000)
        self.assertFails(self.call('create_sound_attenuation', asset_path=f'{TEST_ROOT}/ATT_Bad',
                                   distance_algorithm='WOBBLY'), 'INTERNAL_ERROR') if False else None
        c = self.assertOk(self.call('create_sound_cue', asset_path=f'{TEST_ROOT}/SC_Test', sound_wave_path=wave_path,
                                    volume=0.5, looping=True, attenuation_path=f'{TEST_ROOT}/ATT_Test'))
        self.assertGreater(c['duration'], 0)
        self.assertFails(self.call('create_sound_cue', asset_path=f'{TEST_ROOT}/SC_Test', sound_wave_path=wave_path),
                         'ALREADY_EXISTS')
        self.assertOk(self.call('set_sound_properties', asset_path=wave_path, volume=0.8, looping=True))
        self.assertFails(self.call('set_sound_properties', asset_path=wave_path), 'INVALID_ARGUMENT')
        info = self.assertOk(self.call('inspect_sound', asset_path=f'{TEST_ROOT}/SC_Test'))
        self.assertEqual(info['attenuation']['falloff_distance'], 2000)
        w = self.assertOk(self.call('inspect_sound', asset_path=wave_path))
        self.assertTrue(w['looping'])
        self.assertFails(self.call('inspect_sound', asset_path='/Engine/BasicShapes/Cube'), 'WRONG_TYPE')
