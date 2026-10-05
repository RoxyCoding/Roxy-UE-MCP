"""AudioTools: Sound Cues, attenuation assets, sound settings (volume/pitch/loop/attenuation/
class) and audio asset inspection.

Audio components on Blueprints use BlueprintAuthoringTools.add_blueprint_component
(AudioComponent + {"sound": ...}); placing sounds in a level uses LevelTools.spawn_actor with a
sound asset. MetaSound sources can be created with AssetManagementTools.create_asset
(class MetaSoundSource); MetaSound graph editing is not covered.
"""

from __future__ import annotations

import unreal

from agent_toolkit.core import editor, native, resolve
from agent_toolkit.core.errors import Code, ToolError
from agent_toolkit.core.serialize import from_jsonable, parse_json_arg, to_jsonable
from agent_toolkit.core.tooling import agent_tool, ctx


def _get(obj: unreal.Object, name: str):
    try:
        return obj.get_editor_property(name)
    except Exception:  # pylint: disable=broad-exception-caught
        return None


@unreal.uclass()
class AudioTools(unreal.ToolsetDefinition):
    """Audio authoring: Sound Cues from waves, attenuation (3D falloff) assets, per-sound settings
    and inspection of SoundWave / SoundCue / MetaSound assets."""

    @agent_tool(mutates=True)
    def create_sound_cue(asset_path: str, sound_wave_path: str, volume: float = 1.0, pitch: float = 1.0,
                         looping: bool = False, attenuation_path: str | None = None) -> dict:
        """Creates a Sound Cue that plays a Sound Wave, with volume/pitch multipliers, looping and attenuation.

        Args:
            asset_path: New cue path, e.g. /Game/Audio/SC_Footstep.
            sound_wave_path: SoundWave asset.
            volume: Volume multiplier.
            pitch: Pitch multiplier.
            looping: Loop the wave (sets the Wave Player node to loop).
            attenuation_path: Optional SoundAttenuation asset for 3D falloff.
        """
        package, _ = resolve.normalize_asset_path(asset_path)
        ctx().set_target(package)
        if resolve.asset_exists(package):
            raise ToolError(Code.ALREADY_EXISTS, f'{package} already exists', target=package)
        wave = resolve.load_asset(sound_wave_path, unreal.SoundWave)
        attenuation = resolve.load_asset(attenuation_path, unreal.SoundAttenuation) if attenuation_path else None
        folder, name = package.rsplit('/', 1)
        lib = native.graph_library()
        if lib is not None:
            cue = lib.create_sound_cue(folder, name, wave, looping)
        else:  # without the native plugin the cue is created empty (wave player must be added manually)
            cue = editor.asset_tools().create_asset(name, folder, unreal.SoundCue, unreal.SoundCueFactoryNew())
            ctx().warn('UEAgentToolkitNative not loaded: Sound Cue created without a Wave Player node.', 'NO_WAVE_PLAYER')
        if cue is None:
            raise ToolError(Code.UE_OPERATION_FAILED, f'Could not create {package}', target=package)
        cue.set_editor_property('volume_multiplier', volume)
        cue.set_editor_property('pitch_multiplier', pitch)
        if attenuation is not None:
            cue.set_editor_property('attenuation_settings', attenuation)
        return {'asset': package, 'duration': round(float(_get(cue, 'duration') or 0), 3),
                'looping': bool(looping and lib is not None)}

    @agent_tool(mutates=True)
    def create_sound_attenuation(asset_path: str, inner_radius: float = 400.0, falloff_distance: float = 3600.0,
                                 spatialize: bool = True, distance_algorithm: str = 'LINEAR',
                                 enable_occlusion: bool = False, settings_json: str | None = None) -> dict:
        """Creates a Sound Attenuation asset (3D distance falloff).

        Args:
            asset_path: New asset path, e.g. /Game/Audio/ATT_Default.
            inner_radius: Full-volume radius (cm).
            falloff_distance: Distance over which volume falls to zero (cm).
            spatialize: Enable 3D spatialization.
            distance_algorithm: LINEAR, LOGARITHMIC, INVERSE, LOG_REVERSE, NATURAL_SOUND or CUSTOM.
            enable_occlusion: Trace-based occlusion.
            settings_json: Extra SoundAttenuationSettings fields, e.g. {"attenuate_with_lpf": true}.
        """
        package, _ = resolve.normalize_asset_path(asset_path)
        ctx().set_target(package)
        if resolve.asset_exists(package):
            raise ToolError(Code.ALREADY_EXISTS, f'{package} already exists', target=package)
        extra = parse_json_arg(settings_json or '', 'settings_json', dict)
        settings = unreal.SoundAttenuationSettings()
        settings.set_editor_property('attenuate', True)
        settings.set_editor_property('spatialize', spatialize)
        settings.set_editor_property('falloff_distance', falloff_distance)
        settings.set_editor_property('attenuation_shape_extents', unreal.Vector(inner_radius, 0, 0))
        settings.set_editor_property('distance_algorithm', from_jsonable(
            distance_algorithm, settings.get_editor_property('distance_algorithm'), 'distance_algorithm'))
        settings.set_editor_property('enable_occlusion', enable_occlusion)
        for key, value in extra.items():
            settings.set_editor_property(key, from_jsonable(value, settings.get_editor_property(key), key))
        folder, name = package.rsplit('/', 1)
        asset = editor.asset_tools().create_asset(name, folder, unreal.SoundAttenuation, unreal.SoundAttenuationFactory())
        if asset is None:
            raise ToolError(Code.UE_OPERATION_FAILED, f'Could not create {package}', target=package)
        asset.set_editor_property('attenuation', settings)
        return {'asset': package, 'inner_radius': inner_radius, 'falloff_distance': falloff_distance}

    @agent_tool(mutates=True)
    def set_sound_properties(asset_path: str, volume: float | None = None, pitch: float | None = None,
                             looping: bool | None = None, attenuation_path: str | None = None,
                             sound_class_path: str | None = None) -> dict:
        """Changes playback settings of a SoundWave, SoundCue or MetaSound source. Only provided
        arguments change.

        Args:
            asset_path: Sound asset path.
            volume: Volume (SoundWave) / volume multiplier (SoundCue).
            pitch: Pitch (SoundWave) / pitch multiplier (SoundCue).
            looping: Looping flag (SoundWave).
            attenuation_path: SoundAttenuation asset to use.
            sound_class_path: SoundClass asset to route the sound through.
        """
        sound = resolve.load_asset(asset_path, unreal.SoundBase)
        ctx().set_target(sound.get_outermost().get_name())
        changed = {}
        is_cue = isinstance(sound, unreal.SoundCue)
        for value, wave_prop, cue_prop in ((volume, 'volume', 'volume_multiplier'), (pitch, 'pitch', 'pitch_multiplier')):
            if value is None:
                continue
            prop = cue_prop if is_cue else wave_prop
            if _get(sound, prop) is None:
                raise ToolError(Code.NOT_SUPPORTED, f'{sound.get_class().get_name()} has no {prop} setting')
            sound.set_editor_property(prop, value)
            changed[prop] = value
        if looping is not None:
            if _get(sound, 'looping') is None:
                raise ToolError(Code.NOT_SUPPORTED, f'{sound.get_class().get_name()} has no looping flag '
                                '(for Sound Cues loop the Wave Player node).')
            sound.set_editor_property('looping', looping)
            changed['looping'] = looping
        if attenuation_path:
            sound.set_editor_property('attenuation_settings', resolve.load_asset(attenuation_path, unreal.SoundAttenuation))
            changed['attenuation_settings'] = attenuation_path
        if sound_class_path:
            sound.set_editor_property('sound_class_object', resolve.load_asset(sound_class_path, unreal.SoundClass))
            changed['sound_class_object'] = sound_class_path
        if not changed:
            raise ToolError(Code.INVALID_ARGUMENT, 'No setting was provided.')
        return {'changed': changed}

    @agent_tool()
    def inspect_sound(asset_path: str) -> dict:
        """Returns sound asset details: class, duration, channels, sample rate, looping, volume,
        pitch, attenuation (with radii), sound class and max distance.

        Args:
            asset_path: SoundWave, SoundCue, MetaSoundSource or other SoundBase asset.
        """
        sound = resolve.load_asset(asset_path, unreal.SoundBase)
        ctx().set_target(sound.get_outermost().get_name())
        details = {'asset': sound.get_outermost().get_name(), 'class': sound.get_class().get_name()}
        for prop in ('duration', 'num_channels', 'sample_rate', 'looping', 'volume', 'pitch', 'volume_multiplier',
                     'pitch_multiplier', 'max_distance', 'sound_class_object', 'attenuation_settings'):
            value = _get(sound, prop)
            if value is not None:
                details[prop] = to_jsonable(value)
        att = _get(sound, 'attenuation_settings')
        if att is not None:
            s = att.get_editor_property('attenuation')
            details['attenuation'] = {k: to_jsonable(s.get_editor_property(k)) for k in
                                      ('spatialize', 'falloff_distance', 'attenuation_shape_extents',
                                       'distance_algorithm', 'enable_occlusion')}
        return details
