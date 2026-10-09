import unreal

from agent_toolkit.tests.base import TEST_ROOT, ToolTestCase, call_tool, requires_editor_ui
from agent_toolkit.toolsets.build_debug import BuildDebugTools
from agent_toolkit.toolsets.input import InputTools
from agent_toolkit.toolsets.inspector import InspectorTools
from agent_toolkit.toolsets.material_authoring import MaterialAuthoringTools


class TestMaterialAuthoring(ToolTestCase):
    toolset = MaterialAuthoringTools

    def setUp(self):
        eas = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
        if eas.does_asset_exist(f'{TEST_ROOT}/M_Test'):
            eas.delete_asset(f'{TEST_ROOT}/M_Test')
        unreal.AssetToolsHelpers.get_asset_tools().create_asset('M_Test', TEST_ROOT, unreal.Material,
                                                                unreal.MaterialFactoryNew())
        self.path = f'{TEST_ROOT}/M_Test'

    def test_parameters_textures_wiring_compile(self):
        v = self.assertOk(self.call('add_material_parameter', asset_path=self.path, parameter_type='vector',
                                    parameter_name='Tint', default_value_json='[1, 0.2, 0.2, 1]', group='Color',
                                    connect_to_output='base_color'))
        self.assertTrue(v['expression'])
        self.assertOk(self.call('add_material_parameter', asset_path=self.path, parameter_type='scalar',
                                parameter_name='Rough', default_value_json='0.7', connect_to_output='roughness'))
        self.assertFails(self.call('add_material_parameter', asset_path=self.path, parameter_type='scalar',
                                   parameter_name='Rough'), 'ALREADY_EXISTS')
        self.assertFails(self.call('add_material_parameter', asset_path=self.path, parameter_type='matrix',
                                   parameter_name='M'), 'INVALID_ARGUMENT')
        t = self.assertOk(self.call('add_texture_sample', asset_path=self.path,
                                    texture_path='/Engine/EngineResources/DefaultTexture', parameter_name='Albedo', y=300))
        self.assertIn('RGB', t['outputs'])
        self.assertOk(self.call('connect_material_expressions', asset_path=self.path,
                                connections=[f"{t['expression']}.RGB->@emissive_color"]))
        self.assertFails(self.call('connect_material_expressions', asset_path=self.path,
                                   connections=['NoExpr.RGB->@base_color']), 'OBJECT_NOT_FOUND')
        self.assertFails(self.call('connect_material_expressions', asset_path=self.path,
                                   connections=[f"{t['expression']}.RGB->@not_an_output"]), 'INVALID_ARGUMENT')
        s = self.assertOk(self.call('set_material_settings', asset_path=self.path, blend_mode='MASKED', two_sided=True))
        self.assertIn('blend_mode', s['changed'])
        self.assertFails(self.call('set_material_settings', asset_path=self.path), 'INVALID_ARGUMENT')
        c = self.assertOk(self.call('compile_material', asset_path=self.path))
        self.assertEqual(c['errors'], [])
        info = call_tool(InspectorTools, 'inspect_material', asset_path=self.path)['details']
        self.assertIn('Tint', info['parameters']['vector'])
        self.assertEqual(info['output_connections'].get('base_color'), v['expression'])

    def test_wrong_asset_type(self):
        self.assertFails(self.call('compile_material', asset_path='/Engine/BasicShapes/Cube'), 'WRONG_TYPE')


class TestInput(ToolTestCase):
    toolset = InputTools

    def test_actions_contexts_mappings(self):
        self.assertOk(self.call('create_input_action', asset_path=f'{TEST_ROOT}/IA_Move', value_type='axis2d'))
        self.assertOk(self.call('create_input_action', asset_path=f'{TEST_ROOT}/IA_Jump', value_type='bool',
                                triggers='pressed'))
        self.assertFails(self.call('create_input_action', asset_path=f'{TEST_ROOT}/IA_Jump'), 'ALREADY_EXISTS')
        self.assertFails(self.call('create_input_action', asset_path=f'{TEST_ROOT}/IA_X', value_type='quaternion'),
                         'INVALID_ARGUMENT')
        self.assertOk(self.call('create_input_mapping_context', asset_path=f'{TEST_ROOT}/IMC_Test'))
        imc, move, jump = f'{TEST_ROOT}/IMC_Test', f'{TEST_ROOT}/IA_Move', f'{TEST_ROOT}/IA_Jump'
        for key, mods in (('W', 'swizzle'), ('S', 'swizzle,negate'), ('A', 'negate'), ('D', None)):
            self.assertOk(self.call('add_key_mapping', context_path=imc, action_path=move, key=key, modifiers=mods))
        m = self.assertOk(self.call('add_key_mapping', context_path=imc, action_path=jump, key='SpaceBar',
                                    triggers='hold:0.3'))
        self.assertEqual(m['triggers'], ['Hold'])
        self.assertFails(self.call('add_key_mapping', context_path=imc, action_path=jump, key='SpaceBar'), 'ALREADY_EXISTS')
        self.assertFails(self.call('add_key_mapping', context_path=imc, action_path=jump, key='NotAKey123'),
                         'INVALID_ARGUMENT')
        self.assertFails(self.call('add_key_mapping', context_path=imc, action_path=jump, key='E', modifiers='wobble'),
                         'INVALID_ARGUMENT')
        u = self.assertOk(self.call('update_key_mapping', context_path=imc, action_path=jump, key='SpaceBar',
                                    triggers='none'))
        self.assertEqual(u['triggers'], [])
        listing = self.assertOk(self.call('list_key_mappings', context_path=imc))
        self.assertEqual(len(listing['mappings']), 5)
        s_map = next(x for x in listing['mappings'] if x['key'] == 'S')
        self.assertEqual(s_map['modifiers'], ['SwizzleAxis', 'Negate'])
        r = self.assertOk(self.call('remove_key_mapping', context_path=imc, action_path=move, key='D'))
        self.assertEqual(len(r['removed']), 1)
        self.assertFails(self.call('remove_key_mapping', context_path=imc, action_path=move, key='D'), 'OBJECT_NOT_FOUND')
        dry = self.assertOk(self.call('register_default_mapping_context', context_path=imc, priority=1, dry_run=True))
        self.assertTrue(any('DefaultMappingContexts' in l for l in dry['change']['added_lines']))

    def test_gamepad_support(self):
        imc, move, look, jump, fire = (f'{TEST_ROOT}/{n}' for n in ('IMC_Pad', 'IA_PadMove', 'IA_PadLook',
                                                                    'IA_PadJump', 'IA_PadFire'))
        for path, vt in ((move, 'axis2d'), (look, 'axis2d'), (jump, 'bool'), (fire, 'bool')):
            self.assertOk(self.call('create_input_action', asset_path=path, value_type=vt))
        self.assertOk(self.call('create_input_mapping_context', asset_path=imc))
        for key, mods in (('W', 'swizzle'), ('S', 'swizzle,negate'), ('A', 'negate'), ('D', None)):
            self.assertOk(self.call('add_key_mapping', context_path=imc, action_path=move, key=key, modifiers=mods))
        self.assertOk(self.call('add_key_mapping', context_path=imc, action_path=look, key='Mouse2D', modifiers='negate:y'))
        self.assertOk(self.call('add_key_mapping', context_path=imc, action_path=jump, key='SpaceBar', triggers='hold:0.3'))
        self.assertOk(self.call('add_key_mapping', context_path=imc, action_path=fire, key='LeftMouseButton'))
        self.assertOk(self.call('add_key_mapping', context_path=imc, action_path=fire, key='K'))
        # multi-value modifier args stay attached to their spec
        m = self.assertOk(self.call('update_key_mapping', context_path=imc, action_path=fire, key='K',
                                    modifiers='deadzone:0.1,0.9,radial,scalar:2,2,1,response_curve:2'))
        self.assertEqual(m['modifiers'], ['DeadZone', 'Scalar', 'ResponseCurveExponential'])
        dry = self.assertOk(self.call('add_gamepad_mappings', context_path=imc, dry_run=True))
        planned = {e['key']: e for e in dry['plan']}
        self.assertEqual(set(planned), {'Gamepad_Left2D', 'Gamepad_Right2D', 'Gamepad_FaceButton_Bottom',
                                        'Gamepad_RightTrigger'})
        self.assertIn('negate:y', planned['Gamepad_Right2D']['modifiers'])
        self.assertEqual(planned['Gamepad_FaceButton_Bottom']['triggers'], 'hold:0.3')
        self.assertEqual(dry['unmapped_keys'], [{'action': fire, 'key': 'K'}])
        self.assertEqual(len(self.assertOk(self.call('list_key_mappings', context_path=imc))['mappings']), 8)
        r = self.assertOk(self.call('add_gamepad_mappings', context_path=imc, key_map='K=Gamepad_RightShoulder'))
        self.assertEqual(len(r['added']), 5)
        left = next(a for a in r['added'] if a['key'] == 'Gamepad_Left2D')
        self.assertEqual(left['modifiers'], ['DeadZone'])
        again = self.assertOk(self.call('add_gamepad_mappings', context_path=imc))
        self.assertEqual(again['added'], [])
        self.assertEqual(len(again['skipped_actions']), 4)
        self.assertFails(self.call('add_gamepad_mappings', context_path=imc, key_map='K=NotAKey123'), 'INVALID_ARGUMENT')

    def test_force_feedback_effect(self):
        if getattr(unreal, 'AgentToolkitWorldLibrary', None) is None:
            self.skipTest('UEAgentToolkitNative not loaded')
        path = f'{TEST_ROOT}/FF_Test'
        r = self.assertOk(self.call('create_force_feedback_effect', asset_path=path, curve='0:1,0.15:0.5,0.4:0',
                                    channels='left_large,right_large'))
        self.assertAlmostEqual(r['duration'], 0.4)
        details = unreal.load_asset(path).get_editor_property('channel_details')
        self.assertEqual(len(details), 1)
        self.assertTrue(details[0].get_editor_property('affects_left_large'))
        self.assertFalse(details[0].get_editor_property('affects_left_small'))
        self.assertFails(self.call('create_force_feedback_effect', asset_path=path), 'ALREADY_EXISTS')
        self.assertFails(self.call('create_force_feedback_effect', asset_path=f'{TEST_ROOT}/FF_Bad', curve='0:2,1:0'),
                         'INVALID_ARGUMENT')
        self.assertFails(self.call('create_force_feedback_effect', asset_path=f'{TEST_ROOT}/FF_Bad', channels='top'),
                         'INVALID_ARGUMENT')

    @requires_editor_ui  # the node menu indexes new Input Actions only with the editor running
    def test_blueprint_wiring(self):
        self.assertOk(self.call('create_input_action', asset_path=f'{TEST_ROOT}/IA_Fire', value_type='bool'))
        self.assertOk(self.call('create_input_mapping_context', asset_path=f'{TEST_ROOT}/IMC_Wire'))
        unreal.EditorAssetLibrary.save_asset(f'{TEST_ROOT}/IA_Fire', False)
        self.make_blueprint('BP_InputChar', unreal.Character)
        bp_path = f'{TEST_ROOT}/BP_InputChar'
        w = self.assertOk(self.call('add_mapping_context_to_blueprint', blueprint_path=bp_path,
                                    context_path=f'{TEST_ROOT}/IMC_Wire', priority=0))
        self.assertIn(w['status'], ('up_to_date', 'up_to_date_with_warnings'))
        ev = self.assertOk(self.call('bind_input_action_event', blueprint_path=bp_path, action_path=f'{TEST_ROOT}/IA_Fire'))
        self.assertTrue(any(p['name'] == 'Triggered' for p in ev['pins']), ev['pins'])
        self.assertFails(self.call('bind_input_action_event', blueprint_path=bp_path, action_path=f'{TEST_ROOT}/IA_Fire'),
                         'ALREADY_EXISTS')
        self.assertTrue(call_tool(BuildDebugTools, 'compile_blueprint', asset_path=bp_path)['success'])
