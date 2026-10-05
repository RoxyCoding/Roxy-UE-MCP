"""MaterialAuthoringTools: one-call parameter/texture nodes, name-addressed expression wiring,
material settings and compilation with errors.

Complements Epic's MaterialTools (create_material, add_expression, connect_expressions,
recompile, ...) and MaterialInstanceTools (create, set_*_parameter, set_parent).
Expressions are addressed by the names returned from InspectorTools.inspect_material.
"""

from __future__ import annotations

import unreal

from agent_toolkit.core import resolve
from agent_toolkit.core.errors import Code, ToolError
from agent_toolkit.core.serialize import from_jsonable, parse_json_arg, to_jsonable
from agent_toolkit.core.tooling import agent_tool, ctx

MEL = unreal.MaterialEditingLibrary
_OUTPUTS = {
    'base_color': 'MP_BASE_COLOR', 'metallic': 'MP_METALLIC', 'specular': 'MP_SPECULAR', 'roughness': 'MP_ROUGHNESS',
    'anisotropy': 'MP_ANISOTROPY', 'emissive_color': 'MP_EMISSIVE_COLOR', 'emissive': 'MP_EMISSIVE_COLOR',
    'opacity': 'MP_OPACITY', 'opacity_mask': 'MP_OPACITY_MASK', 'normal': 'MP_NORMAL', 'tangent': 'MP_TANGENT',
    'world_position_offset': 'MP_WORLD_POSITION_OFFSET', 'subsurface_color': 'MP_SUBSURFACE_COLOR',
    'ambient_occlusion': 'MP_AMBIENT_OCCLUSION', 'refraction': 'MP_REFRACTION', 'pixel_depth_offset': 'MP_PIXEL_DEPTH_OFFSET',
    'displacement': 'MP_DISPLACEMENT',
}
_PARAM_CLASSES = {
    'scalar': unreal.MaterialExpressionScalarParameter, 'vector': unreal.MaterialExpressionVectorParameter,
    'texture': unreal.MaterialExpressionTextureSampleParameter2D,
    'static_switch': unreal.MaterialExpressionStaticSwitchParameter,
}


def _material(asset_path: str) -> unreal.Material:
    mat = resolve.load_asset(asset_path, unreal.Material)
    ctx().set_target(mat.get_outermost().get_name())
    return mat


def _output_property(name: str):
    key = _OUTPUTS.get(name.lower().lstrip('@'))
    if key is None:
        raise ToolError(Code.INVALID_ARGUMENT, f'Unknown material output {name!r}', likely_causes=[f'Valid: {sorted(_OUTPUTS)}'])
    return getattr(unreal.MaterialProperty, key)


def _expression(mat: unreal.Material, name: str):
    exprs = list(MEL.get_material_expressions(mat) or [])
    for e in exprs:
        if e.get_name() == name:
            return e
    for e in exprs:
        try:
            if str(e.get_editor_property('parameter_name')) == name:
                return e
        except Exception:  # pylint: disable=broad-exception-caught
            continue
    raise ToolError(Code.OBJECT_NOT_FOUND, f'Expression {name!r} not found in {mat.get_name()}',
                    target=mat.get_path_name(),
                    likely_causes=[f'Expressions: {[e.get_name() for e in exprs][:40]} (parameter names also accepted)'])


def _connect(mat: unreal.Material, src_ref: str, dst_ref: str) -> None:
    src_name, _, src_out = src_ref.partition('.')
    src = _expression(mat, src_name)
    if dst_ref.startswith('@'):
        if not MEL.connect_material_property(src, src_out, _output_property(dst_ref)):
            raise ToolError(Code.UE_OPERATION_FAILED, f'Could not connect {src_ref} to {dst_ref}',
                            likely_causes=[f'Outputs of {src_name}: {list(MEL.get_material_expression_output_names(src) or [])} '
                                           '(empty output name = default output)'])
        return
    dst_name, _, dst_in = dst_ref.partition('.')
    dst = _expression(mat, dst_name)
    if not MEL.connect_material_expressions(src, src_out, dst, dst_in):
        raise ToolError(Code.UE_OPERATION_FAILED, f'Could not connect {src_ref} -> {dst_ref}',
                        likely_causes=[f'Outputs of {src_name}: {list(MEL.get_material_expression_output_names(src) or [])}',
                                       f'Inputs of {dst_name}: {list(MEL.get_material_expression_input_names(dst) or [])}'])


@unreal.uclass()
class MaterialAuthoringTools(unreal.ToolsetDefinition):
    """Material graph authoring helpers: settings (domain/blend/shading), parameter and texture
    nodes in one call, material function calls, batch wiring with "Expr.Output->Expr.Input" /
    "Expr.Output->@base_color", and compile with error reporting."""

    @agent_tool(mutates=True)
    def set_material_settings(asset_path: str, domain: str | None = None, blend_mode: str | None = None,
                              shading_model: str | None = None, two_sided: bool | None = None,
                              properties_json: str | None = None) -> dict:
        """Sets material-level settings. Only provided arguments change.

        Args:
            asset_path: Material asset path.
            domain: SURFACE, DEFERRED_DECAL, LIGHT_FUNCTION, VOLUME, POST_PROCESS, USER_INTERFACE.
            blend_mode: OPAQUE, MASKED, TRANSLUCENT, ADDITIVE, MODULATE, ALPHA_COMPOSITE, ALPHA_HOLDOUT.
            shading_model: DEFAULT_LIT, UNLIT, SUBSURFACE, CLEAR_COAT, TWO_SIDED_FOLIAGE, HAIR, CLOTH, EYE, THIN_TRANSLUCENT.
            two_sided: Render back faces.
            properties_json: Other Material properties, e.g. {"used_with_skeletal_mesh": true}.
        """
        mat = _material(asset_path)
        changed = {}
        for prop, value in (('material_domain', domain), ('blend_mode', blend_mode), ('shading_model', shading_model)):
            if value is None:
                continue
            mat.set_editor_property(prop, from_jsonable(value, mat.get_editor_property(prop), prop))
            changed[prop] = to_jsonable(mat.get_editor_property(prop))
        if two_sided is not None:
            mat.set_editor_property('two_sided', two_sided)
            changed['two_sided'] = two_sided
        for key, value in parse_json_arg(properties_json or '', 'properties_json', dict).items():
            mat.set_editor_property(key, from_jsonable(value, mat.get_editor_property(key), key))
            changed[key] = to_jsonable(mat.get_editor_property(key))
        if not changed:
            raise ToolError(Code.INVALID_ARGUMENT, 'No setting was provided.')
        MEL.recompile_material(mat)
        return {'changed': changed}

    @agent_tool(mutates=True)
    def add_material_parameter(asset_path: str, parameter_type: str, parameter_name: str,
                               default_value_json: str | None = None, group: str | None = None,
                               connect_to_output: str | None = None, x: int = -400, y: int = 0) -> dict:
        """Adds a Scalar/Vector/Texture/StaticSwitch parameter node with name, default and group,
        optionally wired straight to a material output.

        Args:
            asset_path: Material asset path.
            parameter_type: scalar, vector, texture or static_switch.
            parameter_name: Parameter name (used by Material Instances).
            default_value_json: scalar -> 0.5; vector -> [r,g,b,a]; texture -> "/Game/T_Albedo"; static_switch -> true.
            group: Parameter group.
            connect_to_output: Output to wire the default output to, e.g. base_color, roughness, emissive_color.
            x: Node X.
            y: Node Y.
        """
        mat = _material(asset_path)
        cls = _PARAM_CLASSES.get(parameter_type.lower())
        if cls is None:
            raise ToolError(Code.INVALID_ARGUMENT, f'parameter_type must be one of {sorted(_PARAM_CLASSES)}')
        existing = [str(n) for fn in (MEL.get_scalar_parameter_names, MEL.get_vector_parameter_names,
                                      MEL.get_texture_parameter_names, MEL.get_static_switch_parameter_names)
                    for n in (fn(mat) or [])]
        if parameter_name in existing:
            raise ToolError(Code.ALREADY_EXISTS, f'Parameter {parameter_name!r} already exists', target=mat.get_path_name())
        expr = MEL.create_material_expression(mat, cls, x, y)
        expr.set_editor_property('parameter_name', unreal.Name(parameter_name))
        if group:
            expr.set_editor_property('group', unreal.Name(group))
        if default_value_json is not None:
            value = parse_json_arg(default_value_json, 'default_value_json', (int, float, list, dict, str, bool))
            prop = {'scalar': 'default_value', 'vector': 'default_value', 'texture': 'texture',
                    'static_switch': 'default_value'}[parameter_type.lower()]
            if prop == 'texture':
                expr.set_editor_property('texture', resolve.load_asset(value, unreal.Texture))
            else:
                expr.set_editor_property(prop, from_jsonable(value, expr.get_editor_property(prop), prop))
        if connect_to_output:
            out = 'RGB' if parameter_type.lower() == 'texture' else ''
            if not MEL.connect_material_property(expr, out, _output_property(connect_to_output)):
                ctx().warn(f'Created but could not connect to {connect_to_output}', 'CONNECT_FAILED')
        MEL.recompile_material(mat)
        return {'expression': expr.get_name(), 'parameter': parameter_name,
                'outputs': list(MEL.get_material_expression_output_names(expr) or [])}

    @agent_tool(mutates=True)
    def add_texture_sample(asset_path: str, texture_path: str, parameter_name: str | None = None,
                           connect_rgb_to: str | None = None, sampler_type: str | None = None,
                           x: int = -400, y: int = 0) -> dict:
        """Adds a Texture Sample (or a Texture parameter when parameter_name is given) for a texture.

        Args:
            asset_path: Material asset path.
            texture_path: Texture asset path.
            parameter_name: Make it a TextureSampleParameter2D with this name.
            connect_rgb_to: Output to wire RGB to (base_color, normal, emissive_color, ...).
            sampler_type: Optional sampler type, e.g. NORMAL, LINEAR_COLOR, MASKS (auto for normal maps).
            x: Node X.
            y: Node Y.
        """
        mat = _material(asset_path)
        tex = resolve.load_asset(texture_path, unreal.Texture)
        cls = unreal.MaterialExpressionTextureSampleParameter2D if parameter_name else unreal.MaterialExpressionTextureSample
        expr = MEL.create_material_expression(mat, cls, x, y)
        expr.set_editor_property('texture', tex)
        if parameter_name:
            expr.set_editor_property('parameter_name', unreal.Name(parameter_name))
        is_normal = False
        try:
            is_normal = 'NORMAL' in str(tex.get_editor_property('compression_settings'))
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        if sampler_type or is_normal:
            expr.set_editor_property('sampler_type', from_jsonable(sampler_type or 'NORMAL',
                                                                    expr.get_editor_property('sampler_type'), 'sampler_type'))
        if connect_rgb_to:
            if not MEL.connect_material_property(expr, 'RGB', _output_property(connect_rgb_to)):
                ctx().warn(f'Could not connect RGB to {connect_rgb_to}', 'CONNECT_FAILED')
        MEL.recompile_material(mat)
        return {'expression': expr.get_name(), 'outputs': list(MEL.get_material_expression_output_names(expr) or [])}

    @agent_tool(mutates=True)
    def add_material_function_call(asset_path: str, function_path: str, x: int = -400, y: int = 0) -> dict:
        """Adds a Material Function Call node for a MaterialFunction asset.

        Args:
            asset_path: Material asset path.
            function_path: MaterialFunction asset path (e.g. /Engine/Functions/Engine_MaterialFunctions02/Texturing/...).
            x: Node X.
            y: Node Y.
        """
        mat = _material(asset_path)
        fn = resolve.load_asset(function_path, unreal.MaterialFunctionInterface)
        expr = MEL.create_material_expression(mat, unreal.MaterialExpressionMaterialFunctionCall, x, y)
        expr.set_material_function(fn) if hasattr(expr, 'set_material_function') else \
            expr.set_editor_property('material_function', fn)
        return {'expression': expr.get_name(), 'inputs': list(MEL.get_material_expression_input_names(expr) or []),
                'outputs': list(MEL.get_material_expression_output_names(expr) or [])}

    @agent_tool(mutates=True)
    def connect_material_expressions(asset_path: str, connections: list[str]) -> dict:
        """Wires expressions in batch. Formats: "ExprName.Output->ExprName.Input" or
        "ExprName.Output->@base_color" (material output). Use an empty output name for the
        default output ("Multiply_0.->@base_color"). Parameter names may replace expression names.

        Args:
            asset_path: Material asset path.
            connections: Connection strings.
        """
        mat = _material(asset_path)
        done = []
        for conn in connections:
            if '->' not in conn:
                raise ToolError(Code.INVALID_ARGUMENT, f'Connection must be "A.Out->B.In" or "A.Out->@output", got {conn!r}')
            src, dst = [c.strip() for c in conn.split('->', 1)]
            _connect(mat, src, dst)
            done.append(conn)
        MEL.recompile_material(mat)
        return {'connected': done}

    @agent_tool()
    def compile_material(asset_path: str) -> dict:
        """Recompiles a Material and returns compile errors plus shader statistics. Fails with
        COMPILE_FAILED when the material has errors.

        Args:
            asset_path: Material asset path.
        """
        mat = _material(asset_path)
        errors = [str(e) for e in MEL.recompile_material(mat) or []]
        stats = {}
        try:
            stats = to_jsonable(MEL.get_statistics(mat))
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        report = {'asset': mat.get_outermost().get_name(), 'errors': errors, 'statistics': stats}
        if errors:
            raise ToolError(Code.COMPILE_FAILED, f'{len(errors)} material compile errors', target=report['asset'],
                            likely_causes=errors[:10], details=report)
        return report
