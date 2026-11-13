# 3rd party imports
import bpy
# internal imports
from .brresimport import ImportSettings
from .brresexport import ExportSettings
from .tev import TevSettings
from .lighting import LightingSet, _tagRedraw


class SceneSettings(bpy.types.PropertyGroup):

    tevConfigs: TevSettings.CustomIDCollectionProperty()

    importSettings: bpy.props.PointerProperty(type=ImportSettings)
    exportSettings: bpy.props.PointerProperty(type=ExportSettings)

    renderAssumeOpaqueMats: bpy.props.BoolProperty(
        name="Assume Opaque Materials",
        description="For materials with blending disabled, write an alpha of 1 (opaque). Only affects transparent renders (not viewport)", # pylint: disable=line-too-long
        default=True,
        options=set()
    )

    renderNoTransparentOverwrite: bpy.props.BoolProperty(
        name="Prevent Transparent Overwrites",
        description="Always use blending factors '1' and '1 - Source' for alpha source & destination factors, respectively, independent from color blending. Prevents objects from being made more transparent by stuff in front of them. Only affects transparent renders (not viewport)", # pylint: disable=line-too-long
        default=True,
        options=set()
    )

    lightingSets: bpy.props.CollectionProperty(type=LightingSet)
    activeLightingSet: bpy.props.IntProperty(
        name="Active Lighting Set", update=_tagRedraw, options=set()
    )
    lightingVersion: bpy.props.IntProperty(options={'HIDDEN', 'SKIP_SAVE'})
    """Counts changes to the lighting sets, so renderers know when to read them again."""

    renderLighting: bpy.props.BoolProperty(
        name="Scene Lighting",
        description="Render with the active lighting set as the game would. When disabled, render without scene lights, fog and post effects", # pylint: disable=line-too-long
        default=False, update=_tagRedraw, options=set()
    )

    lightingUnitScale: bpy.props.FloatProperty(
        name="Unit Scale",
        description="Game units per Blender unit, which is the scale models were imported with. Distances of fog, depth of field and lights are in game units", # pylint: disable=line-too-long
        default=16, min=.001, update=_tagRedraw, options=set()
    )

    renderLights: bpy.props.BoolProperty(
        name="Lights", description="Light materials with the lights and ambient colors of the set",
        default=True, update=_tagRedraw, options=set()
    )

    renderLightMaps: bpy.props.BoolProperty(
        name="Light Maps",
        description="Generate the light map textures of the set and use them for textures of their names", # pylint: disable=line-too-long
        default=True, update=_tagRedraw, options=set()
    )

    renderFog: bpy.props.BoolProperty(
        name="Fog", default=True, update=_tagRedraw, options=set()
    )

    renderDof: bpy.props.BoolProperty(
        name="Depth Of Field", default=True, update=_tagRedraw, options=set()
    )

    renderBloom: bpy.props.BoolProperty(
        name="Bloom", default=True, update=_tagRedraw, options=set()
    )

    # version default of (0, 0, 0) just behaves as "most recent" & gets updated on blendfile save
    # this is done because we want the default to be the most recent version, but can't do that
    # directly because:
    # 1) we can't import it from bl_info here because that would make a circular dependency
    # 2) we can't store the version here and then import it when creating bl_info because bl_info
    # is parsed manually by blender or something and seemingly can't use variables for its fields
    version: bpy.props.IntVectorProperty(default=(0, 0, 0))
