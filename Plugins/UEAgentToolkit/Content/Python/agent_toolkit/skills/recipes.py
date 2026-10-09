"""Agent skill: proven tool sequences for common game-development requests."""

import unreal

from toolset_registry.agent_skill import agent_skill

_INSTRUCTIONS = """\
PLAYER CHARACTER (move / jump / camera / input)
1. Blueprint with parent Character; add SpringArmComponent (use_pawn_control_rotation=true) and
   CameraComponent under it; give the Mesh a skeletal mesh + anim class if available.
2. Input Actions: IA_Move (axis2d), IA_Look (axis2d), IA_Jump (bool). Mapping Context:
   W=swizzle, S=swizzle,negate, A=negate, D; Mouse2D for look (negate:y if inverted); SpaceBar jump.
   Controller: add_gamepad_mappings on the context (Left2D move, Right2D look, buttons).
3. register_default_mapping_context (project-wide) or add_mapping_context_to_blueprint.
4. bind_input_action_event per action; wire IA_Jump Triggered->Character:Jump,
   Completed->Character:StopJumping; IA_Move Triggered -> Pawn:AddMovementInput twice
   (GetActorForwardVector * ActionValue.Y, GetActorRightVector * ActionValue.X; use
   Break Vector 2D via KismetMathLibrary:BreakVector2D); IA_Look -> AddControllerYawInput /
   AddControllerPitchInput.
5. Set the Blueprint as the GameMode DefaultPawnClass (or level override), place a
   PlayerStart, compile, validate.

ENEMY AI THAT DETECTS AND CHASES
- AIController Blueprint with AIPerceptionComponent (sight config); Character Blueprint with
  AIControllerClass set and auto_possess_ai = PLACED_IN_WORLD_OR_SPAWNED.
- Simple chase: on perception updated -> AIBlueprintHelperLibrary:SimpleMoveToActor or AI MoveTo.
- Behavior Tree route: Blackboard keys (TargetActor object:Actor), BT Selector ->
  MoveTo(TargetActor) when set; RunBehaviorTree on BeginPlay of the controller.
- Always add a NavMeshBoundsVolume covering walkable space; validate_level warns otherwise.

HUD / PAUSE MENU
- Widget Blueprints (UMG toolsets) for HUD and pause menu; create + AddToViewport on BeginPlay
  of the PlayerController/Character; pause via GameplayStatics:SetGamePaused and input mode
  WidgetBlueprintLibrary:SetInputMode_UIOnlyEx / GameOnly; bind an IA_Pause action.
- Controller: setup_gamepad_navigation on the menu buttons (focus + D-pad/stick navigation);
  map IA_Pause to Gamepad_Special_Right (add_gamepad_mappings does this for Escape/P).

ATTACK SETUP
- Montage from an attack sequence (sections/notifies), Character:PlayAnimMontage from an
  IA_Attack event; damage via GameplayStatics:ApplyDamage on overlap/trace; Niagara system and
  sound spawned at the notify/hit location (GameplayStatics:SpawnSoundAtLocation,
  NiagaraFunctionLibrary:SpawnSystemAtLocation).

PROJECT HEALTH
- "Fix Blueprint errors": compile_blueprints_in_folder -> per error inspect graph nodes -> fix ->
  compile_blueprint -> validate_project(compile_blueprints=true).
- "Unused assets / broken references": save first, then find_missing_references,
  find_broken_assets, find_redirectors, find_unused_assets; report before deleting.
"""


@agent_skill
class AgentToolkitRecipesSkill(unreal.AgentSkill):
    """Step outlines for frequent requests: player character with input and camera, chasing
    enemy AI, HUD and pause menu, attack setup, project health checks. Apply when planning
    multi-step gameplay setup work with agent_toolkit tools."""

    instructions = _INSTRUCTIONS
