"""Test helpers: call tools through the ToolsetRegistry exactly like the MCP server does."""

from __future__ import annotations

import json
import unittest
from typing import Any

import unreal

from agent_toolkit.core import editor

TEST_ROOT = '/Game/__AgentToolkitTests__'

# Commandlets have no undo buffer / Level Editor UI.
requires_editor_ui = unittest.skipIf(editor.is_commandlet(), 'requires the full editor (undo buffer)')


def toolset_name(toolset_cls: type) -> str:
    return f'{toolset_cls.__module__}.{toolset_cls.__name__}'


def call_tool(toolset_cls: type, tool: str, **kwargs: Any) -> dict:
    """Executes a tool via the registry (JSON in / JSON out) and returns the envelope
    (the tool's JSON string return value)."""
    name = toolset_name(toolset_cls)
    result = unreal.ToolsetRegistry.execute_tool(name, tool, json.dumps(kwargs))
    for _ in range(1000):
        if result.is_complete:
            break
    if not result.is_complete:
        raise AssertionError(f'{name}.{tool} did not complete synchronously')
    if result.error:
        raise AssertionError(f'{name}.{tool} registry error: {result.error}')
    envelope = json.loads(json.loads(result.value)['returnValue'])
    return envelope


class ToolTestCase(unittest.TestCase):
    """Base class: provides a scratch content folder that is deleted afterwards."""

    toolset: type = None  # set by subclasses

    @classmethod
    def setUpClass(cls):
        eas = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
        if not eas.does_directory_exist(TEST_ROOT):
            eas.make_directory(TEST_ROOT)

    @classmethod
    def tearDownClass(cls):
        eas = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
        for path in eas.list_assets(TEST_ROOT, True, False) or []:
            eas.delete_asset(path)
        if eas.does_directory_exist(TEST_ROOT):
            eas.delete_directory(TEST_ROOT)

    def call(self, tool: str, **kwargs: Any) -> dict:
        return call_tool(self.toolset, tool, **kwargs)

    def assertOk(self, env: dict) -> dict:
        self.assertTrue(env['success'], f"{env['tool']} failed: {env['errors']}")
        return env['details']

    def assertFails(self, env: dict, code: str | None = None) -> dict:
        self.assertFalse(env['success'], f"{env['tool']} unexpectedly succeeded: {env['details']}")
        self.assertTrue(env['errors'], 'failure without structured errors')
        if code:
            self.assertEqual(env['errors'][0]['code'], code, env['errors'])
        return env['errors'][0]

    def make_blueprint(self, name: str, parent: type = unreal.Actor) -> unreal.Blueprint:
        path = f'{TEST_ROOT}/{name}'
        eas = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
        if eas.does_asset_exist(path):
            eas.delete_asset(path)
        return unreal.BlueprintEditorLibrary.create_blueprint_asset_with_parent(path, parent.static_class())

    def spawn(self, cls: type = unreal.StaticMeshActor, label: str = 'AgentTestActor') -> unreal.Actor:
        eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        actor = eas.spawn_actor_from_class(cls, unreal.Vector(0, 0, 100))
        actor.set_actor_label(label)
        return actor

    @staticmethod
    def destroy(actor: unreal.Actor) -> None:
        if actor:
            unreal.get_editor_subsystem(unreal.EditorActorSubsystem).destroy_actor(actor)
