import unreal

from agent_toolkit.tests.base import ToolTestCase, requires_editor_ui
from agent_toolkit.toolsets.level import LevelTools
from agent_toolkit.toolsets.safety import SafetyTools
from agent_toolkit.tests.base import call_tool


def _actor(label):
    for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors():
        if a.get_actor_label() == label:
            return a
    return None


class TestLevel(ToolTestCase):
    toolset = LevelTools

    def tearDown(self):
        eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        for a in eas.get_all_level_actors():
            if a.get_actor_label().startswith('LT_'):
                eas.destroy_actor(a)

    def test_spawn_actor_class_and_asset(self):
        d = self.assertOk(self.call('spawn_actor', class_or_asset='PointLight', location=[0, 0, 200], rotation=[0, 0, 0],
                                    scale=[1, 1, 1], label='LT_Light', folder='Test/Lights'))
        self.assertEqual(d['class'], 'PointLight')
        self.assertEqual(str(_actor('LT_Light').get_folder_path()), 'Test/Lights')
        d = self.assertOk(self.call('spawn_actor', class_or_asset='/Engine/BasicShapes/Cube', location=[100, 0, 0],
                                    rotation=[0, 45, 0], scale=[2, 2, 2], label='LT_Cube',
                                    properties_json='{"can_be_damaged": false}'))
        self.assertEqual(d['class'], 'StaticMeshActor')
        self.assertFalse(_actor('LT_Cube').get_editor_property('can_be_damaged'))

    def test_spawn_errors(self):
        self.assertFails(self.call('spawn_actor', class_or_asset='NoSuchActorClass', location=[0, 0, 0],
                                   rotation=[0, 0, 0], scale=[1, 1, 1]), 'CLASS_NOT_FOUND')
        self.assertFails(self.call('spawn_actor', class_or_asset='StaticMeshComponent', location=[0, 0, 0],
                                   rotation=[0, 0, 0], scale=[1, 1, 1]), 'WRONG_TYPE')
        self.assertFails(self.call('spawn_actor', class_or_asset='PointLight', location=[0, 0],
                                   rotation=[0, 0, 0], scale=[1, 1, 1]), 'INVALID_ARGUMENT')
        self.assertFails(self.call('spawn_actor', class_or_asset='PointLight', location=[0, 0, 0],
                                   rotation=[0, 0, 0], scale=[1, 1, 1], properties_json='{"no_such_prop": 1}'),
                         'INVALID_ARGUMENT')

    def test_helpers(self):
        self.assertOk(self.call('add_light', light_type='spot', location=[0, 0, 300], rotation=[-90, 0, 0],
                                intensity=5000, color='1,0.5,0.2', label='LT_Spot'))
        self.assertFails(self.call('add_light', light_type='laser', location=[0, 0, 0], rotation=[0, 0, 0]),
                         'INVALID_ARGUMENT')
        self.assertOk(self.call('add_camera', location=[0, -500, 200], rotation=[0, 90, 0], label='LT_Cam'))
        v = self.assertOk(self.call('add_volume', volume_type='nav_mesh_bounds', location=[0, 0, 0],
                                    extent=[1000, 1000, 200], label='LT_Nav'))
        self.assertEqual(v['class'], 'NavMeshBoundsVolume')
        self.assertOk(self.call('add_volume', volume_type='trigger_box', location=[0, 0, 0], extent=[50, 50, 50],
                                label='LT_Trigger'))
        self.assertOk(self.call('add_player_start', location=[0, 0, 100], yaw=90, label='LT_Start'))

    @requires_editor_ui
    def test_duplicate_attach_detach_delete_and_undo(self):
        self.assertOk(self.call('spawn_actor', class_or_asset='/Engine/BasicShapes/Cube', location=[0, 0, 0],
                                rotation=[0, 0, 0], scale=[1, 1, 1], label='LT_Parent'))
        self.assertOk(self.call('spawn_actor', class_or_asset='/Engine/BasicShapes/Sphere', location=[0, 0, 100],
                                rotation=[0, 0, 0], scale=[1, 1, 1], label='LT_Child'))
        for label in ('LT_Parent', 'LT_Child'):
            _actor(label).root_component.set_mobility(unreal.ComponentMobility.MOVABLE)
        self.assertOk(self.call('attach_actor', child='LT_Child', parent='LT_Parent'))
        self.assertEqual(_actor('LT_Child').get_attach_parent_actor().get_actor_label(), 'LT_Parent')
        self.assertOk(self.call('detach_actor', actor='LT_Child'))
        dup = self.assertOk(self.call('duplicate_actors', actors=['LT_Child'], offset=[0, 200, 0]))
        self.assertEqual(len(dup['copies']), 1)
        self.assertFails(self.call('delete_actors', actors=['LT_Parent']), 'CONFIRMATION_REQUIRED')
        self.assertOk(self.call('delete_actors', actors=['LT_Parent'], confirm=True))
        self.assertIsNone(_actor('LT_Parent'))
        undo = call_tool(SafetyTools, 'undo')
        self.assertTrue(undo['success'], undo['errors'])
        self.assertIsNotNone(_actor('LT_Parent'), 'undo did not restore the deleted actor')

    def test_world_settings_and_game_mode(self):
        self.assertOk(self.call('set_world_settings', settings_json='{"kill_z": -12345}'))
        ws = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world().get_world_settings()
        self.assertAlmostEqual(ws.get_editor_property('kill_z'), -12345)
        self.assertOk(self.call('set_level_game_mode_override', game_mode_class='GameModeBase'))
        self.assertFails(self.call('set_level_game_mode_override', game_mode_class='Actor'), 'WRONG_TYPE')
        self.assertOk(self.call('set_level_game_mode_override'))
        dry = self.assertOk(self.call('set_project_maps_and_modes', global_default_game_mode='GameModeBase', dry_run=True))
        self.assertTrue(dry['dry_run'])
        self.assertFails(self.call('set_project_maps_and_modes'), 'INVALID_ARGUMENT')

    def test_organize_folders_dry_run(self):
        self.assertOk(self.call('add_player_start', location=[0, 0, 100], label='LT_Start2'))
        d = self.assertOk(self.call('organize_actors_into_folders', group_by='type', dry_run=True))
        self.assertTrue(d['dry_run'])

    def test_streaming_missing_level(self):
        self.assertFails(self.call('add_streaming_level', level_path='/Game/Maps/NoSuchLevel'), 'ASSET_NOT_FOUND')
