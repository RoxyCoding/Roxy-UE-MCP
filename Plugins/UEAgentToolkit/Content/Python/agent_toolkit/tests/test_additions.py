import json
import math
import os
import struct
import tempfile
import wave

import unreal

from agent_toolkit.core import native
from agent_toolkit.tests.base import TEST_ROOT, ToolTestCase, call_tool
from agent_toolkit.tests.test_phase3 import _engine_anim
from agent_toolkit.toolsets.ai import AITools
from agent_toolkit.toolsets.animation import AnimationTools
from agent_toolkit.toolsets.assets import AssetManagementTools
from agent_toolkit.toolsets.metasound import MetaSoundTools
from agent_toolkit.toolsets.packaging import PackagingTools
from agent_toolkit.toolsets.performance import PerformanceTools
from agent_toolkit.toolsets.umg import UMGTools
from agent_toolkit.toolsets.world import WorldTools


def _epic(toolset: str, tool: str, **kwargs):
    r = unreal.ToolsetRegistry.execute_tool(toolset, tool, json.dumps(kwargs))
    if r.error:
        raise AssertionError(f'{toolset}.{tool}: {r.error}')
    return json.loads(r.value)['returnValue']


class _NativeCase(ToolTestCase):
    def setUp(self):
        if not native.graph_library():
            self.skipTest('UEAgentToolkitNative not loaded')


class TestSimpleParallelAndEQS(_NativeCase):
    toolset = AITools

    def test_simple_parallel(self):
        unreal.AssetToolsHelpers.get_asset_tools().create_asset('BT_SP', TEST_ROOT, unreal.BehaviorTree,
                                                                unreal.BehaviorTreeFactory())
        bt = f'{TEST_ROOT}/BT_SP'
        seq = self.assertOk(self.call('add_bt_node', behavior_tree_path=bt, node_class='Sequence'))['node']
        sp = self.assertOk(self.call('add_bt_node', behavior_tree_path=bt, node_class='SimpleParallel', parent_node=seq))['node']
        self.assertFails(self.call('add_bt_node', behavior_tree_path=bt, node_class='Sequence', parent_node=sp),
                         'INVALID_ARGUMENT')
        self.assertOk(self.call('add_bt_node', behavior_tree_path=bt, node_class='Wait', parent_node=sp, parent_output=0))
        self.assertOk(self.call('add_bt_node', behavior_tree_path=bt, node_class='Wait', parent_node=sp, parent_output=1))
        self.assertFails(self.call('add_bt_node', behavior_tree_path=bt, node_class='Wait', parent_node=sp, parent_output=0),
                         'INVALID_ARGUMENT')
        tree = self.assertOk(self.call('inspect_bt_graph', behavior_tree_path=bt))
        par = tree['root']['children'][0]['children'][0]
        self.assertEqual(sorted(c['branch'] for c in par['children']), ['background', 'main'])

    def test_eqs(self):
        env = call_tool(AssetManagementTools, 'create_asset', asset_path=f'{TEST_ROOT}/EQS_Find', asset_class='EnvQuery')
        self.assertTrue(env['success'], env['errors'])
        q = f'{TEST_ROOT}/EQS_Find'
        g = self.assertOk(self.call('add_eqs_generator', query_path=q, generator_class='SimpleGrid',
                                    properties_json='{"GridSize": {"DefaultValue": 1500}}'))
        self.assertEqual(g['option'], 0)
        t = self.assertOk(self.call('add_eqs_test', query_path=q, option_index=0, test_class='Distance', purpose='score'))
        self.assertEqual(t['test'], 0)
        d = self.assertOk(self.call('inspect_eqs_query', query_path=q))
        self.assertEqual(d['options'][0]['generator'], 'EnvQueryGenerator_SimpleGrid')
        self.assertIn('Score', d['options'][0]['tests'][0]['purpose'])
        self.assertFails(self.call('add_eqs_test', query_path=q, option_index=5, test_class='Distance'), 'OBJECT_NOT_FOUND')
        self.assertFails(self.call('add_eqs_generator', query_path=q, generator_class='NoSuchGen'), 'CLASS_NOT_FOUND')
        self.assertFails(self.call('set_eqs_properties', query_path=q, option_index=0, properties_json='{"Nope": 1}'),
                         'OBJECT_NOT_FOUND')
        self.assertOk(self.call('remove_eqs_item', query_path=q, option_index=0, test_index=0))
        self.assertOk(self.call('remove_eqs_item', query_path=q, option_index=0))
        self.assertEqual(self.assertOk(self.call('inspect_eqs_query', query_path=q))['options'], [])


class TestUMG(_NativeCase):
    toolset = UMGTools

    def test_layout_properties_animation(self):
        _epic('UMGToolSet.UMGToolSet', 'CreateWidgetBlueprint', FolderPath=TEST_ROOT, AssetName='WBP_Pause',
              ParentClass='/Script/UMG.UserWidget')
        wbp = f'{TEST_ROOT}/WBP_Pause'
        wbp_obj = f'{wbp}.WBP_Pause'
        root = _epic('UMGToolSet.UMGToolSet', 'AddWidget', WidgetBlueprint=wbp_obj, WidgetClass='/Script/UMG.CanvasPanel',
                     WidgetDisplayName='Root')
        root_widget = next((v for k, v in root.items() if k.lower() == 'widget'), None)
        self.assertTrue(root_widget, root)
        _epic('UMGToolSet.UMGToolSet', 'AddWidget', WidgetBlueprint=wbp_obj, WidgetClass='/Script/UMG.TextBlock',
              WidgetDisplayName='Title', ParentWidget=root_widget)
        lay = self.assertOk(self.call('set_widget_layout', widget_blueprint=wbp, widget_name='Title', anchors='top_center',
                                      position='0,80', size='400,60', alignment='0.5,0'))
        self.assertEqual(lay['changed']['anchors'], [0.5, 0, 0.5, 0])
        self.assertFails(self.call('set_widget_layout', widget_blueprint=wbp, widget_name='Title', anchors='1,2'),
                         'INVALID_ARGUMENT')
        self.assertFails(self.call('set_widget_layout', widget_blueprint=wbp, widget_name='Nope', z_order=1),
                         'OBJECT_NOT_FOUND')
        self.assertOk(self.call('set_widget_properties', widget_blueprint=wbp, widget_name='Title',
                                properties_json='{"text": "PAUSED", "render_opacity": 0.9}'))
        info = self.assertOk(self.call('inspect_widget', widget_blueprint=wbp, widget_name='Title'))
        self.assertEqual(info['slot'], 'CanvasPanelSlot')
        self.assertOk(self.call('create_widget_animation', widget_blueprint=wbp, animation_name='FadeIn', length_seconds=0.5))
        self.assertFails(self.call('create_widget_animation', widget_blueprint=wbp, animation_name='FadeIn'), 'ALREADY_EXISTS')
        self.assertOk(self.call('add_widget_animation_keys', widget_blueprint=wbp, animation_name='FadeIn',
                                widget_name='Title', property_name='RenderOpacity', keys_json='[[0,0],[0.5,1]]'))
        self.assertOk(self.call('add_widget_animation_keys', widget_blueprint=wbp, animation_name='FadeIn',
                                widget_name='Title', property_name='RenderTransform', channel='translation_y',
                                keys_json='[[0,-40],[0.5,0]]'))
        self.assertFails(self.call('add_widget_animation_keys', widget_blueprint=wbp, animation_name='FadeIn',
                                   widget_name='Title', property_name='Text', keys_json='[[0,0]]'), 'INVALID_ARGUMENT')
        a = self.assertOk(self.call('inspect_widget_animations', widget_blueprint=wbp))
        tracks = a['animations'][0]['bindings'][0]['tracks']
        self.assertEqual(len(tracks), 2, tracks)
        c = _epic('UMGToolSet.UMGToolSet', 'CompileWidgetBlueprint', WidgetBlueprint=wbp_obj)
        self.assertTrue(c, 'widget blueprint failed to compile')


class TestMetaSound(ToolTestCase):
    toolset = MetaSoundTools

    def test_build_and_inspect(self):
        path = os.path.join(tempfile.mkdtemp(), 'S_MsWave.wav')
        with wave.open(path, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes(b''.join(struct.pack('<h', int(6000 * math.sin(i / 7))) for i in range(2205)))
        self.assertTrue(call_tool(AssetManagementTools, 'import_files', source_files=[path],
                                  destination_folder=TEST_ROOT)['success'])
        graph = {'nodes': [{'id': 'wave', 'class': 'UE.Wave Player.Mono',
                            'inputs': {'Wave Asset': f'{TEST_ROOT}/S_MsWave', 'Loop': False}},
                           {'id': 'gain', 'class': 'UE.Multiply.Audio by Float'}],
                 'graph_inputs': [{'name': 'Volume', 'type': 'Float', 'default': 0.5}],
                 'connections': ['graph.On Play->wave.Play', 'wave.Out Mono->gain.PrimaryOperand',
                                 'graph.Volume->gain.AdditionalOperands', 'gain.Out->graph.Out Mono',
                                 'wave.On Finished->graph.On Finished']}
        d = self.assertOk(self.call('build_metasound_source', asset_path=f'{TEST_ROOT}/MS_Test', graph_json=json.dumps(graph)))
        self.assertIn('Play', d['nodes']['wave']['inputs'])
        i = self.assertOk(self.call('inspect_metasound', asset_path=f'{TEST_ROOT}/MS_Test'))
        self.assertIn('Volume', i['graph_inputs'])
        bad = dict(graph, connections=['wave.NoPin->graph.Out Mono'])
        self.assertFails(self.call('build_metasound_source', asset_path=f'{TEST_ROOT}/MS_Bad', graph_json=json.dumps(bad)),
                         'OBJECT_NOT_FOUND')
        bad2 = dict(graph, nodes=[{'id': 'x', 'class': 'UE.DoesNotExist'}], connections=[])
        self.assertFails(self.call('build_metasound_source', asset_path=f'{TEST_ROOT}/MS_Bad2', graph_json=json.dumps(bad2)),
                         'UE_OPERATION_FAILED')


class TestRetarget(ToolTestCase):
    toolset = AnimationTools

    def test_ik_rig_and_retargeter(self):
        anim, _ = _engine_anim()
        reg = unreal.AssetRegistryHelpers.get_asset_registry()
        mesh = next((str(d.package_name) for d in reg.get_assets_by_path('/Engine', recursive=True) or []
                     if str(d.asset_class_path.asset_name) == 'SkeletalMesh'), None)
        if not mesh:
            self.skipTest('no SkeletalMesh in engine content')
        r = self.assertOk(self.call('create_ik_rig', asset_path=f'{TEST_ROOT}/IK_Test', skeletal_mesh_path=mesh))
        self.assertIn('template_matched', r)
        t = self.assertOk(self.call('create_ik_retargeter', asset_path=f'{TEST_ROOT}/RTG_Test',
                                    source_ik_rig=f'{TEST_ROOT}/IK_Test', target_ik_rig=f'{TEST_ROOT}/IK_Test'))
        if r['retargetable']:
            self.assertGreater(t['ops'], 0)
        else:
            self.assertFalse(t['configured'])
            self.assertTrue(any(w['code'] == 'RIG_NOT_RETARGETABLE' for w in
                                self.call('create_ik_retargeter', asset_path=f'{TEST_ROOT}/RTG_Test2',
                                          source_ik_rig=f'{TEST_ROOT}/IK_Test', target_ik_rig=f'{TEST_ROOT}/IK_Test')['warnings']))
        self.assertFails(self.call('create_ik_rig', asset_path=f'{TEST_ROOT}/IK_Test', skeletal_mesh_path=mesh),
                         'ALREADY_EXISTS')
        if anim:
            env = self.call('retarget_animations', retargeter_path=f'{TEST_ROOT}/RTG_Test', animation_paths=[anim],
                            source_mesh=mesh, target_mesh=mesh, target_folder=TEST_ROOT)
            if not env['success']:
                self.assertIn(env['errors'][0]['code'], ('UE_OPERATION_FAILED', 'INVALID_ARGUMENT'))


class TestWorld(_NativeCase):
    toolset = WorldTools

    def tearDown(self):
        eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        for a in eas.get_all_level_actors():
            if isinstance(a, (unreal.LandscapeProxy, unreal.InstancedFoliageActor)):
                eas.destroy_actor(a)

    def test_landscape_and_foliage(self):
        self.assertFails(self.call('create_landscape', location=[0, 0, 0], quads_per_section=10), 'INVALID_ARGUMENT')
        hm = os.path.join(tempfile.mkdtemp(), 'hm.r16')
        size = 2 * 7 + 1
        with open(hm, 'wb') as f:
            f.write(b''.join(struct.pack('<H', 32768 + (x * 200)) for _ in range(size) for x in range(size)))
        d = self.assertOk(self.call('create_landscape', location=[0, 0, 0], components_x=2, components_y=2,
                                    quads_per_section=7, heightmap_file=hm))
        self.assertEqual(d['vertices'], [15, 15])
        info = self.assertOk(self.call('inspect_landscapes'))
        mine = next(l for l in info['landscapes'] if l['label'] == d['landscape'])
        self.assertEqual(mine['components'], 4)
        self.assertOk(self.call('set_landscape_material', material_path='/Engine/BasicShapes/BasicShapeMaterial',
                                landscape=d['landscape']))
        self.assertOk(self.call('create_foliage_type', asset_path=f'{TEST_ROOT}/FT_Cube', mesh_path='/Engine/BasicShapes/Cube'))
        self.assertOk(self.call('add_foliage_instances', foliage_type_path=f'{TEST_ROOT}/FT_Cube',
                                transforms_json='[{"location": [0, 0, 100]}, {"location": [200, 0, 100], "scale": [2,2,2]}]'))
        s = self.call('scatter_foliage', foliage_type_path=f'{TEST_ROOT}/FT_Cube', center=[0, 0, 0], radius=500, count=10,
                      seed=7)
        if not s['success']:
            self.assertEqual(s['errors'][0]['code'], 'UE_OPERATION_FAILED')  # no collision in headless sessions
        f = self.assertOk(self.call('inspect_foliage'))
        self.assertGreaterEqual(f['total'], 2)
        self.assertOk(self.call('remove_all_foliage_instances', foliage_type_path=f'{TEST_ROOT}/FT_Cube'))
        self.assertEqual(self.assertOk(self.call('inspect_foliage'))['total'], 0)


class TestPackagingAndStats(ToolTestCase):
    toolset = PackagingTools

    def test_validation(self):
        self.assertFails(self.call('start_packaging', platform='Dreamcast'), 'INVALID_ARGUMENT')
        self.assertFails(self.call('get_packaging_status', job_id='Packages-nope'), 'OBJECT_NOT_FOUND')
        self.assertFails(self.call('cancel_packaging', job_id='Packages-nope'), 'OBJECT_NOT_FOUND')

    def test_frame_stats(self):
        env = call_tool(PerformanceTools, 'get_frame_stats')
        if native.graph_library():
            self.assertTrue(env['success'], env['errors'])
            self.assertIn('draw_calls', env['details'])
        else:
            self.assertEqual(env['errors'][0]['code'], 'NOT_SUPPORTED')


class TestMetaSoundEditing(ToolTestCase):
    toolset = MetaSoundTools

    def setUp(self):
        if not getattr(unreal, 'AgentToolkitMetaSoundLibrary', None):
            self.skipTest('UEAgentToolkitNative not loaded')

    def test_partial_editing(self):
        path = os.path.join(tempfile.mkdtemp(), 'S_EditWave.wav')
        with wave.open(path, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes(b''.join(struct.pack('<h', int(5000 * math.sin(i / 5))) for i in range(2205)))
        self.assertTrue(call_tool(AssetManagementTools, 'import_files', source_files=[path],
                                  destination_folder=TEST_ROOT)['success'])
        ms = f'{TEST_ROOT}/MS_Edit'
        graph = {'nodes': [{'id': 'wave', 'class': 'UE.Wave Player.Mono', 'inputs': {'Wave Asset': f'{TEST_ROOT}/S_EditWave'}},
                           {'id': 'gain', 'class': 'UE.Multiply.Audio by Float'}],
                 'connections': ['graph.On Play->wave.Play', 'wave.Out Mono->gain.PrimaryOperand',
                                 'gain.Out->graph.Out Mono', 'wave.On Finished->graph.On Finished']}
        self.assertOk(self.call('build_metasound_source', asset_path=ms, graph_json=json.dumps(graph)))
        g = self.assertOk(self.call('inspect_metasound', asset_path=ms))
        wave_id = next(n['id'] for n in g['nodes'] if n['class'].startswith('UE.Wave Player'))
        gain_id = next(n['id'] for n in g['nodes'] if n['class'].startswith('UE.Multiply'))
        self.assertTrue(any(e.endswith(f'{gain_id}.PrimaryOperand') for e in g['edges']), g['edges'])
        # Insert a second gain stage between gain and the output.
        g2 = self.assertOk(self.call('add_metasound_node', asset_path=ms, node_class='UE.Multiply.Audio by Float',
                                     x=600, input_defaults_json='{"AdditionalOperands": 0.5}'))
        self.assertOk(self.call('disconnect_metasound_pin', asset_path=ms, input_pin='@Out Mono'))
        self.assertOk(self.call('connect_metasound_pins', asset_path=ms, connections=[
            f'{gain_id}.Out->{g2["id"]}.PrimaryOperand', f'{g2["id"]}.Out->@Out Mono']))
        self.assertOk(self.call('add_metasound_graph_input', asset_path=ms, name='Pitch', data_type='Float', default_value='1.5'))
        self.assertOk(self.call('connect_metasound_pins', asset_path=ms, connections=[f'@Pitch->{wave_id}.Pitch Shift']))
        self.assertOk(self.call('set_metasound_input_defaults', asset_path=ms,
                                values_json=json.dumps({f'{wave_id}.Loop': True, f'{gain_id}.AdditionalOperands': 0.8})))
        g = self.assertOk(self.call('inspect_metasound', asset_path=ms))
        self.assertIn('Pitch', g['graph_inputs'])
        wave_node = next(n for n in g['nodes'] if n['id'] == wave_id)
        self.assertIn('link', next(p for p in wave_node['inputs'] if p['name'] == 'Pitch Shift'))
        self.assertIn('true', next(p for p in wave_node['inputs'] if p['name'] == 'Loop').get('default', '').lower())
        # Error paths
        self.assertFails(self.call('add_metasound_node', asset_path=ms, node_class='UE.NoSuchNode'), 'OBJECT_NOT_FOUND')
        self.assertFails(self.call('connect_metasound_pins', asset_path=ms, connections=[f'@Pitch->{gain_id}.PrimaryOperand']),
                         'INVALID_ARGUMENT')
        self.assertFails(self.call('connect_metasound_pins', asset_path=ms, connections=[f'{wave_id}.NoPin->@Out Mono']),
                         'OBJECT_NOT_FOUND')
        self.assertFails(self.call('remove_metasound_graph_input', asset_path=ms, name='Nope'), 'OBJECT_NOT_FOUND')
        self.assertFails(self.call('inspect_metasound', asset_path='/Engine/BasicShapes/Cube'), 'WRONG_TYPE')
        # Removal
        self.assertOk(self.call('remove_metasound_nodes', asset_path=ms, nodes=[g2['id']]))
        self.assertOk(self.call('remove_metasound_graph_input', asset_path=ms, name='Pitch'))
        g = self.assertOk(self.call('inspect_metasound', asset_path=ms))
        self.assertNotIn(g2['id'], [n['id'] for n in g['nodes']])
        self.assertNotIn('Pitch', g['graph_inputs'])
        # Edits persist through save + reload of the asset document.
        self.assertTrue(unreal.EditorAssetLibrary.save_asset(ms, False))
        self.assertIn(wave_id, [n['id'] for n in self.assertOk(self.call('inspect_metasound', asset_path=ms))['nodes']])
