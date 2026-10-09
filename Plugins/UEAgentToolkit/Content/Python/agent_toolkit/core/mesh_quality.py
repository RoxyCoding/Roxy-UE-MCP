"""Static mesh completion gate shared by import, repair and save tools."""

import unreal

from . import native, resolve
from .errors import Code, ToolError
from .tooling import ctx


def basis_errors(mesh) -> list[str]:
    lib = native.require('Static mesh tangent basis validation')
    if not hasattr(lib, 'get_static_mesh_basis_errors'):
        raise ToolError(Code.NOT_SUPPORTED, 'Rebuild UEAgentToolkitNative and restart the editor to validate mesh bases.')
    return list(lib.get_static_mesh_basis_errors(mesh))


def check_meshes(assets, repair: bool = False) -> list[dict]:
    reports = []
    for mesh in assets:
        if not isinstance(mesh, unreal.StaticMesh):
            continue
        path = mesh.get_path_name()
        errors = basis_errors(mesh)
        repaired = False
        if errors and repair:
            subsystem = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
            if subsystem is None:
                raise ToolError(Code.NOT_SUPPORTED, 'Mesh basis repair requires the full Unreal Editor.', target=path)
            mesh.modify()
            ctx().mark_modified()
            for lod in range(subsystem.get_lod_count(mesh)):
                settings = subsystem.get_lod_build_settings(mesh, lod)
                settings.set_editor_property('recompute_normals', True)
                settings.set_editor_property('recompute_tangents', True)
                settings.set_editor_property('use_mikk_t_space', True)
                settings.set_editor_property('remove_degenerates', True)
                subsystem.set_lod_build_settings(mesh, lod, settings)
            errors = basis_errors(mesh)
            repaired = not errors
        reports.append({'asset': path, 'repaired': repaired, 'errors': errors})
    failed = [report for report in reports if report['errors']]
    if failed:
        raise ToolError(Code.UE_OPERATION_FAILED, 'Static mesh tangent basis validation failed; do not declare completion.',
                        target=failed[0]['asset'],
                        likely_causes=['Fix zero-area faces, collapsed/missing UVs and invalid normals in the source '
                                       'mesh, then regenerate or reimport. Do not disable MikkTSpace to hide warnings.'],
                        details={'mesh_basis_checks': reports})
    return reports


def check_paths(paths, repair: bool = False) -> list[dict]:
    if repair and paths:
        ctx().mark_modified()
    return check_meshes((resolve.load_asset(path) for path in dict.fromkeys(paths)), repair)
