"""Every toolset is registered and every tool returns the standard envelope schema."""

import json
import os
import unittest

import unreal

from agent_toolkit.toolsets import TOOLSET_CLASSES
from agent_toolkit.tests.base import toolset_name


class TestRegistration(unittest.TestCase):

    def test_all_toolsets_registered(self):
        for cls in TOOLSET_CLASSES:
            self.assertTrue(unreal.ToolsetRegistry.is_toolset_registered(toolset_name(cls)), toolset_name(cls))

    def test_superseded_epic_blueprint_tools_hidden(self):
        ini = os.path.join(unreal.Paths.project_config_dir(), 'DefaultEditorPerProjectUserSettings.ini')
        text = open(ini, encoding='utf-8').read() if os.path.exists(ini) else ''
        if 'editor_toolset\.toolsets\.blueprint\.BlueprintTools' not in text:
            self.skipTest('BlueprintTools is not blocked in this project')
        result = unreal.ToolsetRegistry.execute_tool('editor_toolset.toolsets.blueprint.BlueprintTools',
                                                     'list_graphs', '{}')
        self.assertTrue(result.error, 'blocked BlueprintTools still executed')
        self.assertIn('Toolset', str(result.error))
        other = unreal.ToolsetRegistry.execute_tool('agent_toolkit.toolsets.inspector.InspectorTools',
                                                    'get_editor_state', '{}')
        self.assertFalse(other.error, other.error)

    def test_schemas_have_tools_with_descriptions(self):
        for cls in TOOLSET_CLASSES:
            schema = json.loads(unreal.ToolsetRegistry.get_toolset_json_schema(cls))
            tools = schema.get('tools', [])
            self.assertTrue(tools, f'{toolset_name(cls)} exposes no tools')
            for tool in tools:
                self.assertTrue(tool.get('description'), f"{tool.get('name')} has no description")
                self.assertIn('inputSchema', tool)
                out = tool.get('outputSchema', {}).get('properties', {}).get('returnValue', {})
                self.assertEqual(out.get('type'), 'string', f"{tool['name']}: envelope must be a JSON string")
                props = tool['inputSchema'].get('properties', {})
                for pname, pschema in props.items():
                    if pschema.get('type') == 'array':
                        self.assertNotIn('default', pschema, f"{tool['name']}.{pname}: list params cannot be optional")

    def test_tool_names_are_unique_snake_case(self):
        seen = {}
        for cls in TOOLSET_CLASSES:
            schema = json.loads(unreal.ToolsetRegistry.get_toolset_json_schema(cls))
            for tool in schema.get('tools', []):
                short = tool['name'].rsplit('.', 1)[-1]
                self.assertEqual(short, short.lower(), short)
                self.assertNotIn(short, seen, f'{short} defined in {seen.get(short)} and {cls.__name__}')
                seen[short] = cls.__name__
