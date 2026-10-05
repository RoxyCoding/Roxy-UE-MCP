import unreal

from agent_toolkit.core import native
from agent_toolkit.tests.base import TEST_ROOT, ToolTestCase, call_tool
from agent_toolkit.toolsets.blueprint_authoring import BlueprintAuthoringTools
from agent_toolkit.toolsets.networking import NetworkingTools
from agent_toolkit.toolsets.performance import PerformanceTools


class TestNetworking(ToolTestCase):
    toolset = NetworkingTools

    def test_replication_roundtrip(self):
        self.make_blueprint('BP_Net', unreal.Actor)
        path = f'{TEST_ROOT}/BP_Net'
        env = call_tool(BlueprintAuthoringTools, 'add_blueprint_variable', asset_path=path, variable_name='Health',
                        variable_type='float', replication='replicated')
        self.assertTrue(env['success'], env['errors'])
        d = self.assertOk(self.call('inspect_replication', asset_path=path))
        self.assertEqual(d['replicated_variables'][0]['name'], 'Health')
        self.assertTrue(any(w['code'] == 'REPLICATION_DISABLED' for w in self.call('inspect_replication',
                                                                                     asset_path=path)['warnings']))
        s = self.assertOk(self.call('set_actor_replication', asset_path=path, replicates=True, replicate_movement=True,
                                    net_dormancy='DORM_AWAKE', net_cull_distance=10000))
        self.assertTrue(s['applied']['replicates'])
        d = self.assertOk(self.call('inspect_replication', asset_path=path))
        self.assertTrue(d['actor_settings']['replicates'])
        self.assertFails(self.call('set_actor_replication', asset_path=path), 'INVALID_ARGUMENT')
        if native.library():
            call_tool(BlueprintAuthoringTools, 'add_custom_event', asset_path=path, event_name='ServerDoThing')
            r = self.assertOk(self.call('set_custom_event_replication', asset_path=path, event_name='ServerDoThing',
                                        replication='server', reliable=True))
            self.assertEqual(r['rpc'], 'Server,Reliable')
            self.assertFails(self.call('set_custom_event_replication', asset_path=path, event_name='Nope',
                                       replication='server'), 'OBJECT_NOT_FOUND')
        self.assertFails(self.call('inspect_replication', asset_path='/Engine/BasicShapes/Cube'), 'WRONG_TYPE')

    def test_pie_mode(self):
        d = self.assertOk(self.call('set_pie_network_mode', net_mode='listen_server', number_of_players=2))
        self.assertIn('2', str(d))
        self.assertFails(self.call('set_pie_network_mode', net_mode='p2p'), 'INVALID_ARGUMENT')
        self.assertOk(self.call('set_pie_network_mode', net_mode='standalone', number_of_players=1))


class TestPerformance(ToolTestCase):
    toolset = PerformanceTools

    def test_level_stats_and_scans(self):
        eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        a = eas.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(0, 0, 0))
        a.static_mesh_component.set_editor_property('static_mesh', unreal.load_asset('/Engine/BasicShapes/Sphere'))
        try:
            d = self.assertOk(self.call('get_level_performance_stats'))
            self.assertGreater(d['triangles_lod0']['non_nanite'] + d['triangles_lod0']['nanite'], 0)
            self.assertTrue(d['heaviest_meshes'])
        finally:
            eas.destroy_actor(a)
        bp = self.make_blueprint('BP_Ticker')
        unreal.BlueprintEditorLibrary.add_event_override(bp, 'ReceiveTick', unreal.IntPoint(0, 0))
        t = self.assertOk(self.call('find_blueprint_tick_usage', path=TEST_ROOT))
        self.assertTrue(any(x['blueprint'].endswith('BP_Ticker') for x in t['blueprints']))
        h = self.assertOk(self.call('find_heavy_assets', path='/Engine/BasicShapes', triangle_threshold=1))
        self.assertTrue(h['meshes'])
