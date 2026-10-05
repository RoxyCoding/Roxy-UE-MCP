import unreal

from agent_toolkit.tests.base import TEST_ROOT, ToolTestCase, call_tool
from agent_toolkit.toolsets.build_debug import BuildDebugTools
from agent_toolkit.toolsets.blueprint_graph import BlueprintGraphTools


class TestBlueprintGraph(ToolTestCase):
    toolset = BlueprintGraphTools

    def setUp(self):
        self.make_blueprint('BP_Graph', unreal.Actor)
        self.path = f'{TEST_ROOT}/BP_Graph'

    def build(self, **kw):
        return self.call('build_blueprint_graph', asset_path=self.path, **kw)

    def test_build_graph_and_queries(self):
        spec = ('{"nodes": ['
                '{"id": "begin", "kind": "event", "identifier": "ReceiveBeginPlay"},'
                '{"id": "print", "kind": "function", "identifier": "KismetSystemLibrary:PrintString",'
                ' "defaults": {"InString": "Hello"}}],'
                '"connections": ["begin.then->print.execute"]}')
        d = self.assertOk(self.build(graph_json=spec))
        self.assertEqual(set(d['nodes']), {'begin', 'print'})
        self.assertEqual(d['defaults']['print.InString'], 'Hello')
        # idempotent for events: BeginPlay is reused, not duplicated
        again = self.assertOk(self.build(graph_json='{"nodes": [{"id": "b", "kind": "event", '
                                                      '"identifier": "ReceiveBeginPlay"}]}'))
        self.assertEqual(again['reused_existing'], ['b'])
        self.assertEqual(again['nodes']['b'], d['nodes']['begin'])
        sub = self.assertOk(self.call('get_connected_subgraph', asset_path=self.path, start_node=d['nodes']['begin'],
                                      exec_only=True))
        self.assertIn(d['nodes']['print'], [n['id'] for n in sub['nodes']])
        found = self.assertOk(self.call('find_blueprint_nodes', asset_path=self.path, class_name='CallFunction'))
        self.assertEqual(found['total'], 1)
        self.assertOk(self.call('arrange_blueprint_nodes', asset_path=self.path))
        c = call_tool(BuildDebugTools, 'compile_blueprint', asset_path=self.path)
        self.assertTrue(c['success'], c['errors'])

    def test_build_rolls_back(self):
        before = self.assertOk(self.call('find_blueprint_nodes', asset_path=self.path))['total']
        bad = ('{"nodes": [{"id": "p", "kind": "function", "identifier": "KismetSystemLibrary:PrintString"}],'
               '"connections": ["p.nope->p.execute"]}')
        self.assertFails(self.build(graph_json=bad), 'OBJECT_NOT_FOUND')
        after = self.assertOk(self.call('find_blueprint_nodes', asset_path=self.path))['total']
        self.assertEqual(before, after)
        self.assertFails(self.build(graph_json='{"nodes": [{"id": "x", "kind": "bogus", "identifier": "y"}]}'),
                         'INVALID_ARGUMENT')

    def test_node_type_discovery(self):
        pins = self.assertOk(self.call('get_blueprint_node_type_pins', asset_path=self.path, node_kind='function',
                                       identifier='KismetSystemLibrary:PrintString'))
        self.assertIn('InString', [p['name'] for p in pins['pins']])
        self.assertEqual(self.assertOk(self.call('find_blueprint_nodes', asset_path=self.path,
                                                 class_name='CallFunction'))['total'], 0)
        types = self.assertOk(self.call('find_blueprint_node_types', asset_path=self.path, query='Print String'))
        self.assertIn('KismetSystemLibrary:PrintString', [f['id'] for f in types['functions']])
        own = self.assertOk(self.call('find_blueprint_node_types', asset_path=self.path, query='set actor location'))
        self.assertIn('Actor:K2_SetActorLocation', [f['id'] for f in own['functions']])
        self.assertOk(self.call('find_blueprint_node_categories', asset_path=self.path))

    def test_variable_function_parent(self):
        call_tool(__import__('agent_toolkit.toolsets.blueprint_authoring', fromlist=['x']).BlueprintAuthoringTools,
                  'add_blueprint_variable', asset_path=self.path, variable_name='Temp', variable_type='int')
        self.assertOk(self.call('remove_blueprint_variable', asset_path=self.path, variable_name='Temp'))
        self.assertFails(self.call('remove_blueprint_variable', asset_path=self.path, variable_name='Temp'),
                         'OBJECT_NOT_FOUND')
        parent = self.assertOk(self.call('get_blueprint_parent', asset_path=self.path))
        self.assertIn('Actor', parent['parent_class'])
        pawn = self.assertOk(self.call('set_blueprint_parent', asset_path=self.path, parent_class='Pawn'))
        self.assertTrue(pawn['changed'])
        self.assertTrue(self.assertOk(self.call('get_blueprint_parent', asset_path=self.path))['parent_class'].endswith('.Pawn'))

    def test_remove_function_and_component_events(self):
        authoring = __import__('agent_toolkit.toolsets.blueprint_authoring', fromlist=['x']).BlueprintAuthoringTools
        self.assertTrue(call_tool(authoring, 'create_blueprint_function', asset_path=self.path,
                                  function_name='Temp')['success'])
        self.assertOk(self.call('remove_blueprint_function', asset_path=self.path, function_name='Temp'))
        self.assertFails(self.call('remove_blueprint_function', asset_path=self.path, function_name='Temp'),
                         'OBJECT_NOT_FOUND')
        self.assertFails(self.call('remove_blueprint_function', asset_path=self.path, function_name='EventGraph'),
                         'OBJECT_NOT_FOUND')
        self.assertTrue(call_tool(authoring, 'add_blueprint_component', asset_path=self.path, component_class='BoxComponent',
                                  component_name='Trigger')['success'])
        events = self.assertOk(self.call('list_component_events', asset_path=self.path, component_name='Trigger'))
        self.assertIn('OnComponentBeginOverlap', events['events'])
        spec = ('{"nodes": [{"id": "ov", "kind": "component_event", "identifier": "Trigger:OnComponentBeginOverlap"}, '
                '{"id": "p", "kind": "function", "identifier": "KismetSystemLibrary:PrintString"}], '
                '"connections": ["ov.then->p.execute"]}')
        self.assertOk(self.build(graph_json=spec))
        c = call_tool(BuildDebugTools, 'compile_blueprint', asset_path=self.path)
        self.assertTrue(c['success'], c['errors'])
