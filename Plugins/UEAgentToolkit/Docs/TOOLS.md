# UE Agent Toolkit — Tool 一覧 (自動生成)

MCP では `call_tool(toolset_name="<Toolset>", tool_name="<tool>", arguments={...})` で呼び出します。
全 Tool は JSON 文字列（success / errors[] / warnings[] / target / modified / dirtied_packages[] / details{}）を返します。
合計 **230** Tools / 21 Toolsets

## `agent_toolkit.toolsets.inspector.InspectorTools` (16)

Read-only inspection of editor, level, actors, assets, Blueprints, materials,     AI assets and project settings, returned as compact structured JSON.

| Tool | 説明 |
|---|---|
| `get_editor_state` | Returns a one-call overview of the editor: project, engine version, current |
| `inspect_actor` | Returns a structured description of a level actor: class (and Blueprint), |
| `inspect_component` | Returns details of one component on a level actor, optionally with reflected properties. |
| `inspect_asset` | Returns asset metadata: class, package, on-disk file and size, dirty state, |
| `inspect_blueprint` | Returns the full structure of a Blueprint without reading graphs node-by-node: |
| `inspect_blueprint_graph` | Returns a Blueprint graph as structured data: nodes (id, title, class, position, |
| `inspect_material` | Returns a Material or Material Instance as structured data: domain, blend mode, |
| `inspect_behavior_tree` | Returns a Behavior Tree as a nested structure: composites, tasks, decorators and |
| `inspect_blackboard` | Returns Blackboard keys (name, key type, base class/enum, instance synced) and parent. |
| `inspect_skeleton` | Returns the bone hierarchy (name, parent) and sockets of a Skeleton or Skeletal Mesh. |
| `inspect_level` | Returns a summary of the currently loaded level: actor counts by class, gameplay |
| `inspect_world_settings` | Returns the current level's World Settings: game mode override, kill Z, gravity, |
| `inspect_project_settings` | Returns key project settings: project file/name, engine version, Maps & Modes |
| `get_asset_dependencies` | Returns what an asset depends on (hard + soft), as a tree up to `depth` levels, |
| `get_asset_referencers` | Returns which assets reference an asset (hard + soft), as a tree up to `depth` levels. |
| `get_class_hierarchy` | Returns the parent chain of a class and (optionally) its native and Blueprint subclasses. |

## `agent_toolkit.toolsets.assets.AssetManagementTools` (13)

Asset lifecycle tools with safety rails: search, generic create, rename/move with     reference fix-up, guarded deletion with backups, import/reimport/export, redirector     detection/fix-up and unused asset detection.

| Tool | 説明 |
|---|---|
| `search_assets` | Searches the asset registry by name substring, class and folder. |
| `create_asset` | Creates a new asset of any class that has a "create new" factory (Blueprint, |
| `rename_asset` | Renames an asset in place and fixes up references (a redirector may be left behind; |
| `move_assets` | Moves assets to another folder, fixing up references (redirectors may remain). |
| `delete_assets` | Deletes assets safely. Without confirm=True this is a dry run that returns, for each |
| `save_dirty_assets` | Saves all unsaved (dirty) packages ("Save All"), optionally limited to a folder. |
| `import_files` | Imports files from disk (FBX/OBJ/glTF meshes, PNG/TGA/EXR textures, WAV/OGG audio, |
| `import_fbx` | Imports an FBX with explicit options (legacy FBX importer). |
| `reimport_assets` | Reimports assets from their original source files (meshes, textures, audio, tables). |
| `export_assets` | Exports assets to files on disk using their default exporters (e.g. FBX, PNG, T3D). |
| `find_redirectors` | Lists ObjectRedirector assets (left by renames/moves) and their referencers. |
| `fix_redirectors` | Fixes up redirectors: re-saves every referencer so it points at the real asset, then |
| `find_unused_assets` | Finds assets that no other asset references (candidates for cleanup). Levels and |

## `agent_toolkit.toolsets.level.LevelTools` (18)

Level creation/saving, actor spawning (generic + light/camera/volume/PlayerStart helpers),     duplication/deletion, attachment, World Settings, GameMode and default maps, level     streaming and folder organisation. All actor edits are undoable.

| Tool | 説明 |
|---|---|
| `create_level` | Creates a new level asset and opens it. The current level is closed without saving, |
| `save_current_level` | Saves the currently open level (and its external actors). Untitled levels need save_as_path. |
| `spawn_actor` | Spawns an actor from a class (e.g. "PointLight", "/Game/BP/BP_Enemy") or from an |
| `add_light` | Adds a light actor. |
| `add_camera` | Adds a camera actor (CameraActor or CineCameraActor). |
| `add_volume` | Adds a volume or trigger sized by its half-extent. |
| `add_player_start` | Adds a PlayerStart (player spawn point). |
| `duplicate_actors` | Duplicates actors with a location offset. |
| `delete_actors` | Deletes level actors (undoable with SafetyTools.undo). Without confirm=True only |
| `attach_actor` | Attaches one actor to another (optionally to a socket/bone on the parent's root). |
| `detach_actor` | Detaches an actor from its parent, keeping its world transform. |
| `set_world_settings` | Sets World Settings properties of the current level, e.g. |
| `set_level_game_mode_override` | Sets (or clears) the GameMode override of the current level (World Settings). |
| `set_project_maps_and_modes` | Sets project-wide Maps & Modes (Config/DefaultEngine.ini, backed up first) and applies |
| `add_streaming_level` | Adds an existing level as a streaming sublevel of the current persistent level. |
| `remove_streaming_level` | Removes a streaming sublevel from the current persistent level (the asset is kept). |
| `set_streaming_level_visibility` | Shows or hides a sublevel in the editor. |
| `organize_actors_into_folders` | Organises Outliner folders automatically. |

## `agent_toolkit.toolsets.build_debug.BuildDebugTools` (14)

Compile Blueprints with structured error reports, launch/poll C++ builds, read and     search the Output Log by severity since a mark, extract runtime errors and     asserts/ensures, and trace error text back to assets.

| Tool | 説明 |
|---|---|
| `compile_blueprint` | Compiles one Blueprint and returns status plus every node-level error/warning |
| `compile_blueprints_in_folder` | Compiles every Blueprint under a folder and summarises the results. |
| `get_blueprint_compile_status` | Returns the current compile status and node messages of a Blueprint WITHOUT recompiling. |
| `start_cpp_build` | Starts an Unreal Build Tool build of the project's C++ in the background and returns |
| `get_cpp_build_status` | Returns the state of a C++ build job: running/succeeded/failed, compiler errors and |
| `get_output_log` | Returns parsed Output Log entries (category, severity, message, continuation lines, |
| `get_log_errors` | Returns only Error/Fatal log entries (with referenced assets) since the mark. |
| `get_log_warnings` | Returns only Warning log entries since the mark. |
| `search_log` | Searches the Output Log with a regular expression. |
| `clear_log` | Sets a log mark at the current end of the Output Log. Subsequent log queries with |
| `get_runtime_errors` | Extracts Blueprint/script runtime errors (Accessed None, Blueprint Runtime Error, |
| `get_asserts_and_ensures` | Returns assertion failures, ensures and crashes recorded in the log, each with its |
| `get_message_log` | Returns messages from an editor Message Log listing (BlueprintLog, MapCheck, PIE, |
| `resolve_error_context` | Traces an error message back to project content: extracts asset paths and Blueprint |

## `agent_toolkit.toolsets.validation.ValidationTools` (16)

Checks for compile errors, missing/broken references, invalid assets, null properties,     missing materials, invalid collision, duplicate names, naming/placement rules,     redirectors and unsaved assets, plus explicit auto-fix tools. Run validate_project     for a one-call health report.

| Tool | 説明 |
|---|---|
| `validate_project` | One-call health report over a folder: missing references, broken assets, |
| `validate_assets` | Runs Epic's Data Validation (all registered validators, incl. project-specific ones) on assets. |
| `validate_blueprint` | Validates one Blueprint: compile errors/warnings, missing parent, unused variables, |
| `validate_level` | Validates the open level: missing meshes/materials, invalid physics collision, |
| `find_missing_references` | Lists assets that reference packages which no longer exist (broken dependencies). |
| `find_broken_assets` | Finds broken assets: redirectors pointing nowhere, assets whose class is no longer |
| `find_null_properties` | Lists object/class references that are None on a Blueprint's defaults or on a level |
| `find_missing_materials` | Finds static/skeletal mesh assets with empty material slots (loads meshes in the folder). |
| `find_invalid_collision` | Finds static meshes with no simple collision that are not set to use complex |
| `find_duplicate_asset_names` | Finds assets that share a name across folders (ambiguous for humans and agents). |
| `check_naming_conventions` | Checks asset name prefixes per class (BP_, M_, MI_, T_, SM_, SK_, ABP_, WBP_, NS_, |
| `check_asset_placement` | Checks asset placement: assets in the /Game root, developer-folder assets referenced |
| `find_unsaved_assets` | Lists all packages (assets and levels) with unsaved changes. |
| `fix_naming_conventions` | Renames assets to add the expected class prefix (references are fixed up; run |
| `remove_unused_blueprint_variables` | Removes Blueprint variables that are not referenced by any graph. Without |
| `add_simple_collision_to_meshes` | Adds a simple collision primitive to static meshes that have none. |

## `agent_toolkit.toolsets.safety.SafetyTools` (13)

Transactions (group edits into one undo step), undo/redo, list of modified assets,     file backups and named save points with restore, deletion previews and a journal of     every change made through UE Agent Toolkit tools.

| Tool | 説明 |
|---|---|
| `begin_transaction` | Opens an undo transaction so that all following edits (from any tool) undo as one step. |
| `end_transaction` | Closes the transaction opened by begin_transaction, committing it as one undo step. |
| `undo` | Undoes the most recent editor transactions (also those made by the user). |
| `redo` | Redoes previously undone transactions. |
| `get_undo_state` | Returns the undo stack depth, open toolkit transactions and the latest journal entries. |
| `list_modified_assets` | Lists unsaved (dirty) packages, split into content and levels, flagging never-saved ones. |
| `get_change_journal` | Returns the journal of modifications made through UE Agent Toolkit tools in this |
| `preview_asset_deletion` | Read-only report used before deleting assets: existence, external referencers, |
| `create_backup` | Copies the on-disk files of assets to Saved/AgentToolkit/Backups/<id>. Unsaved |
| `list_backups` | Lists backups and save points, newest first. |
| `restore_backup` | Restores files from a backup over the current assets and reloads them. Without |
| `create_save_point` | Creates a named save point: a backup of the given assets plus (optionally) every |
| `restore_save_point` | Restores the most recent save point with this name (see create_save_point). |

## `agent_toolkit.toolsets.blueprint_authoring.BlueprintAuthoringTools` (21)

Blueprint editing: typed variables with defaults/replication, components (SCS),     functions with typed signatures, custom events (incl. RPC), macros, interfaces, and     name-addressed graph editing (add node, connect/disconnect pins, pin defaults, remove/move     nodes). Pair with InspectorTools.inspect_blueprint(_graph) and BuildDebugTools.compile_blueprint.

| Tool | 説明 |
|---|---|
| `add_blueprint_variable` | Adds a member variable with type, default value, category, editability and replication in one call. |
| `set_blueprint_variable_default` | Sets the default value of a Blueprint variable (or any inherited CDO property). |
| `change_blueprint_variable_type` | Changes the type of a Blueprint variable (connected pins of incompatible type will break; |
| `set_blueprint_variable_flags` | Updates variable metadata. Only provided arguments are changed. |
| `add_blueprint_component` | Adds a component to a Blueprint's component tree (SCS), optionally under a parent |
| `remove_blueprint_component` | Removes a component added in this Blueprint (inherited/native components cannot be removed). |
| `set_blueprint_component_properties` | Sets default properties of a component template in a Blueprint, e.g. |
| `attach_blueprint_component` | Re-parents a scene component within the Blueprint's component tree. |
| `create_blueprint_function` | Creates a function graph with a typed signature. |
| `set_blueprint_function_flags` | Changes function specifiers. Only provided arguments are changed. |
| `add_custom_event` | Adds a Custom Event, optionally with inputs and RPC replication (Server/Client/Multicast). |
| `create_blueprint_macro` | Creates an empty macro graph (requires the native module). Add tunnel pins and |
| `implement_blueprint_interface` | Adds an interface (Blueprint Interface asset or native UInterface) to a Blueprint and |
| `remove_blueprint_interface` | Removes an implemented interface (requires the native module). |
| `add_blueprint_node` | Adds a node using locale-independent identifiers and returns its id and pins. |
| `connect_blueprint_pins` | Connects pins. Each connection is "FromNode.OutPin->ToNode.InPin" using ids from |
| `disconnect_blueprint_pins` | Breaks links of a pin (all links, or only the link to other_pin). |
| `set_blueprint_pin_defaults` | Sets default values of unconnected input pins, e.g. {"K2Node_CallFunction_0.InString": "Hello", |
| `remove_blueprint_nodes` | Deletes nodes (and their links) from a graph. |
| `move_blueprint_node` | Moves a node to a new graph position. |
| `search_blueprint_node_actions` | Searches the node actions available in a graph (the editor's right-click menu). Strings |

## `agent_toolkit.toolsets.blueprint_graph.BlueprintGraphTools` (21)

Bulk Blueprint graph construction from a JSON declaration (build_blueprint_graph), node type     discovery (find_blueprint_node_types / get_blueprint_node_type_pins), graph queries     (find_blueprint_nodes / get_connected_subgraph), auto layout, and variable/function/parent     editing gaps. Locale-independent counterpart of Epic's Graph DSL tools.

| Tool | 説明 |
|---|---|
| `build_blueprint_graph` | Builds a whole graph section in one call: creates nodes, sets pin defaults and connects pins. |
| `find_blueprint_node_categories` | Lists node menu categories available in a graph (localized to the editor language) with counts. |
| `find_blueprint_node_types` | Finds creatable node types. Two result lists: |
| `get_blueprint_node_type_pins` | Shows the pins a node would have, before you build with it. The node is created temporarily and |
| `find_blueprint_nodes` | Finds nodes in a graph by title/id text, node class and error state. |
| `get_connected_subgraph` | Returns the nodes reachable from a node (e.g. everything an event triggers) plus their links. |
| `arrange_blueprint_nodes` | Auto-arranges nodes left to right by their distance from the graph's source nodes. |
| `remove_blueprint_variable` | Removes a member variable. Variable get/set nodes that use it become errors, so check |
| `add_blueprint_function_params` | Adds inputs and/or outputs to an existing function graph. |
| `get_blueprint_parent` | Returns the parent class of a Blueprint. |
| `set_blueprint_parent` | Reparents a Blueprint (e.g. Actor -> Character). Nodes and components that no longer exist in |
| `remove_blueprint_function` | Removes a function graph, macro graph or event dispatcher. Call nodes that used it become |
| `list_component_events` | Lists the bindable events of a component (e.g. OnComponentBeginOverlap), for |
| `add_blueprint_node_pin` | Adds an input pin to a node with a variable pin count (Sequence, Make Array, Select, Switch...). |
| `remove_blueprint_node_pin` | Removes a pin from a node with a variable pin count (e.g. "K2Node_ExecutionSequence_0.then_2"). |
| `retarget_blueprint_node_class` | Points a function-call node at the same function on another class (e.g. a base class |
| `add_event_dispatcher` | Creates an Event Dispatcher with optional parameters. Requires the native module. |
| `add_blueprint_dispatcher_params` | Adds parameters to an existing Event Dispatcher signature. Requires the native module. |
| `remove_blueprint_function_params` | Removes parameters from a function graph or Event Dispatcher signature. Requires the native module. |
| `set_create_event_function` | Gets or sets the function bound by a Create Event node. Requires the native module. |
| `list_compatible_event_functions` | Lists functions/custom events whose parameters match the delegate of a Create Event node |

## `agent_toolkit.toolsets.material_authoring.MaterialAuthoringTools` (6)

Material graph authoring helpers: settings (domain/blend/shading), parameter and texture     nodes in one call, material function calls, batch wiring with "Expr.Output->Expr.Input" /     "Expr.Output->@base_color", and compile with error reporting.

| Tool | 説明 |
|---|---|
| `set_material_settings` | Sets material-level settings. Only provided arguments change. |
| `add_material_parameter` | Adds a Scalar/Vector/Texture/StaticSwitch parameter node with name, default and group, |
| `add_texture_sample` | Adds a Texture Sample (or a Texture parameter when parameter_name is given) for a texture. |
| `add_material_function_call` | Adds a Material Function Call node for a MaterialFunction asset. |
| `connect_material_expressions` | Wires expressions in batch. Formats: "ExprName.Output->ExprName.Input" or |
| `compile_material` | Recompiles a Material and returns compile errors plus shader statistics. Fails with |

## `agent_toolkit.toolsets.input.InputTools` (11)

Enhanced Input authoring: Input Actions, Mapping Contexts, key mappings with triggers and     modifiers, project default mapping contexts, Blueprint wiring (AddMappingContext on     BeginPlay, Enhanced Input Action events) and controller support (gamepad mappings,     force feedback).

| Tool | 説明 |
|---|---|
| `create_input_action` | Creates an Input Action asset. |
| `create_input_mapping_context` | Creates an Input Mapping Context asset. |
| `add_key_mapping` | Maps a key to an Input Action inside a Mapping Context, with optional triggers/modifiers. |
| `update_key_mapping` | Replaces the triggers and/or modifiers of an existing mapping ("none" clears the list). |
| `remove_key_mapping` | Removes the mapping of a key (or all keys when key is omitted) for an action. |
| `list_key_mappings` | Lists all mappings of a Mapping Context with keys, triggers, modifiers and action value types. |
| `register_default_mapping_context` | Adds a Mapping Context to Enhanced Input's project-wide Default Mapping Contexts |
| `add_mapping_context_to_blueprint` | Wires "Event BeginPlay -> Get Player Controller(0) -> Enhanced Input Local Player Subsystem |
| `bind_input_action_event` | Adds an Enhanced Input Action event node (Triggered/Started/Ongoing/Canceled/Completed |
| `add_gamepad_mappings` | Adds controller support to a Mapping Context by mirroring its keyboard/mouse mappings |
| `create_force_feedback_effect` | Creates a Force Feedback Effect (controller rumble) asset with one intensity curve. |

## `agent_toolkit.toolsets.animation.AnimationTools` (13)

Animation authoring: Anim Blueprints with state machines (states playing sequences or     blend spaces, transitions with bool/auto rules), montages with sections and notifies,     1D/2D blend spaces, and structured inspection of animation assets.

| Tool | 説明 |
|---|---|
| `create_anim_blueprint` | Creates an Animation Blueprint targeting a skeleton. |
| `add_anim_state_machine` | Adds a state machine node to the AnimGraph (optionally wired to Output Pose). |
| `add_anim_state` | Adds a state that plays a sequence or blend space. The first state becomes the entry state. |
| `add_anim_transition` | Adds a transition between two states with a rule. |
| `inspect_anim_state_machines` | Returns all state machines of an Anim Blueprint: entry state, states (with animation |
| `create_anim_montage` | Creates an Anim Montage from an animation sequence (default slot). |
| `add_montage_section` | Adds a named section to a montage (e.g. combo steps). Sections are not auto-linked. |
| `add_animation_notify` | Adds an Anim Notify (or Notify State when duration > 0) to a sequence or montage. |
| `create_blend_space` | Creates a blend space (1D when y_name is omitted, 2D otherwise) with axes and samples. |
| `create_ik_rig` | Creates an IK Rig for a skeletal mesh, auto-generating retarget chains (and optionally a |
| `create_ik_retargeter` | Creates an IK Retargeter between two IK Rigs: default ops, fuzzy chain mapping and |
| `retarget_animations` | Duplicates animations (sequences, montages, blend spaces) and retargets them to the target |
| `inspect_animation_asset` | Returns animation asset info: class, skeleton, length, frames, rate scale, notifies |

## `agent_toolkit.toolsets.ai.AITools` (19)

AI authoring: Blackboard keys, Behavior Tree nodes/decorators/services with properties and     blackboard key bindings, AI Perception (sight/hearing) on AI Controllers, pawn AI controller     setup, navigation status, path tests and NavMesh rebuild.

| Tool | 説明 |
|---|---|
| `add_blackboard_key` | Adds a key to a Blackboard asset. |
| `remove_blackboard_key` | Removes a key declared in this Blackboard (Behavior Trees using it will report errors). |
| `set_behavior_tree_blackboard` | Assigns the Blackboard asset used by a Behavior Tree. |
| `add_bt_node` | Adds a composite (Selector/Sequence) or task (MoveTo, Wait, ...) under a parent. Children |
| `add_bt_subtree` | Adds a Run Behavior task that runs another Behavior Tree as a subtree (reusable AI logic, |
| `add_bt_subnode` | Adds a decorator (condition) or service (periodic update) to a composite/task node. |
| `set_bt_node_properties` | Sets properties of a Behavior Tree node instance. FBlackboardKeySelector properties take |
| `remove_bt_node` | Removes a node (and its decorators/services; children become unconnected). |
| `inspect_bt_graph` | Returns the editor graph of a Behavior Tree with node ids (needed by the editing tools), |
| `add_eqs_generator` | Adds an option (generator) to an Environment Query (create the query asset with |
| `add_eqs_test` | Adds a test to an EQS option. |
| `set_eqs_properties` | Sets properties of an EQS generator (test_index -1) or test, by C++ property name. |
| `remove_eqs_item` | Removes an EQS option (test_index -1) or a single test. |
| `inspect_eqs_query` | Returns an Environment Query's options: generator class/description and tests with purpose. |
| `add_ai_perception` | Adds an AIPerceptionComponent with Sight (and optionally Hearing) to an AI Controller |
| `set_pawn_ai_controller` | Sets the AI Controller class and auto-possess mode of a Pawn/Character Blueprint. |
| `get_navigation_status` | Reports navigation setup of the open level: NavMeshBoundsVolumes (with extents), |
| `test_navigation_path` | Tests whether a navigation path exists between two points in the open level. |
| `rebuild_navigation` | Rebuilds navigation data for the open level (editor command RebuildNavigation). |

## `agent_toolkit.toolsets.audio.AudioTools` (4)

Audio authoring: Sound Cues from waves, attenuation (3D falloff) assets, per-sound settings     and inspection of SoundWave / SoundCue / MetaSound assets.

| Tool | 説明 |
|---|---|
| `create_sound_cue` | Creates a Sound Cue that plays a Sound Wave, with volume/pitch multipliers, looping and attenuation. |
| `create_sound_attenuation` | Creates a Sound Attenuation asset (3D distance falloff). |
| `set_sound_properties` | Changes playback settings of a SoundWave, SoundCue or MetaSound source. Only provided |
| `inspect_sound` | Returns sound asset details: class, duration, channels, sample rate, looping, volume, |

## `agent_toolkit.toolsets.networking.NetworkingTools` (4)

Multiplayer support: replication summary of an Actor Blueprint (actor net settings,     replicated variables with conditions, RPC events, replicating components), actor     replication settings, RPC mode changes and Play-In-Editor network mode.

| Tool | 説明 |
|---|---|
| `inspect_replication` | Returns the replication setup of an Actor Blueprint: actor net settings (replicates, |
| `set_actor_replication` | Sets actor-level replication defaults of an Actor Blueprint. Only provided arguments change. |
| `set_custom_event_replication` | Changes the RPC mode of an existing custom event (requires the native plugin). |
| `set_pie_network_mode` | Configures Play-In-Editor for multiplayer testing (Editor Preferences > Level Editor > Play). |

## `agent_toolkit.toolsets.performance.PerformanceTools` (4)

Finds performance risks: level statistics (actors, triangles, lights, ticking, Niagara),     Blueprints using Event Tick, high-poly non-Nanite meshes, oversized textures (memory     estimate) and expensive materials. Reports candidates and warnings only.

| Tool | 説明 |
|---|---|
| `get_level_performance_stats` | Static statistics for the open level with warnings: actor/component counts, triangle |
| `get_frame_stats` | Returns last-frame runtime timings: FPS, game/render/RHI thread ms, GPU ms, draw calls and |
| `find_blueprint_tick_usage` | Lists Blueprints that implement Event Tick (connected or empty) — a common CPU cost. |
| `find_heavy_assets` | Finds heavy asset candidates: non-Nanite static meshes above a triangle count, textures |

## `agent_toolkit.toolsets.umg.UMGTools` (8)

UMG helpers: Canvas Panel slot layout (anchor presets, position, size, alignment, z-order,     auto size), widget properties by name, Widget Animations with keyframes, and gamepad/keyboard     menu navigation (focus rules, focusable widgets, initial focus).

| Tool | 説明 |
|---|---|
| `set_widget_layout` | Sets Canvas Panel slot layout of a widget. Only provided arguments change. |
| `set_widget_properties` | Sets properties of a designer widget by name, e.g. {"text": "Paused"} (TextBlock), |
| `create_widget_animation` | Creates a Widget Animation (available as a variable for Play Animation after compiling). |
| `add_widget_animation_keys` | Adds keyframes for a widget property to an animation. |
| `inspect_widget_animations` | Lists Widget Animations with length, bound widgets and tracks (key counts). |
| `inspect_widget` | Returns a designer widget's class, slot type and Canvas layout (anchors, offsets, alignment). |
| `set_widget_navigation` | Sets gamepad/keyboard focus navigation per direction (escape / stop / wrap / explicit widget). |
| `setup_gamepad_navigation` | Makes a menu controller-operable in one call: focusable widgets, list/grid navigation, initial focus. |

## `agent_toolkit.toolsets.metasound.MetaSoundTools` (9)

MetaSound authoring: declarative one-call construction of a MetaSound Source, full graph     inspection with node ids, and partial editing of existing MetaSounds (add/remove nodes,     connect/disconnect pins, input defaults, graph inputs).

| Tool | 説明 |
|---|---|
| `build_metasound_source` | Builds a MetaSound Source asset from a declarative graph in one call. |
| `inspect_metasound` | Returns a MetaSound's graph: nodes (id, name, class, kind, inputs with type/default/link, |
| `add_metasound_node` | Adds a node to an existing MetaSound and returns its id and pins. |
| `remove_metasound_nodes` | Removes nodes (and their connections) from a MetaSound. |
| `connect_metasound_pins` | Connects pins: "FromNode.Output->ToNode.Input". Nodes are ids or unique names; "@Name" |
| `disconnect_metasound_pin` | Removes the connection feeding an input pin ("Node.Input" or "@Out Mono"). |
| `set_metasound_input_defaults` | Sets node input defaults, e.g. {"wave.Wave Asset": "/Game/Audio/S_Rain", "wave.Loop": true, |
| `add_metasound_graph_input` | Adds an exposed graph input (parameter settable from Blueprints / Audio Component). |
| `remove_metasound_graph_input` | Removes an exposed graph input (its connections are removed too). |

## `agent_toolkit.toolsets.model_import.ModelImportTools` (4)

Blender/DCC model import pipeline: one-call import with checks (import_blender_model), mesh     checks for Blender export problems (inspect_imported_meshes), texture settings by name suffix     (fix_texture_settings), Material Instances from texture sets with slot assignment     (create_materials_from_textures).

| Tool | 説明 |
|---|---|
| `import_blender_model` | Imports a Blender export and prepares it for use in one call: import -> texture settings -> |
| `inspect_imported_meshes` | Checks static/skeletal meshes for typical Blender export problems: 0.01x/100x scale, pivot far |
| `fix_texture_settings` | Sets compression / sRGB / green-channel flip from the texture name suffix: _Normal -> normal |
| `create_materials_from_textures` | Groups textures into sets by name (T_Rock_BaseColor + T_Rock_Normal + T_Rock_ORM -> "Rock"), |

## `agent_toolkit.toolsets.world.WorldTools` (11)

Terrain and vegetation: create landscapes (flat or from a heightmap) with a material,     inspect landscapes, create foliage types, place foliage instances explicitly or scattered on     the ground, and remove/inspect foliage.

| Tool | 説明 |
|---|---|
| `create_landscape` | Creates a landscape centered at location. Default 8x8 components of 63 quads = 505x505 |
| `inspect_landscapes` | Lists landscapes in the open level with bounds, component count and material. |
| `set_landscape_material` | Assigns a material to a landscape. |
| `sculpt_landscape` | Sculpts the landscape with a circular brush (world units): raise/lower hills, flatten a plateau |
| `paint_landscape_layer` | Paints a landscape material layer (grass, rock, sand...) with a circular brush. Creates the |
| `sample_landscape` | Reads the landscape height (world Z) and optionally a paint layer weight (0..1) at points. |
| `create_foliage_type` | Creates a static-mesh Foliage Type asset. |
| `add_foliage_instances` | Adds foliage instances at explicit transforms. |
| `scatter_foliage` | Scatters foliage instances in a circle, snapping each to the ground with a downward trace |
| `remove_all_foliage_instances` | Removes every instance of a Foliage Type in the current level. |
| `inspect_foliage` | Counts foliage instances per mesh in the open level. |

## `agent_toolkit.toolsets.world_partition.WorldPartitionTools` (6)

World Partition: inspect the partition and its Data Layers, create Data Layers (runtime or     editor), assign/remove actors, set initial runtime state / editor visibility / loading, delete     layers, and set per-actor streaming (spatially loaded, runtime grid).

| Tool | 説明 |
|---|---|
| `inspect_world_partition` | Returns whether the level uses World Partition, its Data Layers (type, initial runtime state, |
| `create_data_layer` | Creates a Data Layer asset and its instance in the current World Partition level. |
| `assign_actors_to_data_layer` | Adds level actors to a Data Layer (or removes them with remove=true). |
| `set_data_layer_state` | Changes Data Layer states. Only provided arguments change. |
| `delete_data_layer` | Deletes a Data Layer instance from the level (its actors stay, unassigned). The Data Layer asset |
| `set_actor_streaming` | Sets World Partition streaming of actors: spatially loaded (streams by distance) or always |

## `agent_toolkit.toolsets.packaging.PackagingTools` (3)

Project packaging via UAT BuildCookRun (build, cook, stage, pak, archive) as background     jobs with stage tracking and parsed errors/warnings, plus cook-only runs.

| Tool | 説明 |
|---|---|
| `start_packaging` | Starts packaging (or cooking) in the background and returns a job id. Save all assets |
| `get_packaging_status` | Returns the state of a packaging job: running/succeeded/failed, current stage, parsed |
| `cancel_packaging` | Stops a running packaging job. |
