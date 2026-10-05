"""UE Agent Toolkit startup: registers toolsets with Epic's ToolsetRegistry."""

import unreal

try:
    from agent_toolkit import toolsets
    from agent_toolkit import skills  # noqa: F401  (imports register @agent_skill classes)
    from agent_toolkit import tests

    if toolsets._registration.register():  # pylint: disable=protected-access
        unreal.log(f'[UEAgentToolkit] registered {len(toolsets.TOOLSET_CLASSES)} toolsets')
    else:
        unreal.log_warning('[UEAgentToolkit] ToolsetRegistry not available; toolsets not registered')

    tests._test_runner = unreal.PythonTestRunner.create(  # pylint: disable=protected-access
        'AI.Toolsets.UEAgentToolkit',
        unreal.PythonTestRunnerSearchOptions(root_module=tests.__name__))
except Exception as e:  # pylint: disable=broad-exception-caught
    unreal.log_error(f'[UEAgentToolkit] failed to initialize: {e}')
    raise
