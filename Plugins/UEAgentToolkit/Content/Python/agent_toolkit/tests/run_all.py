"""Runs the UE Agent Toolkit test suite inside the editor (GUI or commandlet).

Usage (commandlet, from the engine Binaries folder):
    UnrealEditor-Cmd.exe <Project>.uproject -run=pythonscript
        -script="<Plugin>/Content/Python/agent_toolkit/tests/run_all.py" -unattended -nullrhi
Optional: set env AGENT_TOOLKIT_TESTS="test_inspector,test_assets" to run a subset.
Prints one 'AGENT_TOOLKIT_TEST_SUMMARY {...}' JSON line for CI parsing.
"""

import io
import json
import os
import sys
import unittest

import unreal

import agent_toolkit.tests as tests_pkg


def run() -> bool:
    loader = unittest.TestLoader()
    selected = [s.strip() for s in os.environ.get('AGENT_TOOLKIT_TESTS', '').split(',') if s.strip()]
    if selected:
        suite = unittest.TestSuite(loader.loadTestsFromName(f'agent_toolkit.tests.{name}') for name in selected)
    else:
        suite = loader.discover(os.path.dirname(tests_pkg.__file__), pattern='test_*.py',
                                top_level_dir=os.path.dirname(os.path.dirname(os.path.dirname(tests_pkg.__file__))))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    for line in stream.getvalue().splitlines():
        unreal.log(f'[AgentToolkitTests] {line}')
    summary = {'run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
               'skipped': len(result.skipped), 'ok': result.wasSuccessful()}
    unreal.log('AGENT_TOOLKIT_TEST_SUMMARY ' + json.dumps(summary))
    return result.wasSuccessful()


if __name__ == '__main__':
    run()
    if os.environ.get('AGENT_TOOLKIT_QUIT') == '1':
        unreal.SystemLibrary.quit_editor()
