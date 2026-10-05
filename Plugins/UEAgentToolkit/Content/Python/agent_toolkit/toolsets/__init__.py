"""Toolset registration for UE Agent Toolkit.

Toolset names exposed through the ToolsetRegistry / Unreal MCP are the Python
qualified names, e.g. `agent_toolkit.toolsets.inspector.InspectorTools`.
"""

from toolset_registry.registration import Registration

from agent_toolkit.toolsets import ai
from agent_toolkit.toolsets import animation
from agent_toolkit.toolsets import assets
from agent_toolkit.toolsets import audio
from agent_toolkit.toolsets import blueprint_authoring
from agent_toolkit.toolsets import build_debug
from agent_toolkit.toolsets import input as input_tools
from agent_toolkit.toolsets import inspector
from agent_toolkit.toolsets import level
from agent_toolkit.toolsets import material_authoring
from agent_toolkit.toolsets import metasound
from agent_toolkit.toolsets import networking
from agent_toolkit.toolsets import packaging
from agent_toolkit.toolsets import performance
from agent_toolkit.toolsets import safety
from agent_toolkit.toolsets import umg
from agent_toolkit.toolsets import validation
from agent_toolkit.toolsets import world

TOOLSET_CLASSES = [
    inspector.InspectorTools,
    assets.AssetManagementTools,
    level.LevelTools,
    build_debug.BuildDebugTools,
    validation.ValidationTools,
    safety.SafetyTools,
    # Phase 2
    blueprint_authoring.BlueprintAuthoringTools,
    material_authoring.MaterialAuthoringTools,
    input_tools.InputTools,
    # Phase 3
    animation.AnimationTools,
    ai.AITools,
    audio.AudioTools,
    # Phase 4
    networking.NetworkingTools,
    performance.PerformanceTools,
    # Additions
    umg.UMGTools,
    metasound.MetaSoundTools,
    world.WorldTools,
    packaging.PackagingTools,
]

_registration = Registration(TOOLSET_CLASSES)
