"""ModelImportTools: import pipeline for models made in Blender (or other DCC tools).

Catches the usual Blender -> Unreal problems after import (0.01x / 100x scale, pivot far from
the mesh, Y-up characters, an extra "Armature" root bone, mixamo prefixes, default material slot
names, missing collision / lightmap UVs), fixes texture settings by file-name suffix (OpenGL normal
maps get their green channel flipped), builds Material Instances from texture sets on a shared PBR
master material and assigns them to mesh slots, and applies class prefixes (SM_, SK_, T_, MI_).
"""

from __future__ import annotations

import os
import re

import unreal

from agent_toolkit.core import deps, editor, mesh_quality, resolve
from agent_toolkit.core.errors import Code, ToolError
from agent_toolkit.core.serialize import split_csv
from agent_toolkit.core.tooling import agent_tool, ctx
from agent_toolkit.toolsets.validation import DEFAULT_PREFIXES

MEL = unreal.MaterialEditingLibrary
MASTER_FOLDER = '/Game/AgentToolkit/Materials'
MASTER_NAME = 'M_AgentToolkit_PBR'
_WHITE = '/Engine/EngineResources/WhiteSquareTexture'
_FLAT_NORMAL = '/Engine/EngineMaterials/DefaultNormal'

# Texture roles by file-name suffix (longest first so "_basecolor" wins over "_color").
_ROLE_SUFFIXES = {
    'basecolor': ['basecolor', 'base_color', 'albedo', 'diffuse', 'color', 'col', 'bc', 'd'],
    'normal': ['normalgl', 'normal_gl', 'normaldx', 'normal_dx', 'normal', 'nrm', 'nor', 'n'],
    'orm': ['occlusionroughnessmetallic', 'orm', 'arm'],
    'roughness': ['roughness', 'rough', 'rgh', 'r'],
    'metallic': ['metallic', 'metalness', 'metal', 'm'],
    'ao': ['ambientocclusion', 'ambient_occlusion', 'occlusion', 'ao'],
    'emissive': ['emissive', 'emission', 'emit', 'e'],
}
_SUFFIX_TABLE = sorted(((s, role) for role, ss in _ROLE_SUFFIXES.items() for s in ss), key=lambda x: -len(x[0]))
_DEFAULT_SLOT = re.compile(r'^(material|mat|default|none)([._ ]?\d+)?$', re.IGNORECASE)
_MESH_EXTS = ('.fbx', '.gltf', '.glb', '.obj')


# ------------------------------------------------------------------------------ helpers
def _norm(text: str) -> str:
    text = text.lower()
    for prefix in ('mi_', 'm_', 'mat_', 'sm_', 'sk_', 't_'):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return re.sub(r'[^a-z0-9]', '', text)


def classify_texture(name: str) -> tuple[str | None, str]:
    """('normal', 'Rock') for 'T_Rock_Normal'; (None, name) when no known suffix."""
    low = name.lower()
    for suffix, role in _SUFFIX_TABLE:
        for sep in ('_', '-', '.', ' '):
            if low.endswith(sep + suffix):
                base = name[:-(len(suffix) + 1)]
                if base.lower().startswith('t_'):
                    base = base[2:]
                return role, base
    return None, name


def _collect(asset_paths: str | None, folder: str | None, classes: tuple) -> list:
    """Assets from a CSV list of paths and/or a folder, filtered by class."""
    out = [resolve.load_asset(p) for p in split_csv(asset_paths)]
    if folder:
        for data in deps.assets_in_path(folder, True):
            obj = unreal.load_asset(str(data.package_name))
            if obj is not None:
                out.append(obj)
    seen, result = set(), []
    for obj in out:
        if isinstance(obj, classes) and obj.get_path_name() not in seen:
            seen.add(obj.get_path_name())
            result.append(obj)
    return result


def _package(obj) -> str:
    return obj.get_outermost().get_name()


def _issue(code: str, severity: str, message: str, fix: str = '') -> dict:
    out = {'code': code, 'severity': severity, 'message': message}
    if fix:
        out['fix'] = fix
    return out


def _slots(mesh) -> list[tuple[str, object]]:
    if isinstance(mesh, unreal.StaticMesh):
        return [(str(m.get_editor_property('material_slot_name')), m.get_editor_property('material_interface'))
                for m in mesh.get_editor_property('static_materials') or []]
    return [(str(m.get_editor_property('material_slot_name')), m.get_editor_property('material_interface'))
            for m in mesh.get_editor_property('materials') or []]


def _set_slot(mesh, index: int, material) -> None:
    if isinstance(mesh, unreal.StaticMesh):
        mesh.set_material(index, material)
        return
    mats = list(mesh.get_editor_property('materials'))
    mats[index].set_editor_property('material_interface', material)
    mesh.set_editor_property('materials', mats)


def _extent(mesh) -> tuple[list[float], list[float]] | None:
    """(min, max) corners of the mesh bounds in cm."""
    try:
        if isinstance(mesh, unreal.StaticMesh):
            box = mesh.get_bounding_box()
            return [box.min.x, box.min.y, box.min.z], [box.max.x, box.max.y, box.max.z]
        bounds = mesh.get_bounds()
        o, e = bounds.origin, bounds.box_extent
        return [o.x - e.x, o.y - e.y, o.z - e.z], [o.x + e.x, o.y + e.y, o.z + e.z]
    except Exception:  # pylint: disable=broad-exception-caught
        return None


# ------------------------------------------------------------------------------ inspection
def inspect_mesh(mesh) -> dict:
    issues = []
    info: dict = {'asset': _package(mesh), 'class': mesh.get_class().get_name()}
    corners = _extent(mesh)
    if corners:
        lo, hi = corners
        size = [round(hi[i] - lo[i], 2) for i in range(3)]
        info['size_cm'] = size
        biggest = max(size)
        if 0 < biggest < 1.0:
            issues.append(_issue('SCALE_TOO_SMALL', 'error', f'Largest dimension is {biggest} cm (0.01x scale?).',
                                 'Blender: Apply Scale and export with "Apply Unit Scale" on, or reimport with '
                                 'import_uniform_scale=100.'))
        elif biggest > 100000:
            issues.append(_issue('SCALE_TOO_LARGE', 'error', f'Largest dimension is {biggest} cm (100x scale?).',
                                 'Blender: Unit Scale 1.0 / Apply Scale, or reimport with import_uniform_scale=0.01.'))
        center = [(lo[i] + hi[i]) / 2 for i in range(3)]
        half = max(biggest / 2, 0.01)
        offset = max(abs(center[0]), abs(center[1]))
        if offset > half * 1.5:
            issues.append(_issue('PIVOT_OUTSIDE_MESH', 'warning',
                                 f'Pivot is {round(offset, 1)} cm away from the mesh center horizontally.',
                                 'Blender: Object > Set Origin, then Apply Location before exporting.'))
        if lo[2] > 1.0 or hi[2] < -1.0:
            issues.append(_issue('PIVOT_NOT_ON_MESH_HEIGHT', 'info',
                                 f'Mesh spans Z {round(lo[2], 1)}..{round(hi[2], 1)} cm; pivot is not inside it.',
                                 'Place the origin at the base of the object for easier placement.'))
    slots = _slots(mesh)
    info['material_slots'] = [s for s, _ in slots]
    default_names = [s for s, _ in slots if _DEFAULT_SLOT.match(s)]
    if default_names:
        issues.append(_issue('DEFAULT_SLOT_NAMES', 'info', f'Blender default material names: {default_names}',
                             'Name the materials in Blender so slots are recognizable (and match texture sets).'))
    empty = [s for s, m in slots if m is None or 'WorldGridMaterial' in m.get_path_name()
             or 'DefaultMaterial' in m.get_path_name()]
    if empty:
        issues.append(_issue('MATERIAL_MISSING', 'warning', f'Slots without a real material: {empty}',
                             'Run create_materials_from_textures with assign_to_meshes.'))
    if isinstance(mesh, unreal.StaticMesh):
        _inspect_static(mesh, info, issues)
    else:
        _inspect_skeletal(mesh, info, issues, corners)
    info['issues'] = issues
    return info


def _inspect_static(mesh, info: dict, issues: list) -> None:
    errors = mesh_quality.basis_errors(mesh)
    info['tangent_basis_errors'] = errors
    for error in errors:
        issues.append(_issue('INVALID_MESH_BASIS', 'error', error,
                             'Run repair_mesh_tangent_basis; fix degenerate faces and UVs in the source if it fails.'))
    lib = unreal.EditorStaticMeshLibrary
    try:
        info['triangles'] = int(mesh.get_num_triangles(0))
    except Exception:  # pylint: disable=broad-exception-caught
        info['triangles'] = None
    try:
        info['lods'] = int(lib.get_lod_count(mesh))
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    try:
        nanite = bool(mesh.get_editor_property('nanite_settings').get_editor_property('enabled'))
        info['nanite'] = nanite
        if not nanite and (info.get('triangles') or 0) > 50000:
            issues.append(_issue('NANITE_CANDIDATE', 'info', f"{info['triangles']} triangles without Nanite.",
                                 'Enable Nanite (PerformanceTools / mesh settings) for dense static meshes.'))
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    try:
        simple = int(lib.get_simple_collision_count(mesh))
        info['simple_collision'] = simple
        complex_as_simple = False
        try:
            flag = mesh.get_editor_property('body_setup').get_editor_property('collision_trace_flag')
            complex_as_simple = 'COMPLEX_AS_SIMPLE' in str(flag).upper()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        if simple == 0 and not complex_as_simple:
            issues.append(_issue('NO_COLLISION', 'warning', 'No simple collision.',
                                 'Export UCX_<MeshName> collision from Blender or run add_simple_collision_to_meshes.'))
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    try:
        uv_channels = int(lib.get_num_uv_channels(mesh, 0))
        lightmap_index = int(mesh.get_editor_property('light_map_coordinate_index'))
        info['uv_channels'] = uv_channels
        if lightmap_index >= uv_channels:
            issues.append(_issue('NO_LIGHTMAP_UV', 'info', f'Lightmap UV index {lightmap_index} does not exist '
                                 f'({uv_channels} UV channels).', 'Enable "Generate Lightmap UVs" in the build '
                                 'settings (only needed for baked lighting).'))
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def _inspect_skeletal(mesh, info: dict, issues: list, corners) -> None:
    skeleton = mesh.get_editor_property('skeleton')
    names = [str(n) for n in skeleton.get_reference_pose().get_bone_names()] if skeleton else []
    info['bone_count'] = len(names)
    info['root_bone'] = names[0] if names else None
    if names and 'armature' in names[0].lower():
        issues.append(_issue('ARMATURE_ROOT_BONE', 'warning',
                             f'Root bone is "{names[0]}" (the Blender Armature object became a bone).',
                             'Rename the Armature object to "root" in Blender (or disable "Add Leaf Bones" and '
                             'export with Armature FBXNode Type = Null), then reimport.'))
    mixamo = [n for n in names if n.lower().startswith('mixamorig')]
    if mixamo:
        issues.append(_issue('MIXAMO_PREFIX', 'info', f'{len(mixamo)} bones use the "mixamorig" prefix.',
                             'Retarget with create_ik_rig / create_ik_retargeter; chains are matched fuzzily.'))
    dotted = [n for n in names if re.search(r'\.(l|r|left|right)$', n, re.IGNORECASE)]
    if dotted:
        issues.append(_issue('BLENDER_SIDE_SUFFIX', 'info', f'{len(dotted)} bones use ".L/.R" side suffixes '
                             f'(e.g. {dotted[0]}).', 'Rename to _l/_r for UE-style names if auto IK chains fail.'))
    leaf = [n for n in names if n.lower().endswith('_end') or n.lower().endswith('.end')]
    if leaf:
        issues.append(_issue('LEAF_BONES', 'info', f'{len(leaf)} "_end" leaf bones (Blender "Add Leaf Bones").',
                             'Disable "Add Leaf Bones" in the Blender FBX exporter.'))
    try:
        info['morph_targets'] = [str(n) for n in mesh.get_all_morph_target_names() or []]
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    if corners:
        lo, hi = corners
        dx, dy, dz = (hi[i] - lo[i] for i in range(3))
        if dy > dz * 2 and dy > dx:
            issues.append(_issue('LYING_DOWN', 'warning', f'Mesh is {round(dy)} cm deep but only {round(dz)} cm tall '
                                 '(Y-up export?).', 'Blender FBX export: Forward -Y / Up Z (default), Apply '
                                 'Transform; or rotate the Armature 90 deg on X and apply.'))


# ------------------------------------------------------------------------------ textures
def fix_texture(tex, normal_convention: str) -> dict | None:
    role, base = classify_texture(tex.get_name())
    if role is None:
        return None
    TCS = unreal.TextureCompressionSettings
    changes = {}

    def set_prop(name, value):
        try:
            if tex.get_editor_property(name) != value:
                tex.set_editor_property(name, value)
                changes[name] = str(value)
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    if role == 'normal':
        set_prop('compression_settings', TCS.TC_NORMALMAP)
        set_prop('srgb', False)
        low = tex.get_name().lower()
        is_gl = 'gl' in low.rsplit('_', 1)[-1] or (normal_convention.lower() == 'opengl' and 'dx' not in low)
        set_prop('flip_green_channel', is_gl)
    elif role in ('orm', 'roughness', 'metallic', 'ao'):
        set_prop('compression_settings', TCS.TC_MASKS)
        set_prop('srgb', False)
    else:
        set_prop('compression_settings', TCS.TC_DEFAULT)
        set_prop('srgb', True)
    return {'texture': _package(tex), 'role': role, 'set': base, 'changed': changes}


# ------------------------------------------------------------------------------ materials
def _default_mask_texture():
    path = f'{MASTER_FOLDER}/T_AgentToolkit_DefaultMask'
    if resolve.asset_exists(path):
        return unreal.load_asset(path)
    tex = unreal.EditorAssetLibrary.duplicate_asset(_WHITE, path)
    if tex is None:
        raise ToolError(Code.UE_OPERATION_FAILED, f'Could not create {path} from {_WHITE}')
    tex.set_editor_property('srgb', False)
    tex.set_editor_property('compression_settings', unreal.TextureCompressionSettings.TC_MASKS)
    unreal.EditorAssetLibrary.save_loaded_asset(tex)
    return tex


def ensure_master_material():
    """Shared PBR master: BaseColor/Normal/Emissive maps, UseORM switch between an ORM map and
    separate Roughness/Metallic/AO maps, and scalar multipliers for missing maps."""
    path = f'{MASTER_FOLDER}/{MASTER_NAME}'
    if resolve.asset_exists(path):
        return unreal.load_asset(path)
    mask = _default_mask_texture()
    mat = editor.asset_tools().create_asset(MASTER_NAME, MASTER_FOLDER, unreal.Material, unreal.MaterialFactoryNew())
    if mat is None:
        raise ToolError(Code.UE_OPERATION_FAILED, f'Could not create {path}')
    ST = unreal.MaterialSamplerType
    MP = unreal.MaterialProperty

    def tex_param(name, texture, sampler, x, y):
        e = MEL.create_material_expression(mat, unreal.MaterialExpressionTextureSampleParameter2D, x, y)
        e.set_editor_property('parameter_name', unreal.Name(name))
        e.set_editor_property('texture', texture)
        e.set_editor_property('sampler_type', sampler)
        return e

    def scalar(name, value, x, y):
        e = MEL.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, x, y)
        e.set_editor_property('parameter_name', unreal.Name(name))
        e.set_editor_property('default_value', value)
        return e

    def multiply(a, a_out, b, x, y):
        e = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, x, y)
        MEL.connect_material_expressions(a, a_out, e, 'A')
        MEL.connect_material_expressions(b, '', e, 'B')
        return e

    def switch(true_expr, false_expr, x, y):
        e = MEL.create_material_expression(mat, unreal.MaterialExpressionStaticSwitchParameter, x, y)
        e.set_editor_property('parameter_name', unreal.Name('UseORM'))
        MEL.connect_material_expressions(true_expr, '', e, 'True')
        MEL.connect_material_expressions(false_expr, '', e, 'False')
        return e

    white = unreal.load_asset(_WHITE)
    base = tex_param('BaseColorMap', white, ST.SAMPLERTYPE_COLOR, -900, -300)
    MEL.connect_material_property(base, 'RGB', MP.MP_BASE_COLOR)
    normal = tex_param('NormalMap', unreal.load_asset(_FLAT_NORMAL), ST.SAMPLERTYPE_NORMAL, -900, 0)
    MEL.connect_material_property(normal, 'RGB', MP.MP_NORMAL)
    orm = tex_param('ORMMap', mask, ST.SAMPLERTYPE_MASKS, -1300, 300)
    rough = tex_param('RoughnessMap', mask, ST.SAMPLERTYPE_MASKS, -1300, 600)
    metal = tex_param('MetallicMap', mask, ST.SAMPLERTYPE_MASKS, -1300, 900)
    ao = tex_param('AOMap', mask, ST.SAMPLERTYPE_MASKS, -1300, 1200)
    r_scale = scalar('RoughnessScale', 1.0, -1000, 650)
    m_scale = scalar('MetallicScale', 1.0, -1000, 950)
    a_scale = scalar('AOScale', 1.0, -1000, 1250)
    for orm_out, sep, sep_scale, prop, y in (('G', rough, r_scale, MP.MP_ROUGHNESS, 400),
                                            ('B', metal, m_scale, MP.MP_METALLIC, 700),
                                            ('R', ao, a_scale, MP.MP_AMBIENT_OCCLUSION, 1000)):
        from_orm = multiply(orm, orm_out, sep_scale, -700, y)
        from_sep = multiply(sep, 'R', sep_scale, -700, y + 120)
        MEL.connect_material_property(switch(from_orm, from_sep, -450, y), '', prop)
    emissive = tex_param('EmissiveMap', white, ST.SAMPLERTYPE_COLOR, -1300, 1500)
    emissive_strength = scalar('EmissiveStrength', 0.0, -1000, 1550)
    MEL.connect_material_property(multiply(emissive, 'RGB', emissive_strength, -700, 1500), '', MP.MP_EMISSIVE_COLOR)
    MEL.recompile_material(mat)
    unreal.EditorAssetLibrary.save_loaded_asset(mat)
    return mat


def group_texture_sets(textures: list) -> dict[str, dict[str, object]]:
    sets: dict[str, dict[str, object]] = {}
    for tex in textures:
        role, base = classify_texture(tex.get_name())
        if role:
            sets.setdefault(base, {})[role] = tex
    return sets


def create_instance(base: str, maps: dict, folder: str, parent):
    name = f'MI_{base}'
    path = f'{folder}/{name}'
    mi = unreal.load_asset(path) if resolve.asset_exists(path) else editor.asset_tools().create_asset(
        name, folder, unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew())
    if mi is None:
        raise ToolError(Code.UE_OPERATION_FAILED, f'Could not create {path}')
    MEL.set_material_instance_parent(mi, parent)
    params = {'basecolor': 'BaseColorMap', 'normal': 'NormalMap', 'orm': 'ORMMap', 'roughness': 'RoughnessMap',
              'metallic': 'MetallicMap', 'ao': 'AOMap', 'emissive': 'EmissiveMap'}
    for role, tex in maps.items():
        MEL.set_material_instance_texture_parameter_value(mi, params[role], tex)
    use_orm = 'orm' in maps
    MEL.set_material_instance_static_switch_parameter_value(mi, 'UseORM', use_orm)
    if not use_orm:
        # Missing maps sample the white default texture; scale them to sensible constants.
        if 'roughness' not in maps:
            MEL.set_material_instance_scalar_parameter_value(mi, 'RoughnessScale', 0.5)
        if 'metallic' not in maps:
            MEL.set_material_instance_scalar_parameter_value(mi, 'MetallicScale', 0.0)
    if 'emissive' in maps:
        MEL.set_material_instance_scalar_parameter_value(mi, 'EmissiveStrength', 1.0)
    MEL.update_material_instance(mi)
    return mi


def assign_instances(meshes: list, instances: dict[str, object]) -> list[dict]:
    """Slot name == texture set name (normalized); a single-slot mesh also matches by mesh name,
    and a single texture set is used for every unmatched slot."""
    by_norm = {_norm(k): v for k, v in instances.items()}
    only = next(iter(instances.values())) if len(instances) == 1 else None
    assigned = []
    for mesh in meshes:
        slots = _slots(mesh)
        for index, (slot, _) in enumerate(slots):
            mi = by_norm.get(_norm(slot))
            if mi is None and len(slots) == 1:
                mi = by_norm.get(_norm(mesh.get_name()))
            mi = mi or only
            if mi is not None:
                _set_slot(mesh, index, mi)
                assigned.append({'mesh': _package(mesh), 'slot': slot, 'material': _package(mi)})
    return assigned


def apply_prefixes(assets: list) -> list[dict]:
    renames, done = [], []
    for obj in assets:
        prefix = DEFAULT_PREFIXES.get(obj.get_class().get_name())
        name = obj.get_name()
        if not prefix or name.startswith(prefix):
            continue
        folder = _package(obj).rsplit('/', 1)[0]
        if resolve.asset_exists(f'{folder}/{prefix}{name}'):
            ctx().warn(f'{prefix}{name} already exists; {name} was not renamed', 'NAME_CONFLICT')
            continue
        renames.append(unreal.AssetRenameData(asset=obj, new_package_path=folder, new_name=prefix + name))
        done.append({'from': _package(obj), 'to': f'{folder}/{prefix}{name}'})
    if renames and not editor.asset_tools().rename_assets(renames):
        raise ToolError(Code.UE_OPERATION_FAILED, 'Renaming failed (see Output Log)')
    return done


def _import(source_file: str, folder: str, mesh_type: str, uniform_scale: float, replace: bool,
            import_fbx_materials: bool) -> list[str]:
    task = unreal.AssetImportTask()
    task.filename = source_file
    task.destination_path = folder
    task.replace_existing = replace
    task.automated = True
    task.save = False
    if source_file.lower().endswith('.fbx'):
        ui = unreal.FbxImportUI()
        kind = mesh_type.lower()
        ui.import_mesh = True
        ui.import_materials = import_fbx_materials
        ui.import_textures = True
        if kind == 'auto':
            ui.automated_import_should_detect_type = True
        else:
            ui.automated_import_should_detect_type = False
            ui.import_as_skeletal = kind == 'skeletal'
            ui.import_animations = kind == 'skeletal'
            ui.mesh_type_to_import = (unreal.FBXImportType.FBXIT_SKELETAL_MESH if kind == 'skeletal'
                                      else unreal.FBXImportType.FBXIT_STATIC_MESH)
        for data_name in ('static_mesh_import_data', 'skeletal_mesh_import_data'):
            try:
                ui.get_editor_property(data_name).set_editor_property('import_uniform_scale', float(uniform_scale))
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        task.options = ui
        task.factory = unreal.FbxFactory()
    elif uniform_scale != 1.0:
        ctx().warn('import_uniform_scale is only applied to FBX; scale glTF/OBJ in Blender.', 'SCALE_IGNORED')
    editor.asset_tools().import_asset_tasks([task])
    return [str(p) for p in task.get_editor_property('imported_object_paths')]


@unreal.uclass()
class ModelImportTools(unreal.ToolsetDefinition):
    """Blender/DCC model import pipeline: one-call import with checks (import_blender_model), mesh
    checks for Blender export problems (inspect_imported_meshes), texture settings by name suffix
    (fix_texture_settings), Material Instances from texture sets with slot assignment
    (create_materials_from_textures)."""

    @agent_tool(mutates=True, transaction=False)
    def import_blender_model(source_file: str, destination_folder: str, mesh_type: str = 'auto',
                             texture_files: str | None = None, import_uniform_scale: float = 1.0,
                             normal_map_convention: str = 'opengl', create_materials: bool = True,
                             apply_naming: bool = True, add_missing_collision: bool = False,
                             replace_existing: bool = False) -> dict:
        """Imports a Blender export and prepares it for use in one call: import -> texture settings ->
        Material Instances from texture sets assigned to the mesh slots -> class prefixes -> checks
        (scale, pivot, axis, armature root bone, collision, ...). Returns everything that still needs a fix.

        Args:
            source_file: Absolute path of an .fbx, .gltf, .glb or .obj file.
            destination_folder: Content folder, e.g. /Game/Props/Chair.
            mesh_type: auto, static or skeletal (FBX only; glTF/OBJ are detected by the importer).
            texture_files: Optional comma-separated absolute texture paths (Blender often exports them separately).
            import_uniform_scale: FBX import scale (100 fixes a 0.01x export, 0.01 a 100x export).
            normal_map_convention: opengl (Blender default, flips green) or directx.
            create_materials: Build Material Instances from the textures (FBX materials are then skipped).
            apply_naming: Add SM_/SK_/T_/MI_ prefixes to the imported assets.
            add_missing_collision: Add box collision to static meshes without collision.
            replace_existing: Overwrite existing assets.
        """
        if not os.path.isfile(source_file) or not source_file.lower().endswith(_MESH_EXTS):
            raise ToolError(Code.INVALID_ARGUMENT, f'Not an existing {"/".join(_MESH_EXTS)} file: {source_file}')
        if mesh_type.lower() not in ('auto', 'static', 'skeletal'):
            raise ToolError(Code.INVALID_ARGUMENT, 'mesh_type must be auto, static or skeletal')
        textures_in = split_csv(texture_files)
        missing = [f for f in textures_in if not os.path.isfile(f)]
        if missing:
            raise ToolError(Code.INVALID_ARGUMENT, f'Texture files not found: {missing}')
        ctx().set_target(destination_folder)
        paths = _import(source_file, destination_folder, mesh_type, import_uniform_scale, replace_existing,
                        not create_materials)
        if not paths:
            raise ToolError(Code.UE_OPERATION_FAILED, f'Import produced no assets: {source_file}',
                            likely_causes=['Wrong mesh_type, corrupt file, or existing assets with the same name '
                                           '(replace_existing=False).'])
        for tex_file in textures_in:
            t = unreal.AssetImportTask()
            t.filename, t.destination_path = tex_file, destination_folder
            t.replace_existing, t.automated, t.save = replace_existing, True, False
            editor.asset_tools().import_asset_tasks([t])
            paths += [str(p) for p in t.get_editor_property('imported_object_paths')]
        assets = [a for a in (unreal.load_asset(p) for p in paths) if a is not None]
        meshes = [a for a in assets if isinstance(a, (unreal.StaticMesh, unreal.SkeletalMesh))]
        ctx().mark_modified(bool(assets))
        basis_checks = mesh_quality.check_meshes(meshes, repair=True)
        textures = [a for a in assets if isinstance(a, unreal.Texture2D)]
        result: dict = {'imported': [_package(a) for a in assets]}
        result['mesh_basis_checks'] = basis_checks
        result['textures'] = [r for r in (fix_texture(t, normal_map_convention) for t in textures) if r]
        unknown = [t.get_name() for t in textures if classify_texture(t.get_name())[0] is None]
        if unknown:
            ctx().warn(f'Textures without a known suffix (_BaseColor, _Normal, _ORM, ...): {unknown}', 'TEXTURE_ROLE')
        if create_materials and textures:
            sets = group_texture_sets(textures)
            if sets:
                parent = ensure_master_material()
                instances = {base: create_instance(base, maps, destination_folder, parent) for base, maps in sets.items()}
                assets += list(instances.values())
                result['materials'] = {base: _package(mi) for base, mi in instances.items()}
                result['assigned'] = assign_instances(meshes, instances)
        if add_missing_collision:
            added = []
            for mesh in meshes:
                if isinstance(mesh, unreal.StaticMesh) and unreal.EditorStaticMeshLibrary.get_simple_collision_count(mesh) == 0:
                    unreal.EditorStaticMeshLibrary.add_simple_collisions(mesh, unreal.ScriptCollisionShapeType.BOX)
                    added.append(_package(mesh))
            result['collision_added'] = added
        if apply_naming:
            result['renamed'] = apply_prefixes(assets)
            for report, mesh in zip(basis_checks, (m for m in meshes if isinstance(m, unreal.StaticMesh))):
                report['asset'] = mesh.get_path_name()
        result['checks'] = [inspect_mesh(m) for m in meshes]  # after renaming so paths are current
        result['issue_count'] = sum(len(c['issues']) for c in result['checks'])
        return result

    @agent_tool()
    def inspect_imported_meshes(asset_paths: str | None = None, folder: str | None = None) -> dict:
        """Checks static/skeletal meshes for typical Blender export problems: 0.01x/100x scale, pivot far
        from the mesh, lying-down (Y-up) characters, "Armature" root bone, mixamo prefixes, .L/.R and leaf
        bones, default material slot names, missing materials/collision/lightmap UVs, Nanite candidates.
        Each issue carries a suggested fix.

        Args:
            asset_paths: Comma-separated mesh asset paths.
            folder: Content folder to scan (recursive).
        """
        meshes = _collect(asset_paths, folder, (unreal.StaticMesh, unreal.SkeletalMesh))
        if not meshes:
            raise ToolError(Code.OBJECT_NOT_FOUND, 'No static or skeletal meshes found',
                            likely_causes=['Pass asset_paths and/or folder.'])
        ctx().set_target(folder or _package(meshes[0]))
        checks = [inspect_mesh(m) for m in meshes]
        mesh_quality.check_meshes(meshes)
        return {'meshes': checks, 'issue_count': sum(len(c['issues']) for c in checks)}

    @agent_tool(mutates=True)
    def repair_mesh_tangent_basis(asset_paths: str) -> dict:
        """Repairs invalid static mesh bases by recomputing normals/tangents and removing
        degenerates on every LOD. Leaves valid artist-authored bases unchanged. Returns failure
        if any basis remains invalid; fix source geometry/UVs before completing or saving.

        Args:
            asset_paths: Comma-separated static mesh asset paths.
        """
        paths = split_csv(asset_paths)
        if not paths:
            raise ToolError(Code.INVALID_ARGUMENT, 'asset_paths must contain a static mesh path')
        meshes = [resolve.load_asset(path, unreal.StaticMesh) for path in paths]
        ctx().set_target(asset_paths)
        reports = mesh_quality.check_meshes(meshes, repair=True)
        ctx().mark_modified(any(report['repaired'] for report in reports))
        return {'mesh_basis_checks': reports}

    @agent_tool(mutates=True)
    def fix_texture_settings(asset_paths: str | None = None, folder: str | None = None,
                             normal_map_convention: str = 'opengl') -> dict:
        """Sets compression / sRGB / green-channel flip from the texture name suffix: _Normal -> normal
        map (flip green for OpenGL/Blender), _ORM/_Roughness/_Metallic/_AO -> masks without sRGB,
        _BaseColor/_Emissive -> sRGB color. Textures without a known suffix are listed, not changed.

        Args:
            asset_paths: Comma-separated texture asset paths.
            folder: Content folder to scan (recursive).
            normal_map_convention: opengl (Blender, Substance "OpenGL") or directx. Names containing
                "GL"/"DX" (e.g. _NormalGL) override this.
        """
        if normal_map_convention.lower() not in ('opengl', 'directx'):
            raise ToolError(Code.INVALID_ARGUMENT, 'normal_map_convention must be opengl or directx')
        textures = _collect(asset_paths, folder, (unreal.Texture2D,))
        if not textures:
            raise ToolError(Code.OBJECT_NOT_FOUND, 'No textures found')
        ctx().set_target(folder or _package(textures[0]))
        fixed = [r for r in (fix_texture(t, normal_map_convention) for t in textures) if r]
        unknown = [_package(t) for t in textures if classify_texture(t.get_name())[0] is None]
        return {'textures': fixed, 'changed': sum(1 for f in fixed if f['changed']), 'unrecognized': unknown}

    @agent_tool(mutates=True)
    def create_materials_from_textures(texture_folder: str | None = None, texture_paths: str | None = None,
                                       destination_folder: str | None = None, assign_to_meshes: str | None = None,
                                       parent_material: str | None = None) -> dict:
        """Groups textures into sets by name (T_Rock_BaseColor + T_Rock_Normal + T_Rock_ORM -> "Rock"),
        creates one Material Instance per set (MI_Rock) on a shared PBR master material and optionally
        assigns them to mesh slots whose name matches the set name.

        Args:
            texture_folder: Folder with the textures.
            texture_paths: Comma-separated texture asset paths.
            destination_folder: Folder for the instances (default: the first texture's folder).
            assign_to_meshes: Comma-separated mesh asset paths, or a folder, to assign the instances to.
            parent_material: Custom parent material with the same parameter names (default:
                /Game/AgentToolkit/Materials/M_AgentToolkit_PBR, created on first use).
        """
        textures = _collect(texture_paths, texture_folder, (unreal.Texture2D,))
        sets = group_texture_sets(textures)
        if not sets:
            raise ToolError(Code.OBJECT_NOT_FOUND, 'No texture sets found',
                            likely_causes=['Texture names need a role suffix: _BaseColor, _Normal, _ORM, _Roughness, '
                                           '_Metallic, _AO, _Emissive.'])
        folder = destination_folder or _package(textures[0]).rsplit('/', 1)[0]
        ctx().set_target(folder)
        parent = resolve.load_asset(parent_material, unreal.MaterialInterface) if parent_material \
            else ensure_master_material()
        instances = {base: create_instance(base, maps, folder, parent) for base, maps in sets.items()}
        result: dict = {'parent': parent.get_path_name(),
                        'materials': {b: {'instance': _package(mi), 'maps': sorted(sets[b])} for b, mi in instances.items()}}
        if assign_to_meshes:
            is_folder = ',' not in assign_to_meshes and not resolve.asset_exists(assign_to_meshes)
            meshes = _collect(None if is_folder else assign_to_meshes, assign_to_meshes if is_folder else None,
                              (unreal.StaticMesh, unreal.SkeletalMesh))
            result['assigned'] = assign_instances(meshes, instances)
        return result
