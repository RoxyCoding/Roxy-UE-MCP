# UE Agent Toolkit — Tool 一覧 (自動生成)

MCP では `call_tool(toolset_name="<Toolset>", tool_name="<tool>", arguments={...})` で呼び出します。
全 Tool は `AgentToolResult`（success / errors[] / warnings[] / target / modified / dirtied_packages[] / details_json）を返します。
合計 **195** Tools / 18 Toolsets

## `agent_toolkit.toolsets.inspector.InspectorTools` (16)

Read-only inspection of editor, level, actors, assets, Blueprints, materials,     AI assets and project settings, returned as compact structured JSON (details_json).

| Tool | 説明 |
|---|---|
| `get_class_hierarchy` | Returns the parent chain of a class and (optionally) its native and Blueprint subclasses. |
| `get_asset_referencers` | Returns which assets reference an asset (hard + soft), as a tree up to `depth` levels. |
| `get_asset_dependencies` | Returns what an asset depends on (hard + soft), as a tree up to `depth` levels, |
| `inspect_project_settings` | Returns key project settings: project file/name, engine version, Maps & Modes |
| `inspect_world_settings` | Returns the current level's World Settings: game mode override, kill Z, gravity, |
| `inspect_level` | Returns a summary of the currently loaded level: actor counts by class, gameplay |
| `inspect_skeleton` | Returns the bone hierarchy (name, parent) and sockets of a Skeleton or Skeletal Mesh. |
| `inspect_blackboard` | Returns Blackboard keys (name, key type, base class/enum, instance synced) and parent. |
| `inspect_behavior_tree` | Returns a Behavior Tree as a nested structure: composites, tasks, decorators and |
| `inspect_material` | Returns a Material or Material Instance as structured data: domain, blend mode, |
| `inspect_blueprint_graph` | Returns a Blueprint graph as structured data: nodes (id, title, class, position, |
| `inspect_blueprint` | Returns the full structure of a Blueprint without reading graphs node-by-node: |
| `inspect_asset` | Returns asset metadata: class, package, on-disk file and size, dirty state, |
| `inspect_component` | Returns details of one component on a level actor, optionally with reflected properties. |
| `inspect_actor` | Returns a structured description of a level actor: class (and Blueprint), |
| `get_editor_state` | Returns a one-call overview of the editor: project, engine version, current |

## `agent_toolkit.toolsets.assets.AssetManagementTools` (13)

Asset lifecycle tools with safety rails: search, generic create, rename/move with     reference fix-up, guarded deletion with backups, import/reimport/export, redirector     detection/fix-up and unused asset detection.

| Tool | 説明 |
|---|---|
| `find_unused_assets` | Finds assets that no other asset references (candidates for cleanup). Levels and |
| `fix_redirectors` | Fixes up redirectors: re-saves every referencer so it points at the real asset, then |
| `find_redirectors` | Lists ObjectRedirector assets (left by renames/moves) and their referencers. |
| `export_assets` | Exports assets to files on disk using their default exporters (e.g. FBX, PNG, T3D). |
| `reimport_assets` | Reimports assets from their original source files (meshes, textures, audio, tables). |
| `import_fbx` | Imports an FBX with explicit options (legacy FBX importer). |
| `import_files` | Imports files from disk (FBX/OBJ/glTF meshes, PNG/TGA/EXR textures, WAV/OGG audio, |
| `save_dirty_assets` | Saves all unsaved (dirty) packages ("Save All"), optionally limited to a folder. |
| `delete_assets` | Deletes assets safely. Without confirm=True this is a dry run that returns, for each |
| `move_assets` | Moves assets to another folder, fixing up references (redirectors may remain). |
| `rename_asset` | Renames an asset in place and fixes up references (a redirector may be left behind; |
| `create_asset` | Creates a new asset of any class that has a "create new" factory (Blueprint, |
| `search_assets` | Searches the asset registry by name substring, class and folder. |

## `agent_toolkit.toolsets.level.LevelTools` (18)

Level creation/saving, actor spawning (generic + light/camera/volume/PlayerStart helpers),     duplication/deletion, attachment, World Settings, GameMode and default maps, level     streaming and folder organisation. All actor edits are undoable.

| Tool | 説明 |
|---|---|
| `organize_actors_into_folders` | Organises Outliner folders automatically. |
| `set_streaming_level_visibility` | Shows or hides a sublevel in the editor. |
| `remove_streaming_level` | Removes a streaming sublevel from the current persistent level (the asset is kept). |
| `add_streaming_level` | Adds an existing level as a streaming sublevel of the current persistent level. |
| `set_project_maps_and_modes` | Sets project-wide Maps & Modes (Config/DefaultEngine.ini, backed up first) and applies |
| `set_level_game_mode_override` | Sets (or clears) the GameMode override of the current level (World Settings). |
| `set_world_settings` | Sets World Settings properties of the current level, e.g. |
| `detach_actor` | Detaches an actor from its parent, keeping its world transform. |
| `attach_actor` | Attaches one actor to another (optionally to a socket/bone on the parent's root). |
| `delete_actors` | Deletes level actors (undoable with SafetyTools.undo). Without confirm=True only |
| `duplicate_actors` | Duplicates actors with a location offset. |
| `add_player_start` | Adds a PlayerStart (player spawn point). |
| `add_volume` | Adds a volume or trigger sized by its half-extent. |
| `add_camera` | Adds a camera actor (CameraActor or CineCameraActor). |
| `add_light` | Adds a light actor. |
| `spawn_actor` | Spawns an actor from a class (e.g. "PointLight", "/Game/BP/BP_Enemy") or from an |
| `save_current_level` | Saves the currently open level (and its external actors). Untitled levels need save_as_path. |
| `create_level` | Creates a new level asset and opens it. The current level is closed without saving, |

## `agent_toolkit.toolsets.build_debug.BuildDebugTools` (14)

Compile Blueprints with structured error reports, launch/poll C++ builds, read and     search the Output Log by severity since a mark, extract runtime errors and     asserts/ensures, and trace error text back to assets.

| Tool | 説明 |
|---|---|
| `resolve_error_context` | Traces an error message back to project content: extracts asset paths and Blueprint |
| `get_message_log` | Returns messages from an editor Message Log listing (BlueprintLog, MapCheck, PIE, |
| `get_asserts_and_ensures` | Returns assertion failures, ensures and crashes recorded in the log, each with its |
| `get_runtime_errors` | Extracts Blueprint/script runtime errors (Accessed None, Blueprint Runtime Error, |
| `clear_log` | Sets a log mark at the current end of the Output Log. Subsequent log queries with |
| `search_log` | Searches the Output Log with a regular expression. |
| `get_log_warnings` | Returns only Warning log entries since the mark. |
| `get_log_errors` | Returns only Error/Fatal log entries (with referenced assets) since the mark. |
| `get_output_log` | Returns parsed Output Log entries (category, severity, message, continuation lines, |
| `get_cpp_build_status` | Returns the state of a C++ build job: running/succeeded/failed, compiler errors and |
| `start_cpp_build` | Starts an Unreal Build Tool build of the project's C++ in the background and returns |
| `get_blueprint_compile_status` | Returns the current compile status and node messages of a Blueprint WITHOUT recompiling. |
| `compile_blueprints_in_folder` | Compiles every Blueprint under a folder and summarises the results. |
| `compile_blueprint` | Compiles one Blueprint and returns status plus every node-level error/warning |

## `agent_toolkit.toolsets.validation.ValidationTools` (16)

Checks for compile errors, missing/broken references, invalid assets, null properties,     missing materials, invalid collision, duplicate names, naming/placement rules,     redirectors and unsaved assets, plus explicit auto-fix tools. Run validate_project     for a one-call health report.

| Tool | 説明 |
|---|---|
| `add_simple_collision_to_meshes` | Adds a simple collision primitive to static meshes that have none. |
| `remove_unused_blueprint_variables` | Removes Blueprint variables that are not referenced by any graph. Without |
| `fix_naming_conventions` | Renames assets to add the expected class prefix (references are fixed up; run |
| `find_unsaved_assets` | Lists all packages (assets and levels) with unsaved changes. |
| `check_asset_placement` | Checks asset placement: assets in the /Game root, developer-folder assets referenced |
| `check_naming_conventions` | Checks asset name prefixes per class (BP_, M_, MI_, T_, SM_, SK_, ABP_, WBP_, NS_, |
| `find_duplicate_asset_names` | Finds assets that share a name across folders (ambiguous for humans and agents). |
| `find_invalid_collision` | Finds static meshes with no simple collision that are not set to use complex |
| `find_missing_materials` | Finds static/skeletal mesh assets with empty material slots (loads meshes in the folder). |
| `find_null_properties` | Lists object/class references that are None on a Blueprint's defaults or on a level |
| `find_broken_assets` | Finds broken assets: redirectors pointing nowhere, assets whose class is no longer |
| `find_missing_references` | Lists assets that reference packages which no longer exist (broken dependencies). |
| `validate_level` | Validates the open level: missing meshes/materials, invalid physics collision, |
| `validate_blueprint` | Validates one Blueprint: compile errors/warnings, missing parent, unused variables, |
| `validate_assets` | Runs Epic's Data Validation (all registered validators, incl. project-specific ones) on assets. |
| `validate_project` | One-call health report over a folder: missing references, broken assets, |

## `agent_toolkit.toolsets.safety.SafetyTools` (13)

Transactions (group edits into one undo step), undo/redo, list of modified assets,     file backups and named save points with restore, deletion previews and a journal of     every change made through UE Agent Toolkit tools.

| Tool | 説明 |
|---|---|
| `restore_save_point` | Restores the most recent save point with this name (see create_save_point). |
| `create_save_point` | Creates a named save point: a backup of the given assets plus (optionally) every |
| `restore_backup` | Restores files from a backup over the current assets and reloads them. Without |
| `list_backups` | Lists backups and save points, newest first. |
| `create_backup` | Copies the on-disk files of assets to Saved/AgentToolkit/Backups/<id>. Unsaved |
| `preview_asset_deletion` | Read-only report used before deleting assets: existence, external referencers, |
| `get_change_journal` | Returns the journal of modifications made through UE Agent Toolkit tools in this |
| `list_modified_assets` | Lists unsaved (dirty) packages, split into content and levels, flagging never-saved ones. |
| `get_undo_state` | Returns the undo stack depth, open toolkit transactions and the latest journal entries. |
| `redo` | Redoes previously undone transactions. |
| `undo` | Undoes the most recent editor transactions (also those made by the user). |
| `end_transaction` | Closes the transaction opened by begin_transaction, committing it as one undo step. |
| `begin_transaction` | Opens an undo transaction so that all following edits (from any tool) undo as one step. |

## `agent_toolkit.toolsets.blueprint_authoring.BlueprintAuthoringTools` (21)

Blueprint editing: typed variables with defaults/replication, components (SCS),     functions with typed signatures, custom events (incl. RPC), macros, interfaces, and     name-addressed graph editing (add node, connect/disconnect pins, pin defaults, remove/move     nodes). Pair with InspectorTools.inspect_blueprint(_graph) and BuildDebugTools.compile_blueprint.

| Tool | 説明 |
|---|---|
| `search_blueprint_node_actions` | Searches the node actions available in a graph (the editor's right-click menu). Strings |
| `move_blueprint_node` | Moves a node to a new graph position. |
| `remove_blueprint_nodes` | Deletes nodes (and their links) from a graph. |
| `set_blueprint_pin_defaults` | Sets default values of unconnected input pins, e.g. {"K2Node_CallFunction_0.InString": "Hello", |
| `disconnect_blueprint_pins` | Breaks links of a pin (all links, or only the link to other_pin). |
| `connect_blueprint_pins` | Connects pins. Each connection is "FromNode.OutPin->ToNode.InPin" using ids from |
| `add_blueprint_node` | Adds a node using locale-independent identifiers and returns its id and pins. |
| `remove_blueprint_interface` | Removes an implemented interface (requires the native module). |
| `implement_blueprint_interface` | Adds an interface (Blueprint Interface asset or native UInterface) to a Blueprint and |
| `create_blueprint_macro` | Creates an empty macro graph (requires the native module). Add tunnel pins and |
| `add_custom_event` | Adds a Custom Event, optionally with inputs and RPC replication (Server/Client/Multicast). |
| `set_blueprint_function_flags` | Changes function specifiers. Only provided arguments are changed. |
| `create_blueprint_function` | Creates a function graph with a typed signature. |
| `attach_blueprint_component` | Re-parents a scene component within the Blueprint's component tree. |
| `set_blueprint_component_properties` | Sets default properties of a component template in a Blueprint, e.g. |
| `remove_blueprint_component` | Removes a component added in this Blueprint (inherited/native components cannot be removed). |
| `add_blueprint_component` | Adds a component to a Blueprint's component tree (SCS), optionally under a parent |
| `set_blueprint_variable_flags` | Updates variable metadata. Only provided arguments are changed. |
| `change_blueprint_variable_type` | Changes the type of a Blueprint variable (connected pins of incompatible type will break; |
| `set_blueprint_variable_default` | Sets the default value of a Blueprint variable (or any inherited CDO property). |
| `add_blueprint_variable` | Adds a member variable with type, default value, category, editability and replication in one call. |

## `agent_toolkit.toolsets.material_authoring.MaterialAuthoringTools` (6)

Material graph authoring helpers: settings (domain/blend/shading), parameter and texture     nodes in one call, material function calls, batch wiring with "Expr.Output->Expr.Input" /     "Expr.Output->@base_color", and compile with error reporting.

| Tool | 説明 |
|---|---|
| `compile_material` | Recompiles a Material and returns compile errors plus shader statistics. Fails with |
| `connect_material_expressions` | Wires expressions in batch. Formats: "ExprName.Output->ExprName.Input" or |
| `add_material_function_call` | Adds a Material Function Call node for a MaterialFunction asset. |
| `add_texture_sample` | Adds a Texture Sample (or a Texture parameter when parameter_name is given) for a texture. |
| `add_material_parameter` | Adds a Scalar/Vector/Texture/StaticSwitch parameter node with name, default and group, |
| `set_material_settings` | Sets material-level settings. Only provided arguments change. |

## `agent_toolkit.toolsets.input.InputTools` (9)

Enhanced Input authoring: Input Actions, Mapping Contexts, key mappings with triggers and     modifiers, project default mapping contexts, and Blueprint wiring (AddMappingContext on     BeginPlay, Enhanced Input Action events).

| Tool | 説明 |
|---|---|
| `bind_input_action_event` | Adds an Enhanced Input Action event node (Triggered/Started/Ongoing/Canceled/Completed |
| `add_mapping_context_to_blueprint` | Wires "Event BeginPlay -> Get Player Controller(0) -> Enhanced Input Local Player Subsystem |
| `register_default_mapping_context` | Adds a Mapping Context to Enhanced Input's project-wide Default Mapping Contexts |
| `list_key_mappings` | Lists all mappings of a Mapping Context with keys, triggers, modifiers and action value types. |
| `remove_key_mapping` | Removes the mapping of a key (or all keys when key is omitted) for an action. |
| `update_key_mapping` | Replaces the triggers and/or modifiers of an existing mapping ("none" clears the list). |
| `add_key_mapping` | Maps a key to an Input Action inside a Mapping Context, with optional triggers/modifiers. |
| `create_input_mapping_context` | Creates an Input Mapping Context asset. |
| `create_input_action` | Creates an Input Action asset. |

## `agent_toolkit.toolsets.animation.AnimationTools` (13)

Animation authoring: Anim Blueprints with state machines (states playing sequences or     blend spaces, transitions with bool/auto rules), montages with sections and notifies,     1D/2D blend spaces, and structured inspection of animation assets.

| Tool | 説明 |
|---|---|
| `inspect_animation_asset` | Returns animation asset info: class, skeleton, length, frames, rate scale, notifies |
| `retarget_animations` | Duplicates animations (sequences, montages, blend spaces) and retargets them to the target |
| `create_ik_retargeter` | Creates an IK Retargeter between two IK Rigs: default ops, fuzzy chain mapping and |
| `create_ik_rig` | Creates an IK Rig for a skeletal mesh, auto-generating retarget chains (and optionally a |
| `create_blend_space` | Creates a blend space (1D when y_name is omitted, 2D otherwise) with axes and samples. |
| `add_animation_notify` | Adds an Anim Notify (or Notify State when duration > 0) to a sequence or montage. |
| `add_montage_section` | Adds a named section to a montage (e.g. combo steps). Sections are not auto-linked. |
| `create_anim_montage` | Creates an Anim Montage from an animation sequence (default slot). |
| `inspect_anim_state_machines` | Returns all state machines of an Anim Blueprint: entry state, states (with animation |
| `add_anim_transition` | Adds a transition between two states with a rule. |
| `add_anim_state` | Adds a state that plays a sequence or blend space. The first state becomes the entry state. |
| `add_anim_state_machine` | Adds a state machine node to the AnimGraph (optionally wired to Output Pose). |
| `create_anim_blueprint` | Creates an Animation Blueprint targeting a skeleton. |

## `agent_toolkit.toolsets.ai.AITools` (18)

AI authoring: Blackboard keys, Behavior Tree nodes/decorators/services with properties and     blackboard key bindings, AI Perception (sight/hearing) on AI Controllers, pawn AI controller     setup, navigation status, path tests and NavMesh rebuild.

| Tool | 説明 |
|---|---|
| `rebuild_navigation` | Rebuilds navigation data for the open level (editor command RebuildNavigation). |
| `test_navigation_path` | Tests whether a navigation path exists between two points in the open level. |
| `get_navigation_status` | Reports navigation setup of the open level: NavMeshBoundsVolumes (with extents), |
| `set_pawn_ai_controller` | Sets the AI Controller class and auto-possess mode of a Pawn/Character Blueprint. |
| `add_ai_perception` | Adds an AIPerceptionComponent with Sight (and optionally Hearing) to an AI Controller |
| `inspect_eqs_query` | Returns an Environment Query's options: generator class/description and tests with purpose. |
| `remove_eqs_item` | Removes an EQS option (test_index -1) or a single test. |
| `set_eqs_properties` | Sets properties of an EQS generator (test_index -1) or test, by C++ property name. |
| `add_eqs_test` | Adds a test to an EQS option. |
| `add_eqs_generator` | Adds an option (generator) to an Environment Query (create the query asset with |
| `inspect_bt_graph` | Returns the editor graph of a Behavior Tree with node ids (needed by the editing tools), |
| `remove_bt_node` | Removes a node (and its decorators/services; children become unconnected). |
| `set_bt_node_properties` | Sets properties of a Behavior Tree node instance. FBlackboardKeySelector properties take |
| `add_bt_subnode` | Adds a decorator (condition) or service (periodic update) to a composite/task node. |
| `add_bt_node` | Adds a composite (Selector/Sequence) or task (MoveTo, Wait, ...) under a parent. Children |
| `set_behavior_tree_blackboard` | Assigns the Blackboard asset used by a Behavior Tree. |
| `remove_blackboard_key` | Removes a key declared in this Blackboard (Behavior Trees using it will report errors). |
| `add_blackboard_key` | Adds a key to a Blackboard asset. |

## `agent_toolkit.toolsets.audio.AudioTools` (4)

Audio authoring: Sound Cues from waves, attenuation (3D falloff) assets, per-sound settings     and inspection of SoundWave / SoundCue / MetaSound assets.

| Tool | 説明 |
|---|---|
| `inspect_sound` | Returns sound asset details: class, duration, channels, sample rate, looping, volume, |
| `set_sound_properties` | Changes playback settings of a SoundWave, SoundCue or MetaSound source. Only provided |
| `create_sound_attenuation` | Creates a Sound Attenuation asset (3D distance falloff). |
| `create_sound_cue` | Creates a Sound Cue that plays a Sound Wave, with volume/pitch multipliers, looping and attenuation. |

## `agent_toolkit.toolsets.networking.NetworkingTools` (4)

Multiplayer support: replication summary of an Actor Blueprint (actor net settings,     replicated variables with conditions, RPC events, replicating components), actor     replication settings, RPC mode changes and Play-In-Editor network mode.

| Tool | 説明 |
|---|---|
| `set_pie_network_mode` | Configures Play-In-Editor for multiplayer testing (Editor Preferences > Level Editor > Play). |
| `set_custom_event_replication` | Changes the RPC mode of an existing custom event (requires the native plugin). |
| `set_actor_replication` | Sets actor-level replication defaults of an Actor Blueprint. Only provided arguments change. |
| `inspect_replication` | Returns the replication setup of an Actor Blueprint: actor net settings (replicates, |

## `agent_toolkit.toolsets.performance.PerformanceTools` (4)

Finds performance risks: level statistics (actors, triangles, lights, ticking, Niagara),     Blueprints using Event Tick, high-poly non-Nanite meshes, oversized textures (memory     estimate) and expensive materials. Reports candidates and warnings only.

| Tool | 説明 |
|---|---|
| `find_heavy_assets` | Finds heavy asset candidates: non-Nanite static meshes above a triangle count, textures |
| `find_blueprint_tick_usage` | Lists Blueprints that implement Event Tick (connected or empty) — a common CPU cost. |
| `get_frame_stats` | Returns last-frame runtime timings: FPS, game/render/RHI thread ms, GPU ms, draw calls and |
| `get_level_performance_stats` | Static statistics for the open level with warnings: actor/component counts, triangle |

## `agent_toolkit.toolsets.umg.UMGTools` (6)

UMG helpers: Canvas Panel slot layout (anchor presets, position, size, alignment, z-order,     auto size), widget properties by name, and Widget Animations with keyframes.

| Tool | 説明 |
|---|---|
| `inspect_widget` | Returns a designer widget's class, slot type and Canvas layout (anchors, offsets, alignment). |
| `inspect_widget_animations` | Lists Widget Animations with length, bound widgets and tracks (key counts). |
| `add_widget_animation_keys` | Adds keyframes for a widget property to an animation. |
| `create_widget_animation` | Creates a Widget Animation (available as a variable for Play Animation after compiling). |
| `set_widget_properties` | Sets properties of a designer widget by name, e.g. {"text": "Paused"} (TextBlock), |
| `set_widget_layout` | Sets Canvas Panel slot layout of a widget. Only provided arguments change. |

## `agent_toolkit.toolsets.metasound.MetaSoundTools` (9)

MetaSound authoring: declarative one-call construction of a MetaSound Source, full graph     inspection with node ids, and partial editing of existing MetaSounds (add/remove nodes,     connect/disconnect pins, input defaults, graph inputs).

| Tool | 説明 |
|---|---|
| `remove_metasound_graph_input` | Removes an exposed graph input (its connections are removed too). |
| `add_metasound_graph_input` | Adds an exposed graph input (parameter settable from Blueprints / Audio Component). |
| `set_metasound_input_defaults` | Sets node input defaults, e.g. {"wave.Wave Asset": "/Game/Audio/S_Rain", "wave.Loop": true, |
| `disconnect_metasound_pin` | Removes the connection feeding an input pin ("Node.Input" or "@Out Mono"). |
| `connect_metasound_pins` | Connects pins: "FromNode.Output->ToNode.Input". Nodes are ids or unique names; "@Name" |
| `remove_metasound_nodes` | Removes nodes (and their connections) from a MetaSound. |
| `add_metasound_node` | Adds a node to an existing MetaSound and returns its id and pins. |
| `inspect_metasound` | Returns a MetaSound's graph: nodes (id, name, class, kind, inputs with type/default/link, |
| `build_metasound_source` | Builds a MetaSound Source asset from a declarative graph in one call. |

## `agent_toolkit.toolsets.world.WorldTools` (8)

Terrain and vegetation: create landscapes (flat or from a heightmap) with a material,     inspect landscapes, create foliage types, place foliage instances explicitly or scattered on     the ground, and remove/inspect foliage.

| Tool | 説明 |
|---|---|
| `inspect_foliage` | Counts foliage instances per mesh in the open level. |
| `remove_all_foliage_instances` | Removes every instance of a Foliage Type in the current level. |
| `scatter_foliage` | Scatters foliage instances in a circle, snapping each to the ground with a downward trace |
| `add_foliage_instances` | Adds foliage instances at explicit transforms. |
| `create_foliage_type` | Creates a static-mesh Foliage Type asset. |
| `set_landscape_material` | Assigns a material to a landscape. |
| `inspect_landscapes` | Lists landscapes in the open level with bounds, component count and material. |
| `create_landscape` | Creates a landscape centered at location. Default 8x8 components of 63 quads = 505x505 |

## `agent_toolkit.toolsets.packaging.PackagingTools` (3)

Project packaging via UAT BuildCookRun (build, cook, stage, pak, archive) as background     jobs with stage tracking and parsed errors/warnings, plus cook-only runs.

| Tool | 説明 |
|---|---|
| `cancel_packaging` | Stops a running packaging job. |
| `get_packaging_status` | Returns the state of a packaging job: running/succeeded/failed, current stage, parsed |
| `start_packaging` | Starts packaging (or cooking) in the background and returns a job id. Save all assets |
