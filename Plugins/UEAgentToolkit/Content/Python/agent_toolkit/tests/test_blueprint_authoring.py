import unreal

from agent_toolkit.core import native
from agent_toolkit.tests.base import TEST_ROOT, ToolTestCase, call_tool
from agent_toolkit.toolsets.blueprint_authoring import BlueprintAuthoringTools
from agent_toolkit.toolsets.build_debug import BuildDebugTools
from agent_toolkit.toolsets.inspector import InspectorTools


class TestBlueprintAuthoring(ToolTestCase):
    toolset = BlueprintAuthoringTools

    def setUp(self):
        self.bp = self.make_blueprint('BP_Auth', unreal.Character)
        self.path = f'{TEST_ROOT}/BP_Auth'

    def test_variables(self):
        d = self.assertOk(self.call('add_blueprint_variable', asset_path=self.path, variable_name='Health',
                                    variable_type='float', default_value_json='100', category='Stats',
                                    instance_editable=True))
        self.assertEqual(d['default'], 100.0)
        self.assertIn('double', d['type'])
        self.assertOk(self.call('add_blueprint_variable', asset_path=self.path, variable_name='Spawn',
                                variable_type='vector', default_value_json='[1,2,3]'))
        self.assertOk(self.call('add_blueprint_variable', asset_path=self.path, variable_name='Target',
                                variable_type='object:Actor'))
        self.assertOk(self.call('add_blueprint_variable', asset_path=self.path, variable_name='Points',
                                variable_type='array:int'))
        self.assertFails(self.call('add_blueprint_variable', asset_path=self.path, variable_name='Health',
                                   variable_type='float'), 'ALREADY_EXISTS')
        self.assertFails(self.call('add_blueprint_variable', asset_path=self.path, variable_name='Bad',
                                   variable_type='quaternionish'), 'INVALID_ARGUMENT')
        s = self.assertOk(self.call('set_blueprint_variable_default', asset_path=self.path, variable_name='Health',
                                    value_json='75.5'))
        self.assertEqual(s['default'], 75.5)
        inherited = self.assertOk(self.call('set_blueprint_variable_default', asset_path=self.path,
                                            variable_name='JumpMaxCount', value_json='2'))
        self.assertEqual(inherited['default'], 2)
        self.assertFails(self.call('set_blueprint_variable_default', asset_path=self.path, variable_name='Nope',
                                   value_json='1'), 'INVALID_ARGUMENT')
        t = self.assertOk(self.call('change_blueprint_variable_type', asset_path=self.path, variable_name='Health',
                                    new_type='int'))
        self.assertEqual(t['new_type'], 'int')
        f = self.assertOk(self.call('set_blueprint_variable_flags', asset_path=self.path, variable_name='Health',
                                    replication='replicated'))
        self.assertIn('replication', f['changed'])
        if native.library():
            self.assertOk(self.call('set_blueprint_variable_flags', asset_path=self.path, variable_name='Health',
                                    replication_condition='OWNER_ONLY'))
            self.assertEqual(native.library().get_variable_replication_condition(self.bp, 'Health'),
                             unreal.LifetimeCondition.COND_OWNER_ONLY.value)
        self.assertFails(self.call('set_blueprint_variable_flags', asset_path=self.path, variable_name='Health'),
                         'INVALID_ARGUMENT')

    def test_components(self):
        self.assertOk(self.call('add_blueprint_component', asset_path=self.path, component_class='SpringArmComponent',
                                component_name='CameraBoom', properties_json='{"target_arm_length": 350}'))
        self.assertOk(self.call('add_blueprint_component', asset_path=self.path, component_class='CameraComponent',
                                component_name='FollowCamera', parent_component='CameraBoom'))
        self.assertOk(self.call('add_blueprint_component', asset_path=self.path, component_class='StaticMeshComponent',
                                component_name='Hat', properties_json='{"static_mesh": "/Engine/BasicShapes/Cone", '
                                                                      '"relative_location": [0, 0, 90]}'))
        comps = {c['name']: c for c in call_tool(InspectorTools, 'inspect_blueprint', asset_path=self.path)['details']['components']}
        self.assertEqual(comps['FollowCamera']['parent'], 'CameraBoom')
        self.assertOk(self.call('set_blueprint_component_properties', asset_path=self.path,
                                component_name='CharMoveComp' if 'CharMoveComp' in comps else 'CharacterMovement',
                                properties_json='{"max_walk_speed": 900, "jump_z_velocity": 700}'))
        cdo = unreal.get_default_object(self.bp.generated_class())
        self.assertAlmostEqual(cdo.get_editor_property('character_movement').get_editor_property('max_walk_speed'), 900)
        self.assertOk(self.call('attach_blueprint_component', asset_path=self.path, component_name='Hat',
                                new_parent='CameraBoom'))
        self.assertFails(self.call('add_blueprint_component', asset_path=self.path, component_class='CameraComponent',
                                   component_name='FollowCamera'), 'ALREADY_EXISTS')
        self.assertFails(self.call('add_blueprint_component', asset_path=self.path, component_class='Actor',
                                   component_name='X'), 'WRONG_TYPE')
        self.assertFails(self.call('set_blueprint_component_properties', asset_path=self.path, component_name='Hat',
                                   properties_json='{"no_such": 1}'), 'INVALID_ARGUMENT')
        self.assertFails(self.call('remove_blueprint_component', asset_path=self.path, component_name='CharacterMovement'),
                         'NOT_SUPPORTED')
        self.assertOk(self.call('remove_blueprint_component', asset_path=self.path, component_name='Hat'))
        self.assertFails(self.call('remove_blueprint_component', asset_path=self.path, component_name='Hat'),
                         'OBJECT_NOT_FOUND')

    def test_functions_and_events(self):
        d = self.assertOk(self.call('create_blueprint_function', asset_path=self.path, function_name='ApplyDamage',
                                    inputs_json='[{"name": "Amount", "type": "float"}]',
                                    outputs_json='[{"name": "IsDead", "type": "bool"}]'))
        pins = [p['name'] for n in d['nodes'] for p in n['pins']]
        self.assertIn('Amount', pins)
        self.assertIn('IsDead', pins)
        self.assertOk(self.call('create_blueprint_function', asset_path=self.path, function_name='GetSpeed',
                                outputs_json='[{"name": "Speed", "type": "float"}]', pure=True))
        self.assertOk(self.call('set_blueprint_function_flags', asset_path=self.path, function_name='ApplyDamage',
                                access='protected', call_in_editor=False))
        self.assertFails(self.call('create_blueprint_function', asset_path=self.path, function_name='ApplyDamage'),
                         'ALREADY_EXISTS')
        self.assertFails(self.call('set_blueprint_function_flags', asset_path=self.path, function_name='Nope', pure=True),
                         'OBJECT_NOT_FOUND')
        env = self.call('add_custom_event', asset_path=self.path, event_name='ServerFire',
                        inputs_json='[{"name": "Power", "type": "float"}]', replication='server', reliable=True)
        if native.library():
            ev = self.assertOk(env)
            self.assertEqual(ev['replication'], 'Server,Reliable')
            self.assertIn('Power', [p['name'] for p in ev['pins']])
            self.assertOk(self.call('create_blueprint_macro', asset_path=self.path, macro_name='MyMacro'))
            self.assertFails(self.call('create_blueprint_macro', asset_path=self.path, macro_name='MyMacro'), 'ALREADY_EXISTS')
        else:
            self.assertFails(env, 'NOT_SUPPORTED')
        c = self.assertOk(self.call('compile_blueprint', asset_path=self.path)) if False else \
            call_tool(BuildDebugTools, 'compile_blueprint', asset_path=self.path)
        self.assertTrue(c['success'], c['errors'])

    def test_interfaces(self):
        if not native.library():
            self.assertFails(self.call('implement_blueprint_interface', asset_path=self.path,
                                       interface_class='/Game/X'), 'NOT_SUPPORTED')
            return
        iface = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            'BPI_Interact', TEST_ROOT, unreal.Blueprint, unreal.BlueprintInterfaceFactory())
        unreal.BlueprintEditorLibrary.add_function_graph(iface, 'Interact')
        unreal.BlueprintEditorLibrary.compile_blueprint(iface)
        d = self.assertOk(self.call('implement_blueprint_interface', asset_path=self.path,
                                    interface_class=f'{TEST_ROOT}/BPI_Interact'))
        self.assertTrue(any('BPI_Interact' in i for i in d['interfaces']))
        self.assertFails(self.call('implement_blueprint_interface', asset_path=self.path,
                                   interface_class=f'{TEST_ROOT}/BPI_Interact'), 'ALREADY_EXISTS')
        self.assertFails(self.call('implement_blueprint_interface', asset_path=self.path, interface_class='Actor'),
                         'WRONG_TYPE')
        self.assertOk(self.call('remove_blueprint_interface', asset_path=self.path,
                                interface_class=f'{TEST_ROOT}/BPI_Interact'))
        self.assertFails(self.call('remove_blueprint_interface', asset_path=self.path,
                                   interface_class=f'{TEST_ROOT}/BPI_Interact'), 'OBJECT_NOT_FOUND')

    def test_graph_editing_loop(self):
        begin = self.assertOk(self.call('add_blueprint_node', asset_path=self.path, node_kind='event',
                                        identifier='ReceiveBeginPlay'))
        printer = self.assertOk(self.call('add_blueprint_node', asset_path=self.path, node_kind='function',
                                          identifier='KismetSystemLibrary:PrintString', x=300))
        jump = self.assertOk(self.call('add_blueprint_node', asset_path=self.path, node_kind='function',
                                       identifier='Character:Jump', x=600))
        self.assertOk(self.call('add_blueprint_node', asset_path=self.path, node_kind='branch', x=900))
        self.assertOk(self.call('add_blueprint_node', asset_path=self.path, node_kind='macro', identifier='DoOnce', x=900, y=300))
        c = self.assertOk(self.call('connect_blueprint_pins', asset_path=self.path, connections=[
            f"{begin['id']}.then->{printer['id']}.execute", f"{printer['id']}.then->{jump['id']}.execute"]))
        self.assertEqual(len(c['connected']), 2)
        self.assertOk(self.call('set_blueprint_pin_defaults', asset_path=self.path,
                                values_json='{"%s.InString": "Hello Agent", "%s.Duration": 5}' % (printer['id'], printer['id'])))
        graph = call_tool(InspectorTools, 'inspect_blueprint_graph', asset_path=self.path)['details']
        node = next(n for n in graph['nodes'] if n['id'] == printer['id'])
        self.assertEqual(next(p for p in node['pins'] if p['name'] == 'InString')['value'], 'Hello Agent')
        self.assertTrue(call_tool(BuildDebugTools, 'compile_blueprint', asset_path=self.path)['success'])
        bad = self.call('connect_blueprint_pins', asset_path=self.path,
                        connections=[f"{printer['id']}.then->{begin['id']}.then"])
        self.assertFalse(bad['success'])
        self.assertFails(self.call('connect_blueprint_pins', asset_path=self.path,
                                   connections=[f"{printer['id']}.NoSuchPin->{jump['id']}.execute"]), 'OBJECT_NOT_FOUND')
        self.assertFails(self.call('connect_blueprint_pins', asset_path=self.path, connections=['nonsense']),
                         'INVALID_ARGUMENT')
        dis = self.assertOk(self.call('disconnect_blueprint_pins', asset_path=self.path, pin=f"{printer['id']}.then"))
        self.assertEqual(len(dis['removed_links']), 1)
        self.assertOk(self.call('move_blueprint_node', asset_path=self.path, node_id=jump['id'], x=50, y=50))
        r = self.assertOk(self.call('remove_blueprint_nodes', asset_path=self.path, node_ids=[jump['id']]))
        self.assertEqual(r['removed'], [jump['id']])
        self.assertFails(self.call('add_blueprint_node', asset_path=self.path, node_kind='function',
                                   identifier='KismetSystemLibrary:NoSuchFunction'), 'UE_OPERATION_FAILED')
        self.assertFails(self.call('add_blueprint_node', asset_path=self.path, node_kind='teleport', identifier='x'),
                         'INVALID_ARGUMENT')
        self.assertFails(self.call('add_blueprint_node', asset_path=self.path, node_kind='function'), 'INVALID_ARGUMENT')
        s = self.assertOk(self.call('search_blueprint_node_actions', asset_path=self.path, query='PrintString'))
        self.assertGreater(s['total'], 0)
        action = self.assertOk(self.call('add_blueprint_node', asset_path=self.path, node_kind='action',
                                         identifier='PrintString', y=600))
        self.assertTrue(action['id'])
