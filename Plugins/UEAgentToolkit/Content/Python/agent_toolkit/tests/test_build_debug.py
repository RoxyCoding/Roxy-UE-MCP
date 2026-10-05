import unreal

from agent_toolkit.tests.base import TEST_ROOT, ToolTestCase
from agent_toolkit.toolsets.build_debug import BuildDebugTools


class TestBuildDebug(ToolTestCase):
    toolset = BuildDebugTools

    def test_compile_clean_blueprint(self):
        self.make_blueprint('BP_Clean')
        d = self.assertOk(self.call('compile_blueprint', asset_path=f'{TEST_ROOT}/BP_Clean'))
        self.assertIn(d['status'], ('up_to_date', 'up_to_date_with_warnings'))
        s = self.assertOk(self.call('get_blueprint_compile_status', asset_path=f'{TEST_ROOT}/BP_Clean'))
        self.assertEqual(s['errors'], [])

    def test_compile_error_is_reported_with_node_context(self):
        bp = self.make_blueprint('BP_Broken')
        graph = unreal.BlueprintEditorLibrary.find_event_graph(bp)
        ed = unreal.BlueprintGraphEditor.get_graph_editor(graph)
        # A Pawn-only function in an Actor Blueprint with no Target connected cannot compile.
        call = ed.add_call_function_node('/Script/Engine.Pawn:AddMovementInput')
        self.assertIsNotNone(call, 'could not create AddMovementInput node')
        begin = ed.find_event_node('ReceiveBeginPlay')
        if begin is not None:
            begin.find_then_pin().try_create_connection(call.find_execute_pin())
        env = self.call('compile_blueprint', asset_path=f'{TEST_ROOT}/BP_Broken')
        err = self.assertFails(env, 'COMPILE_FAILED')
        self.assertTrue(err['likely_causes'])
        self.assertTrue(env['details']['errors'][0]['node'])

    def test_compile_folder(self):
        self.make_blueprint('BP_Folder1')
        d = self.assertOk(self.call('compile_blueprints_in_folder', path=TEST_ROOT, only_report_problems=False))
        self.assertGreaterEqual(d['compiled'], 1)

    def test_compile_missing_and_wrong_type(self):
        self.assertFails(self.call('compile_blueprint', asset_path=f'{TEST_ROOT}/BP_None'), 'ASSET_NOT_FOUND')
        self.assertFails(self.call('compile_blueprint', asset_path='/Engine/BasicShapes/Cube'), 'WRONG_TYPE')

    def test_log_mark_and_queries(self):
        self.assertOk(self.call('clear_log', mark_name='t1'))
        unreal.log_error('AGENT_TEST_ERROR missing /Game/Characters/BP_DoesNotExist asset')
        unreal.log_warning('AGENT_TEST_WARNING something odd')
        errors = self.assertOk(self.call('get_log_errors', since_mark='t1'))
        msgs = [e['message'] for e in errors['entries']]
        self.assertTrue(any('AGENT_TEST_ERROR' in m for m in msgs), msgs[-5:])
        hit = next(e for e in errors['entries'] if 'AGENT_TEST_ERROR' in e['message'])
        self.assertIn('/Game/Characters/BP_DoesNotExist', hit['refs']['asset_paths'])
        warnings = self.assertOk(self.call('get_log_warnings', since_mark='t1'))
        self.assertTrue(any('AGENT_TEST_WARNING' in e['message'] for e in warnings['entries']))
        self.assertNotIn('AGENT_TEST_WARNING', str(errors))
        s = self.assertOk(self.call('search_log', pattern='AGENT_TEST_(ERROR|WARNING)', since_mark='t1'))
        self.assertEqual(s['matches'], 2)
        self.assertOk(self.call('get_output_log', since_mark='t1', max_severity='Warning'))
        self.assertFails(self.call('search_log', pattern='(unclosed'), 'INVALID_ARGUMENT')

    def test_runtime_and_asserts(self):
        self.assertOk(self.call('get_runtime_errors', since_mark=None))
        self.assertOk(self.call('get_asserts_and_ensures'))

    def test_resolve_error_context(self):
        d = self.assertOk(self.call('resolve_error_context', error_text=
                                    'Accessed None trying to read property Mesh Node: Set Visibility '
                                    'Graph: EventGraph Function: Execute Ubergraph BP_Foo Blueprint: BP_Foo '
                                    '/Game/Nowhere/BP_Foo.BP_Foo'))
        self.assertEqual(d['references']['blueprint_context']['blueprint'], 'BP_Foo /Game/Nowhere/BP_Foo.BP_Foo')
        self.assertFalse(d['paths'][0]['exists'])

    def test_cpp_build_blueprint_only_project(self):
        self.assertFails(self.call('start_cpp_build'), 'NOT_SUPPORTED')
        self.assertFails(self.call('get_cpp_build_status', job_id='nope'), 'OBJECT_NOT_FOUND')

    def test_message_log_requires_native_or_works(self):
        env = self.call('get_message_log', log_name='BlueprintLog')
        if not env['success']:
            self.assertEqual(env['errors'][0]['code'], 'NOT_SUPPORTED')
