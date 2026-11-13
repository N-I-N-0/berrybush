# standard imports
import base64
import pathlib
# 3rd party imports
import bpy
from bpy_extras.io_utils import ImportHelper, ExportHelper
# internal imports
from .common import PropertyPanel, drawColumnSeparator
from ..wii import egg, scn0, u8


# A lighting set is what the game loads for one scene: a .blight (lights & ambient colors), a .blmap
# (the light map textures it generates from those lights), a .bfog, and optionally a .bdof and .bblm.
# Sets belong to the scene rather than to objects, and one of them is active at a time.


SCENE_PATH = "scene/scene.brres"
"""Archive path of the scene file, which has the light sets."""


MAX_FOGS = egg.FogFile.NUM_FOGS
MAX_TEXTURE_LIGHTS = egg.LightTexture.NUM_SETTINGS


LIGHT_TYPE_ITEMS = (
    ('POINT', "Point", "Shines from its position in every direction", 'LIGHT_POINT', 0),
    ('DIRECTIONAL', "Directional", "Shines along the direction from its position to its aim", 'LIGHT_SUN', 1), # pylint: disable=line-too-long
    ('SPOT', "Spot", "Shines from its position toward its aim", 'LIGHT_SPOT', 2),
)


COORD_SPACE_ITEMS = (
    ('WORLD', "World", "Position and aim are world coordinates", 0),
    ('WORLD_HIGHLIGHT', "World (Highlight)", "Position and aim are world coordinates, and the light shines along the direction halfway between its own and the camera's. On a light map this puts the light where a highlight of it would be", 1), # pylint: disable=line-too-long
    ('VIEW', "Camera", "Position and aim are relative to the camera", 2),
    ('FROM_CAMERA', "From Camera", "The light shines from the camera into the scene. Position and aim are ignored", 3), # pylint: disable=line-too-long
    ('TOWARD_CAMERA', "Toward Camera", "The light shines from behind the scene toward the camera. Position and aim are ignored", 4), # pylint: disable=line-too-long
)


SPOT_FN_ITEMS = (
    ('OFF', "Off", "", 0),
    ('FLAT', "Flat", "", 1),
    ('COS', "Cosine", "", 2),
    ('COS2', "Cosine Squared", "", 3),
    ('SHARP', "Sharp", "", 4),
    ('RING1', "Ring 1", "", 5),
    ('RING2', "Ring 2", "", 6),
)


DIST_FN_ITEMS = (
    ('OFF', "Off", "", 0),
    ('GENTLE', "Gentle", "", 1),
    ('MEDIUM', "Medium", "", 2),
    ('STEEP', "Steep", "", 3),
)


BASE_LAYER_ITEMS = (
    ('SPHERE', "Sphere", "Each light shades a sphere of normals, for environment mapped textures", 0),
    ('SQUARE', "Gradients", "The gradients of the first two lights are laid flat over the texture", 1),
    ('SPHERE_SHIFTED', "Sphere (Shifted)", "As Sphere, with the center of the sphere in the corners of the texture", 2), # pylint: disable=line-too-long
)


# how far a light reaches around the sphere of a light map, by the cosine of the angle between a
# normal and the direction to the light (1 faces the light, 0 is the edge, -1 faces away)
GRADIENT_ITEMS = (
    ('LAMBERT', "Linear", "Rises evenly from the edge to the side facing the light", 0),
    ('WRAP', "Linear (Full Wrap)", "Rises evenly from the side facing away to the side facing the light", 1), # pylint: disable=line-too-long
    ('HALF_WRAP', "Linear (Half Wrap)", "Rises evenly from halfway around the back to the side facing the light", 2), # pylint: disable=line-too-long
    ('POWER_2', "Squared", "Rises from the edge, concentrated toward the light", 3),
    ('POWER_4', "Power 4", "Rises from the edge, more concentrated toward the light", 4),
    ('POWER_8', "Power 8", "A soft highlight facing the light", 5),
    ('POWER_16', "Power 16", "A tight highlight facing the light", 6),
    ('BACK', "Linear (Back)", "Rises evenly on the side facing away and is full from the edge on", 7), # pylint: disable=line-too-long
    ('SOFT_WRAP', "Eased (Wide Wrap)", "Eases in from well behind the edge and is full a quarter of the way to the light", 8), # pylint: disable=line-too-long
    ('SOFT_HALF_WRAP', "Eased (Wrap)", "Eases in from behind the edge and is full a third of the way to the light", 9), # pylint: disable=line-too-long
)


FOG_TYPE_ITEMS = (
    ('NONE', "None", "", 0x00),
    ('PERSP_LIN', "Linear (Perspective)", "", 0x02),
    ('PERSP_EXP', "Exponential (Perspective)", "", 0x04),
    ('PERSP_EXP2', "Exponential Squared (Perspective)", "", 0x05),
    ('PERSP_REVEXP', "Reverse Exponential (Perspective)", "", 0x06),
    ('PERSP_REVEXP2', "Reverse Exponential Squared (Perspective)", "", 0x07),
    ('ORTHO_LIN', "Linear (Orthographic)", "", 0x0A),
    ('ORTHO_EXP', "Exponential (Orthographic)", "", 0x0C),
    ('ORTHO_EXP2', "Exponential Squared (Orthographic)", "", 0x0D),
    ('ORTHO_REVEXP', "Reverse Exponential (Orthographic)", "", 0x0E),
    ('ORTHO_REVEXP2', "Reverse Exponential Squared (Orthographic)", "", 0x0F),
)


BLOOM_BLEND_ITEMS = (
    ('ADD', "Add", "Bloom is added to the image", 0),
    ('SCREEN', "Screen", "Bloom brightens the image without clipping (used by every Mario Kart Wii course)", 1), # pylint: disable=line-too-long
    ('ADD_ALPHA', "Add (Alpha)", "Bloom times its alpha is added to the image", 2),
    ('ALPHA', "Alpha Blend", "Bloom is mixed with the image by its alpha", 3),
    ('MULTIPLY_ADD', "Multiply & Add", "Bloom times the image is added to the image", 4),
)


def _enumId(items, value: int):
    """Get the identifier of the enum item with some value, or None if there's none."""
    for item in items:
        if item[-1] == value:
            return item[0]
    return None


def _enumValue(items, identifier: str):
    for item in items:
        if item[0] == identifier:
            return item[-1]
    raise KeyError(identifier)


def _color(rgba) -> tuple[float, float, float, float]:
    return tuple(c / 255 for c in rgba)


def _bytes(color) -> tuple[int, int, int, int]:
    return tuple(max(0, min(255, round(c * 255))) for c in color)


def _tagRedraw(self, context: bpy.types.Context):
    """Property update callback that makes the viewports draw again."""
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
    scene = context.scene
    if scene is not None:
        scene.brres.lightingVersion += 1


def _colorProp(name: str, description: str = "", default=(1, 1, 1, 1)):
    return bpy.props.FloatVectorProperty(
        name=name, description=description, subtype='COLOR_GAMMA', size=4,
        default=default, min=0, max=1, update=_tagRedraw, options=set()
    )


class LightSettings(bpy.types.PropertyGroup):

    enabled: bpy.props.BoolProperty(
        name="Enabled",
        description="Calculate this light. A disabled light is black",
        default=False, update=_tagRedraw, options=set()
    )

    send: bpy.props.BoolProperty(
        name="Used By Materials",
        description="Give this light to the light channels of materials",
        default=True, update=_tagRedraw, options=set()
    )

    lightMaps: bpy.props.BoolProperty(
        name="Used By Light Maps",
        description="Let light maps draw this light",
        default=True, update=_tagRedraw, options=set()
    )

    lightType: bpy.props.EnumProperty(
        name="Type", items=LIGHT_TYPE_ITEMS, default='DIRECTIONAL', update=_tagRedraw, options=set()
    )

    coordSpace: bpy.props.EnumProperty(
        name="Space", description="What the position and aim are relative to",
        items=COORD_SPACE_ITEMS, default='WORLD', update=_tagRedraw, options=set()
    )

    position: bpy.props.FloatVectorProperty(
        name="Position", description="In game coordinates (Y up)", size=3,
        default=(-10000, 10000, 10000), update=_tagRedraw, options=set()
    )

    aim: bpy.props.FloatVectorProperty(
        name="Aim", description="Point this light shines toward, in game coordinates (Y up)",
        size=3, update=_tagRedraw, options=set()
    )

    snap: bpy.props.BoolProperty(
        name="Follow Light",
        description="Take the position and aim of another light",
        default=False, update=_tagRedraw, options=set()
    )

    snapTarget: bpy.props.IntProperty(
        name="Followed Light", min=0, max=255, update=_tagRedraw, options=set()
    )

    intensity: bpy.props.FloatProperty(
        name="Intensity", default=1, update=_tagRedraw, options=set()
    )

    color: _colorProp("Color")
    shadeColor: _colorProp(
        "Shade Color",
        "Color light maps give the parts this light does not reach. Each light map blends from it to the color along the gradient of the light", # pylint: disable=line-too-long
        default=(0, 0, 0, 1)
    )

    colorEnabled: bpy.props.BoolProperty(
        name="Lights Color",
        description="Affect the color of light channels",
        default=True, update=_tagRedraw, options=set()
    )

    alphaEnabled: bpy.props.BoolProperty(
        name="Lights Alpha",
        description="Affect the alpha of light channels",
        default=True, update=_tagRedraw, options=set()
    )

    rawAngle: bpy.props.BoolProperty(
        name="No Spot Falloff",
        description="Leave out the fade toward the edge of the cone that the spot function and cutoff give", # pylint: disable=line-too-long
        default=False, update=_tagRedraw, options=set()
    )

    spotFn: bpy.props.EnumProperty(
        name="Spot Function", items=SPOT_FN_ITEMS, default='OFF', update=_tagRedraw, options=set()
    )

    spotCutoff: bpy.props.FloatProperty(
        name="Spot Cutoff", description="Half angle of the cone in degrees",
        default=90, min=0, max=90, update=_tagRedraw, options=set()
    )

    rawDistance: bpy.props.BoolProperty(
        name="No Distance Falloff",
        description="Leave out the fade over distance that the distance function, reference distance and brightness give", # pylint: disable=line-too-long
        default=False, update=_tagRedraw, options=set()
    )

    distFn: bpy.props.EnumProperty(
        name="Distance Function", items=DIST_FN_ITEMS, default='OFF', update=_tagRedraw, options=set()
    )

    refDistance: bpy.props.FloatProperty(
        name="Reference Distance", default=.5, update=_tagRedraw, options=set()
    )

    refBrightness: bpy.props.FloatProperty(
        name="Reference Brightness", default=.5, update=_tagRedraw, options=set()
    )

    specular: bpy.props.BoolProperty(
        name="Specular",
        description="Light the specular channel of materials (shininess 16) instead of the diffuse one", # pylint: disable=line-too-long
        default=False, update=_tagRedraw, options=set()
    )

    otherFlags: bpy.props.IntProperty(options={'HIDDEN'})
    """Flag bits BerryBush has no setting for."""

    _FLAGS = (
        ("enabled", egg.LightObject.FLAG_ENABLED),
        ("snap", egg.LightObject.FLAG_SNAP),
        ("lightMaps", egg.LightObject.FLAG_LIGHT_MAPS),
        ("send", egg.LightObject.FLAG_SEND),
        ("rawAngle", egg.LightObject.FLAG_SPOT),
        ("rawDistance", egg.LightObject.FLAG_MANUAL_DISTANCE),
        ("colorEnabled", egg.LightObject.FLAG_G3D_COLOR),
        ("alphaEnabled", egg.LightObject.FLAG_G3D_ALPHA),
        ("specular", egg.LightObject.FLAG_SHININESS),
    )

    def load(self, light: egg.LightObject):
        known = 0
        for prop, bit in self._FLAGS:
            setattr(self, prop, bool(light.flags & bit))
            known |= bit
        self.otherFlags = light.flags & ~known
        self.lightType = _enumId(LIGHT_TYPE_ITEMS, light.lightType) or 'DIRECTIONAL'
        self.coordSpace = _enumId(COORD_SPACE_ITEMS, light.coordSpace) or 'WORLD'
        self.spotFn = _enumId(SPOT_FN_ITEMS, light.spotFn) or 'OFF'
        self.distFn = _enumId(DIST_FN_ITEMS, light.distFn) or 'OFF'
        self.position = light.position
        self.aim = light.aim
        self.snapTarget = light.snapTarget
        self.intensity = light.intensity
        self.color = _color(light.color)
        self.shadeColor = _color(light.shadeColor)
        self.spotCutoff = light.spotCutoff
        self.refDistance = light.refDistance
        self.refBrightness = light.refBrightness

    def save(self, light: egg.LightObject):
        light.flags = self.otherFlags
        for prop, bit in self._FLAGS:
            if getattr(self, prop):
                light.flags |= bit
        light.lightType = _enumValue(LIGHT_TYPE_ITEMS, self.lightType)
        light.coordSpace = _enumValue(COORD_SPACE_ITEMS, self.coordSpace)
        light.spotFn = _enumValue(SPOT_FN_ITEMS, self.spotFn)
        light.distFn = _enumValue(DIST_FN_ITEMS, self.distFn)
        light.position = tuple(self.position)
        light.aim = tuple(self.aim)
        light.snapTarget = self.snapTarget
        light.intensity = self.intensity
        light.color = _bytes(self.color)
        light.shadeColor = _bytes(self.shadeColor)
        light.spotCutoff = self.spotCutoff
        light.refDistance = self.refDistance
        light.refBrightness = self.refBrightness


class AmbientSettings(bpy.types.PropertyGroup):

    color: _colorProp("Color", default=(.39, .39, .39, 1))


class LightTextureLightSettings(bpy.types.PropertyGroup):
    """How one light is drawn into a light map."""

    enabled: bpy.props.BoolProperty(
        name="Enabled", description="Draw this light into the light map",
        default=False, update=_tagRedraw, options=set()
    )

    scale: bpy.props.FloatProperty(
        name="Scale", description="Brightness of the color of this light in the light map. The game ignores values above 1", # pylint: disable=line-too-long
        default=1, min=0, soft_max=1, update=_tagRedraw, options=set()
    )

    gradient: bpy.props.EnumProperty(
        name="Gradient", description="Falloff of this light across the sphere",
        items=GRADIENT_ITEMS, default='LAMBERT', update=_tagRedraw, options=set()
    )


class LightTextureSettings(bpy.types.PropertyGroup):
    """One light map texture the game generates. Materials use it through a texture of its name."""

    baseLayer: bpy.props.EnumProperty(
        name="Base", items=BASE_LAYER_ITEMS, default='SPHERE', update=_tagRedraw, options=set()
    )

    useAmbient: bpy.props.BoolProperty(
        name="Use Ambient Color", description="Start from an ambient color instead of black",
        default=False, update=_tagRedraw, options=set()
    )

    ambientIndex: bpy.props.IntProperty(
        name="Ambient Color", min=0, max=255, update=_tagRedraw, options=set()
    )

    ambientScale: bpy.props.FloatProperty(
        name="Ambient Scale", description="Brightness of the ambient color in this light map",
        default=1, update=_tagRedraw, options=set()
    )

    lights: bpy.props.CollectionProperty(type=LightTextureLightSettings)

    def load(self, texture: egg.LightTexture):
        self.name = texture.name
        self.baseLayer = _enumId(BASE_LAYER_ITEMS, texture.baseLayer) or 'SPHERE'
        self.useAmbient = texture.ambientLight
        self.ambientIndex = texture.ambientIndex
        self.ambientScale = texture.ambientScale
        self.lights.clear()
        for setting in texture.settings:
            light: LightTextureLightSettings = self.lights.add()
            light.enabled = setting.enabled
            light.scale = setting.scale
            light.gradient = _enumId(GRADIENT_ITEMS, setting.gradient) or 'LAMBERT'

    def save(self, texture: egg.LightTexture):
        texture.name = self.name
        texture.baseLayer = _enumValue(BASE_LAYER_ITEMS, self.baseLayer)
        texture.ambientLight = self.useAmbient
        texture.ambientIndex = self.ambientIndex
        texture.ambientScale = self.ambientScale
        for setting, light in zip(texture.settings, self.lights):
            setting.enabled = light.enabled
            setting.scale = light.scale
            setting.gradient = _enumValue(GRADIENT_ITEMS, light.gradient)


class FogSettings(bpy.types.PropertyGroup):

    enabled: bpy.props.BoolProperty(
        name="Enabled", default=False, update=_tagRedraw, options=set()
    )

    fogType: bpy.props.EnumProperty(
        name="Type", items=FOG_TYPE_ITEMS, default='NONE', update=_tagRedraw, options=set()
    )

    startZ: bpy.props.FloatProperty(
        name="Start", description="Distance from the camera where the fog begins",
        update=_tagRedraw, options=set()
    )

    endZ: bpy.props.FloatProperty(
        name="End", description="Distance from the camera where the fog is complete",
        update=_tagRedraw, options=set()
    )

    nearZ: bpy.props.FloatProperty(
        name="Near", description="Near plane the fog is calculated with (the game replaces it with the camera's)", # pylint: disable=line-too-long
        update=_tagRedraw, options=set()
    )

    farZ: bpy.props.FloatProperty(
        name="Far", description="Far plane the fog is calculated with (the game replaces it with the camera's)", # pylint: disable=line-too-long
        update=_tagRedraw, options=set()
    )

    color: _colorProp("Color")

    rangeAdjust: bpy.props.BoolProperty(
        name="Range Adjustment",
        description="Correct the fog toward the screen edges",
        default=False, update=_tagRedraw, options=set()
    )

    def load(self, fog: egg.Fog):
        self.enabled = fog.enabled
        self.fogType = _enumId(FOG_TYPE_ITEMS, fog.fogType) or 'NONE'
        self.startZ = fog.startZ
        self.endZ = fog.endZ
        self.nearZ = fog.nearZ
        self.farZ = fog.farZ
        self.color = _color(fog.color)
        self.rangeAdjust = fog.rangeAdjust

    def save(self, fog: egg.Fog):
        fog.enabled = self.enabled
        fog.fogType = _enumValue(FOG_TYPE_ITEMS, self.fogType)
        fog.startZ = self.startZ
        fog.endZ = self.endZ
        fog.nearZ = self.nearZ
        fog.farZ = self.farZ
        fog.color = _bytes(self.color)
        fog.rangeAdjust = self.rangeAdjust


DOF_MODE_ITEMS = (
    ('DEPTH', "Gradual", "Blur grows with the distance from the focus distance, read from the depth of the image", 0), # pylint: disable=line-too-long
    ('FOG', "Gradual (Fog)", "As Gradual, with the distance worked out by drawing fog over the image", 1), # pylint: disable=line-too-long
    ('SPLIT', "Split", "Everything in front of the focus distance gets the near strength and everything behind it the far strength", 2), # pylint: disable=line-too-long
)


DOF_TAPS_ITEMS = (
    ('4', "4", "Four samples around each pixel", 0),
    ('2', "2", "Two samples around each pixel", 1),
    ('1', "1", "One sample, which leaves only the softness of the smaller image", 2),
)


DOF_CURVE_ITEMS = (
    ('LINEAR', "Linear", "Blur grows evenly toward the ends of the focus range", 0),
    ('SQUARED', "Squared", "Blur stays low near the focus distance", 1),
    ('POWER_4', "Power 4", "Blur stays low for most of the focus range", 2),
)


class DofSettings(bpy.types.PropertyGroup):

    drawMode: bpy.props.EnumProperty(
        name="Mode", items=DOF_MODE_ITEMS, default='DEPTH', update=_tagRedraw, options=set()
    )

    focusCenter: bpy.props.FloatProperty(
        name="Focus Distance", description="Distance from the camera that is in focus",
        update=_tagRedraw, options=set()
    )

    focusRange: bpy.props.FloatProperty(
        name="Focus Range",
        description="Depth around the focus distance over which the blur grows to full",
        update=_tagRedraw, options=set()
    )

    depthCurve: bpy.props.EnumProperty(
        name="Falloff", description="How the blur grows across the focus range",
        items=DOF_CURVE_ITEMS, default='LINEAR', update=_tagRedraw, options=set()
    )

    nearStrength: bpy.props.IntProperty(
        name="Near Strength",
        description="Opacity of the blur in front of the focus distance (Split mode)",
        min=0, max=255, default=0, update=_tagRedraw, options=set()
    )

    farStrength: bpy.props.IntProperty(
        name="Strength",
        description="Opacity of the blur. In Split mode, of the blur behind the focus distance",
        min=0, max=255, default=255, update=_tagRedraw, options=set()
    )

    blurRadius: bpy.props.FloatProperty(
        name="Blur Radius", description="Distance of the blur samples, in pixels of the smaller image", # pylint: disable=line-too-long
        min=0, default=1.6, update=_tagRedraw, options=set()
    )

    blurTaps: bpy.props.EnumProperty(
        name="Samples", items=DOF_TAPS_ITEMS, default='4', update=_tagRedraw, options=set()
    )

    quarterResolution: bpy.props.BoolProperty(
        name="Quarter Resolution",
        description="Blur a quarter size image instead of a half size one",
        default=False, update=_tagRedraw, options=set()
    )

    writeAlpha: bpy.props.BoolProperty(
        name="Blur Writes Alpha",
        description="Let the blur pass write to the alpha of the frame buffer",
        default=False, update=_tagRedraw, options=set()
    )

    heatHaze: bpy.props.BoolProperty(
        name="Heat Haze",
        description="Distort the blurred image with the scrolling haze texture (dof/shimmer.bti)",
        default=False, update=_tagRedraw, options=set()
    )

    hazeScroll: bpy.props.FloatVectorProperty(
        name="Haze Scroll", description="Movement of the haze texture per frame",
        size=2, precision=4, update=_tagRedraw, options=set()
    )

    hazeStrength: bpy.props.FloatVectorProperty(
        name="Haze Strength", description="How far the haze moves the image",
        size=2, update=_tagRedraw, options=set()
    )

    hazeScale: bpy.props.FloatVectorProperty(
        name="Haze Scale", description="Tiling of the haze texture",
        size=2, default=(1, 1), update=_tagRedraw, options=set()
    )

    unused: bpy.props.FloatProperty(
        name="Unused Value",
        description="The game reads and saves this number, but nothing in it uses it",
        default=20, options=set()
    )

    otherFlags: bpy.props.IntProperty(options={'HIDDEN'})

    _FLAGS = (
        ("writeAlpha", egg.DofFile.FLAG_WRITE_ALPHA),
        ("heatHaze", egg.DofFile.FLAG_HEAT_HAZE),
        ("quarterResolution", egg.DofFile.FLAG_QUARTER_RESOLUTION),
    )
    _FIELDS = ("nearStrength", "farStrength", "focusCenter", "focusRange", "unused", "blurRadius")
    _VECTORS = ("hazeScroll", "hazeStrength", "hazeScale")
    _ENUMS = (("drawMode", DOF_MODE_ITEMS), ("blurTaps", DOF_TAPS_ITEMS),
              ("depthCurve", DOF_CURVE_ITEMS))

    def load(self, dof: egg.DofFile):
        known = 0
        for prop, bit in self._FLAGS:
            setattr(self, prop, bool(dof.flags & bit))
            known |= bit
        self.otherFlags = dof.flags & ~known
        for field in self._FIELDS + self._VECTORS:
            setattr(self, field, getattr(dof, field))
        for field, items in self._ENUMS:
            setattr(self, field, _enumId(items, getattr(dof, field)) or items[0][0])

    def save(self, dof: egg.DofFile):
        dof.flags = self.otherFlags
        for prop, bit in self._FLAGS:
            if getattr(self, prop):
                dof.flags |= bit
        for field in self._FIELDS:
            setattr(dof, field, getattr(self, field))
        for field in self._VECTORS:
            setattr(dof, field, tuple(getattr(self, field)))
        for field, items in self._ENUMS:
            setattr(dof, field, _enumValue(items, getattr(self, field)))


class BloomSettings(bpy.types.PropertyGroup):

    threshold: bpy.props.FloatProperty(
        name="Threshold", description="Brightness above which the image blooms",
        default=.5, min=0, max=1, update=_tagRedraw, options=set()
    )

    thresholdColor: _colorProp(
        "Threshold Color", "Tint of the threshold. A darker channel blooms earlier"
    )

    compositeColor: _colorProp("Color", "Tint of the bloom")

    compositeBlend: bpy.props.EnumProperty(
        name="Blend", description="How the bloom is put on the image",
        items=BLOOM_BLEND_ITEMS, default='SCREEN', update=_tagRedraw, options=set()
    )

    halfResolution: bpy.props.BoolProperty(
        name="Half Resolution",
        description="Work at half the screen size instead of a quarter",
        default=False, update=_tagRedraw, options=set()
    )

    fullBlur: bpy.props.BoolProperty(
        name="Circular Blur",
        description="Blur with samples on a circle instead of two samples side by side",
        default=True, update=_tagRedraw, options=set()
    )

    blurTwice: bpy.props.BoolProperty(
        name="Two Passes",
        description="Run the side by side blur twice",
        default=True, update=_tagRedraw, options=set()
    )

    radius: bpy.props.FloatProperty(
        name="Radius", default=1, min=0, update=_tagRedraw, options=set()
    )

    intensity: bpy.props.FloatProperty(
        name="Intensity", default=.3, min=0, update=_tagRedraw, options=set()
    )

    weight: bpy.props.FloatProperty(
        name="Weight", description="Share of this blur in the bloom",
        default=.2, min=0, update=_tagRedraw, options=set()
    )

    secondBlur: bpy.props.BoolProperty(
        name="Wide Blur",
        description="Add a second, wider blur at half the working size",
        default=True, update=_tagRedraw, options=set()
    )

    secondRadius: bpy.props.FloatProperty(
        name="Radius", default=2, min=0, update=_tagRedraw, options=set()
    )

    secondIntensity: bpy.props.FloatProperty(
        name="Intensity", default=.08, min=0, update=_tagRedraw, options=set()
    )

    secondWeight: bpy.props.FloatProperty(
        name="Weight", description="Share of this blur in the bloom",
        default=.2, min=0, update=_tagRedraw, options=set()
    )

    secondPasses: bpy.props.IntProperty(
        name="Rings", description="Number of sample rings of the wide blur",
        default=2, min=1, max=2, update=_tagRedraw, options=set()
    )

    noThresholdAlpha: bpy.props.BoolProperty(
        name="Ignore Threshold Alpha", default=False, update=_tagRedraw, options=set()
    )

    otherFlags: bpy.props.IntProperty(options={'HIDDEN'})

    _FLAGS = (
        ("secondBlur", egg.BloomFile.FLAG_SECOND_BLUR),
        ("blurTwice", egg.BloomFile.FLAG_SIMPLE_BLUR_TWICE),
        ("halfResolution", egg.BloomFile.FLAG_HALF_RESOLUTION),
        ("fullBlur", egg.BloomFile.FLAG_FULL_BLUR),
        ("noThresholdAlpha", egg.BloomFile.FLAG_NO_THRESHOLD_ALPHA),
    )

    def load(self, bloom: egg.BloomFile):
        known = 0
        for prop, bit in self._FLAGS:
            setattr(self, prop, bool(bloom.flags & bit))
            known |= bit
        self.otherFlags = bloom.flags & ~known
        self.threshold = bloom.threshold
        self.thresholdColor = _color(bloom.thresholdColor)
        self.compositeColor = _color(bloom.compositeColor)
        self.compositeBlend = _enumId(BLOOM_BLEND_ITEMS, bloom.compositeBlend) or 'SCREEN'
        self.radius, self.secondRadius = bloom.blurRadius
        self.intensity, self.secondIntensity = bloom.blurIntensity
        self.weight, self.secondWeight = bloom.weights
        self.secondPasses = max(1, min(2, bloom.secondBlurPasses))

    def save(self, bloom: egg.BloomFile):
        bloom.flags = self.otherFlags
        for prop, bit in self._FLAGS:
            if getattr(self, prop):
                bloom.flags |= bit
        bloom.threshold = self.threshold
        bloom.thresholdColor = _bytes(self.thresholdColor)
        bloom.compositeColor = _bytes(self.compositeColor)
        bloom.compositeBlend = _enumValue(BLOOM_BLEND_ITEMS, self.compositeBlend)
        bloom.blurRadius = [self.radius, self.secondRadius]
        bloom.blurIntensity = [self.intensity, self.secondIntensity]
        bloom.weights = [self.weight, self.secondWeight]
        bloom.secondBlurPasses = self.secondPasses


class SceneLightSettings(bpy.types.PropertyGroup):
    """Starting values of a light in the scene file. The .blight replaces them when a scene loads."""

    enabled: bpy.props.BoolProperty(name="Enabled", default=False, options=set())
    lightType: bpy.props.EnumProperty(
        name="Type", items=LIGHT_TYPE_ITEMS, default='DIRECTIONAL', options=set()
    )
    color: _colorProp("Color")
    position: bpy.props.FloatVectorProperty(name="Position", size=3, options=set())
    aim: bpy.props.FloatVectorProperty(name="Aim", size=3, options=set())
    useColor: bpy.props.BoolProperty(name="Lights Color", default=True, options=set())
    useAlpha: bpy.props.BoolProperty(name="Lights Alpha", default=True, options=set())
    animated: bpy.props.BoolProperty(options={'HIDDEN'})

    def load(self, light: scn0.SceneLight):
        self.name = light.name
        self.enabled = light.enabled
        self.lightType = _enumId(LIGHT_TYPE_ITEMS, light.lightType) or 'DIRECTIONAL'
        self.color = _color(light.color)
        self.position = light.position
        self.aim = light.aim
        self.useColor = light.useColor
        self.useAlpha = light.useAlpha
        self.animated = light.animated

    def save(self, light: scn0.SceneLight):
        light.enabled = self.enabled
        light.lightType = _enumValue(LIGHT_TYPE_ITEMS, self.lightType)
        light.color = _bytes(self.color)
        light.position = tuple(self.position)
        light.aim = tuple(self.aim)
        light.useColor = self.useColor
        light.useAlpha = self.useAlpha


class SceneAmbientSettings(bpy.types.PropertyGroup):
    """Starting value of an ambient color in the scene file. The .blight replaces it."""

    color: _colorProp("Color", default=(.39, .39, .39, 0))


class SceneLightSetLight(bpy.types.PropertyGroup):
    """A light used by a light set, by name."""

    light: bpy.props.StringProperty(
        name="Light", description="Light of the scene", update=_tagRedraw, options=set()
    )


class SceneLightSetSettings(bpy.types.PropertyGroup):
    """A light set: the lights and ambient color that light materials with its index."""

    ambient: bpy.props.StringProperty(
        name="Ambient Color", description="Ambient color of this light set",
        update=_tagRedraw, options=set()
    )

    lights: bpy.props.CollectionProperty(type=SceneLightSetLight)


class LightingSet(bpy.types.PropertyGroup):
    """Everything the game loads to light one scene."""

    lights: bpy.props.CollectionProperty(type=LightSettings)
    activeLight: bpy.props.IntProperty(options=set())
    ambients: bpy.props.CollectionProperty(type=AmbientSettings)
    backColor: _colorProp("Background", default=(0, 0, 0, 1))

    lightTextures: bpy.props.CollectionProperty(type=LightTextureSettings)
    activeLightTexture: bpy.props.IntProperty(options=set())
    showDisabledTextureLights: bpy.props.BoolProperty(
        name="Show Unused Lights", description="List the lights this light map doesn't draw too",
        default=False, options=set()
    )

    fogs: bpy.props.CollectionProperty(type=FogSettings)
    activeFog: bpy.props.IntProperty(options=set())

    useDof: bpy.props.BoolProperty(
        name="Depth Of Field", description="This set has a .bdof",
        default=False, update=_tagRedraw, options=set()
    )
    dof: bpy.props.PointerProperty(type=DofSettings)

    useBloom: bpy.props.BoolProperty(
        name="Bloom", description="This set has a .bblm",
        default=False, update=_tagRedraw, options=set()
    )
    bloom: bpy.props.PointerProperty(type=BloomSettings)

    sceneName: bpy.props.StringProperty(
        name="Scene", description="Name of the scene (SCN0) inside the scene file", options=set()
    )
    lightSets: bpy.props.CollectionProperty(type=SceneLightSetSettings)
    activeSceneLightSet: bpy.props.IntProperty(options=set())
    sceneLights: bpy.props.CollectionProperty(type=SceneLightSettings)
    activeSceneLight: bpy.props.IntProperty(options=set())
    sceneAmbients: bpy.props.CollectionProperty(type=SceneAmbientSettings)
    templateScene: bpy.props.StringProperty(options={'HIDDEN'})
    """The scene.brres the scene was read from."""

    sourceArc: bpy.props.StringProperty(
        name="Archive", description="Archive this set was loaded from", subtype='FILE_PATH',
        options=set()
    )
    sourceDof: bpy.props.StringProperty(
        name="Depth Of Field File", description="Name of the .bdof in the archive", options=set()
    )

    # the files a set was loaded from, so bytes BerryBush has no setting for are written back
    templateBlight: bpy.props.StringProperty(options={'HIDDEN'})
    templateBlmap: bpy.props.StringProperty(options={'HIDDEN'})
    templateBfog: bpy.props.StringProperty(options={'HIDDEN'})
    templateBdof: bpy.props.StringProperty(options={'HIDDEN'})
    templateBblm: bpy.props.StringProperty(options={'HIDDEN'})

    _TEMPLATES = {"blight": "templateBlight", "blmap": "templateBlmap", "bfog": "templateBfog",
                  "bdof": "templateBdof", "bblm": "templateBblm"}

    def _template(self, ext: str):
        """Get the file object the settings of some file type are written into."""
        fileType = egg.FILE_TYPES[ext]
        stored = getattr(self, self._TEMPLATES[ext])
        if stored:
            return fileType.unpack(base64.b64decode(stored))
        return fileType()

    def initDefaults(self):
        """Fill this set with as many lights, ambient colors and fogs as the game's files have."""
        for _ in range(36):
            self.lights.add()
        for _ in range(8):
            self.ambients.add()
        for _ in range(MAX_FOGS):
            self.fogs.add()

    def loadFile(self, ext: str, data: bytes):
        """Load the settings of one file into this set."""
        parsed = egg.FILE_TYPES[ext].unpack(data)
        setattr(self, self._TEMPLATES[ext], base64.b64encode(data).decode("ascii"))
        if ext == "blight":
            self.lights.clear()
            for light in parsed.lights:
                self.lights.add().load(light)
            self.ambients.clear()
            for ambient in parsed.ambients:
                self.ambients.add().color = _color(ambient)
            self.backColor = _color(parsed.backColor)
        elif ext == "blmap":
            self.lightTextures.clear()
            for texture in parsed.textures:
                self.lightTextures.add().load(texture)
        elif ext == "bfog":
            self.fogs.clear()
            for fog in parsed.fogs:
                self.fogs.add().load(fog)
        elif ext == "bdof":
            self.dof.load(parsed)
            self.useDof = True
        elif ext == "bblm":
            self.bloom.load(parsed)
            self.useBloom = True

    def lightName(self, index: int) -> str:
        """Name of a light as the scene file calls it, or a numbered one if it has none."""
        if 0 <= index < len(self.sceneLights):
            return self.sceneLights[index].name
        return f"Light {index}"

    def ambientName(self, index: int) -> str:
        """Name of an ambient color as the scene file calls it."""
        if 0 <= index < len(self.sceneAmbients):
            return self.sceneAmbients[index].name
        return f"Ambient {index}"

    def loadScene(self, data: bytes, sceneName: str = ""):
        """Load the light sets and names of a scene of a scene.brres into this set."""
        scenes = scn0.findScenes(data)
        if not scenes:
            raise ValueError("The scene file has no scene (SCN0)")
        scene = next((s for s in scenes if s.name == sceneName), scenes[0])
        self.templateScene = base64.b64encode(data).decode("ascii")
        self.sceneName = scene.name
        self.lightSets.clear()
        for lightSet in scene.lightSets:
            settings: SceneLightSetSettings = self.lightSets.add()
            settings.name = lightSet.name
            settings.ambient = lightSet.ambient
            for lightName in lightSet.lights:
                settings.lights.add().light = lightName
        self.sceneAmbients.clear()
        for ambient in scene.ambients:
            settings = self.sceneAmbients.add()
            settings.name = ambient.name
            settings.color = _color(ambient.color)
        self.sceneLights.clear()
        for light in scene.lights:
            self.sceneLights.add().load(light)

    def saveScene(self) -> bytes:
        """Get the scene.brres of this set with its scene settings written in, or None."""
        if not self.templateScene:
            return None
        data = bytearray(base64.b64decode(self.templateScene))
        scene = next((s for s in scn0.findScenes(data) if s.name == self.sceneName), None)
        if scene is None:
            return None
        for lightSet, settings in zip(scene.lightSets, self.lightSets):
            lightSet.ambient = settings.ambient
            lightSet.lights = [l.light for l in settings.lights]
        for ambient, settings in zip(scene.ambients, self.sceneAmbients):
            ambient.color = _bytes(settings.color)
        for light, settings in zip(scene.lights, self.sceneLights):
            settings.save(light)
        scene.packInto(data)
        return bytes(data)

    def saveFile(self, ext: str) -> bytes:
        """Get the data of one file of this set, or None if the set has no such file."""
        if ext == "bdof" and not self.useDof:
            return None
        if ext == "bblm" and not self.useBloom:
            return None
        if ext == "blmap" and not self.lightTextures:
            return None
        if ext == "bfog" and not self.fogs:
            return None
        out = self._template(ext)
        if ext == "blight":
            del out.lights[len(self.lights):]
            while len(out.lights) < len(self.lights):
                out.lights.append(egg.LightObject())
            for light, settings in zip(out.lights, self.lights):
                settings.save(light)
            out.ambients = [_bytes(a.color) for a in self.ambients]
            out.backColor = _bytes(self.backColor)
        elif ext == "blmap":
            del out.textures[len(self.lightTextures):]
            while len(out.textures) < len(self.lightTextures):
                out.textures.append(egg.LightTexture())
            for texture, settings in zip(out.textures, self.lightTextures):
                settings.save(texture)
        elif ext == "bfog":
            del out.fogs[len(self.fogs):]
            while len(out.fogs) < len(self.fogs):
                out.fogs.append(egg.Fog())
            for fog, settings in zip(out.fogs, self.fogs):
                settings.save(fog)
        elif ext == "bdof":
            self.dof.save(out)
        elif ext == "bblm":
            self.bloom.save(out)
        return out.pack()


def activeLightingSet(scene: bpy.types.Scene) -> LightingSet:
    """Get the active lighting set of a scene, or None if it has none."""
    settings = scene.brres
    if 0 <= settings.activeLightingSet < len(settings.lightingSets):
        return settings.lightingSets[settings.activeLightingSet]
    return None


def _newSet(scene: bpy.types.Scene, name: str) -> LightingSet:
    settings = scene.brres
    lightingSet: LightingSet = settings.lightingSets.add()
    usedNames = {s.name for s in settings.lightingSets}
    unique = name
    i = 1
    while unique in usedNames:
        unique = f"{name}.{i:03}"
        i += 1
    lightingSet.name = unique
    settings.activeLightingSet = len(settings.lightingSets) - 1
    return lightingSet


# ---------------------------------------------------------------------------------------------
# archives


def archiveSets(files: dict[str, bytes]) -> dict[str, dict[str, str]]:
    """Find the lighting sets of an archive: for each set name, the path of each of its files.

    A set is named after its .blight. Its .blmap, .bfog and .bblm have the same name in their own
    folders. Depth of field files are named differently and chosen on their own.
    """
    sets: dict[str, dict[str, str]] = {}
    byFolderAndName = {p.lower(): p for p in files}
    for path in files:
        folder, _, fileName = path.rpartition("/")
        name, _, ext = fileName.rpartition(".")
        if ext == "blight" and folder == egg.FOLDERS["blight"]:
            found = {"blight": path}
            for other in ("blmap", "bfog", "bblm"):
                otherPath = f"{egg.FOLDERS[other]}/{name}.{other}".lower()
                if otherPath in byFolderAndName:
                    found[other] = byFolderAndName[otherPath]
            sets[name] = found
    return sets


def archiveDofs(files: dict[str, bytes]) -> dict[str, str]:
    """Find the depth of field files of an archive: path for each name."""
    return {p.rpartition("/")[2].rpartition(".")[0]: p for p in files if p.endswith(".bdof")}


_archiveCache: dict[str, tuple[float, dict[str, bytes]]] = {}


def readArchive(path: str) -> dict[str, bytes]:
    """Read the files of an archive, reusing the last read while the file is unchanged."""
    resolved = pathlib.Path(bpy.path.abspath(path))
    modified = resolved.stat().st_mtime
    cached = _archiveCache.get(str(resolved))
    if cached and cached[0] == modified:
        return cached[1]
    files = u8.unpack(resolved.read_bytes())
    _archiveCache[str(resolved)] = (modified, files)
    return files


_enumItemCache: list = []
"""Keeps dynamic enum item strings alive, as Blender doesn't."""


def _archiveSetItems(self, context: bpy.types.Context):
    items = []
    try:
        for name, found in archiveSets(readArchive(self.arcPath)).items():
            kinds = ", ".join(sorted(found))
            items.append((name, name, f"Files: {kinds}"))
    except (OSError, ValueError):
        pass
    if not items:
        items.append(('', "(no lighting sets)", ""))
    _enumItemCache[:] = [items]
    return items


def _archiveDofItems(self, context: bpy.types.Context):
    items = [('NONE', "None", "Load no depth of field")]
    try:
        for name in archiveDofs(readArchive(self.arcPath)):
            items.append((name, name, ""))
    except (OSError, ValueError):
        pass
    _enumItemCache.append(items)
    return items


class LightingSetAdd(bpy.types.Operator):
    """Add an empty lighting set"""

    bl_idname = "brres.lighting_set_add"
    bl_label = "Add Lighting Set"
    bl_options = {'UNDO'}

    def execute(self, context: bpy.types.Context):
        _newSet(context.scene, "Lighting").initDefaults()
        return {'FINISHED'}


class LightingSetRemove(bpy.types.Operator):
    """Remove the active lighting set"""

    bl_idname = "brres.lighting_set_remove"
    bl_label = "Remove Lighting Set"
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context: bpy.types.Context):
        return activeLightingSet(context.scene) is not None

    def execute(self, context: bpy.types.Context):
        settings = context.scene.brres
        settings.lightingSets.remove(settings.activeLightingSet)
        settings.activeLightingSet = max(0, settings.activeLightingSet - 1)
        _tagRedraw(self, context)
        return {'FINISHED'}


class LightingImportArchive(bpy.types.Operator, ImportHelper):
    """Choose an archive (Env_course.arc, Env_world.arc, ...) to load a lighting set from"""

    bl_idname = "brres.lighting_import_arc"
    bl_label = "Open Archive"

    filename_ext = ".arc"
    filter_glob: bpy.props.StringProperty(default="*.arc", options={'HIDDEN'})

    def execute(self, context: bpy.types.Context):
        try:
            sets = archiveSets(readArchive(self.filepath))
        except (OSError, ValueError) as e:
            self.report({'ERROR'}, f"Could not read the archive: {e}")
            return {'CANCELLED'}
        if not sets:
            self.report({'ERROR'}, "This archive has no lighting sets (light/*.blight)")
            return {'CANCELLED'}
        bpy.ops.brres.lighting_import_arc_set('INVOKE_DEFAULT', arcPath=self.filepath)
        return {'FINISHED'}


class LightingImportArchiveSet(bpy.types.Operator):
    """Load one lighting set of an archive"""

    bl_idname = "brres.lighting_import_arc_set"
    bl_label = "Load Lighting Set"
    bl_options = {'UNDO'}

    arcPath: bpy.props.StringProperty(options={'HIDDEN'})

    setName: bpy.props.EnumProperty(
        name="Set", description="Lighting set to load, named after its .blight",
        items=_archiveSetItems
    )

    dofName: bpy.props.EnumProperty(
        name="Depth Of Field", description="Depth of field file to load with the set",
        items=_archiveDofItems
    )

    replace: bpy.props.BoolProperty(
        name="Replace Active Set",
        description="Load into the active lighting set instead of adding a new one",
        default=False
    )

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context: bpy.types.Context):
        layout = self.layout
        layout.use_property_split = True
        layout.label(text=pathlib.Path(self.arcPath).name, icon='FILE_ARCHIVE')
        layout.prop(self, "setName")
        try:
            found = archiveSets(readArchive(self.arcPath)).get(self.setName, {})
            layout.label(text="Files: " + ", ".join(f".{ext}" for ext in sorted(found)))
        except (OSError, ValueError):
            pass
        layout.prop(self, "dofName")
        if activeLightingSet(context.scene) is not None:
            layout.prop(self, "replace")

    def execute(self, context: bpy.types.Context):
        files = readArchive(self.arcPath)
        found = archiveSets(files).get(self.setName)
        if not found:
            self.report({'ERROR'}, "No lighting set chosen")
            return {'CANCELLED'}
        scene = context.scene
        lightingSet = activeLightingSet(scene) if self.replace else None
        if lightingSet is None:
            lightingSet = _newSet(scene, self.setName)
        else:
            lightingSet.name = self.setName
        lightingSet.useDof = False
        lightingSet.useBloom = False
        try:
            for ext, path in found.items():
                lightingSet.loadFile(ext, files[path])
            if self.dofName != 'NONE':
                lightingSet.loadFile("bdof", files[archiveDofs(files)[self.dofName]])
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        if not lightingSet.fogs:
            for _ in range(MAX_FOGS):
                lightingSet.fogs.add()
        if SCENE_PATH in files:
            try:
                lightingSet.loadScene(files[SCENE_PATH])
            except ValueError as e:
                self.report({'WARNING'}, f"Scene file not loaded: {e}")
        lightingSet.sourceArc = self.arcPath
        lightingSet.sourceDof = "" if self.dofName == 'NONE' else self.dofName
        _tagRedraw(self, context)
        self.report({'INFO'}, f"Loaded lighting set '{self.setName}'")
        return {'FINISHED'}


class LightingImportFiles(bpy.types.Operator, ImportHelper):
    """Load loose lighting files (.blight, .blmap, .bfog, .bdof, .bblm) into the active set"""

    bl_idname = "brres.lighting_import_files"
    bl_label = "Load Files"
    bl_options = {'UNDO'}

    filter_glob: bpy.props.StringProperty(
        default="*.blight;*.blmap;*.bfog;*.bdof;*.bblm;*.brres", options={'HIDDEN'}
    )
    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement)
    directory: bpy.props.StringProperty(subtype='DIR_PATH')

    def execute(self, context: bpy.types.Context):
        scene = context.scene
        lightingSet = activeLightingSet(scene)
        paths = [pathlib.Path(self.directory) / f.name for f in self.files if f.name]
        if not paths:
            paths = [pathlib.Path(self.filepath)]
        if lightingSet is None:
            lightingSet = _newSet(scene, paths[0].stem)
            lightingSet.initDefaults()
        for path in paths:
            ext = path.suffix[1:].lower()
            if ext == "brres":
                try:
                    lightingSet.loadScene(path.read_bytes())
                except (OSError, ValueError) as e:
                    self.report({'ERROR'}, f"'{path.name}': {e}")
                    return {'CANCELLED'}
                continue
            if ext not in egg.FILE_TYPES:
                self.report({'WARNING'}, f"'{path.name}' is not a lighting file")
                continue
            try:
                lightingSet.loadFile(ext, path.read_bytes())
            except (OSError, ValueError) as e:
                self.report({'ERROR'}, f"'{path.name}': {e}")
                return {'CANCELLED'}
        _tagRedraw(self, context)
        return {'FINISHED'}


class LightingExportArchive(bpy.types.Operator):
    """Write the active lighting set into an archive, replacing the files of the same name"""

    bl_idname = "brres.lighting_export_arc"
    bl_label = "Save To Archive"

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.arc", options={'HIDDEN'})

    dofName: bpy.props.StringProperty(
        name="Depth Of Field Name",
        description="Name of the .bdof in the archive (the game picks it per stage, not by the set's name)" # pylint: disable=line-too-long
    )

    writeScene: bpy.props.BoolProperty(
        name="Write Light Sets",
        description="Also write the scene file (scene/scene.brres) with the light sets. Every lighting set of the archive shares it", # pylint: disable=line-too-long
        default=True
    )

    @classmethod
    def poll(cls, context: bpy.types.Context):
        return activeLightingSet(context.scene) is not None

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event):
        lightingSet = activeLightingSet(context.scene)
        self.filepath = lightingSet.sourceArc
        self.dofName = lightingSet.sourceDof or lightingSet.name
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context: bpy.types.Context):
        lightingSet = activeLightingSet(context.scene)
        path = pathlib.Path(bpy.path.abspath(self.filepath))
        try:
            files = dict(u8.unpack(path.read_bytes()))
        except (OSError, ValueError) as e:
            self.report({'ERROR'}, f"Could not read the archive: {e}")
            return {'CANCELLED'}
        written = []
        for ext in egg.FILE_TYPES:
            data = lightingSet.saveFile(ext)
            if data is None:
                continue
            name = self.dofName if ext == "bdof" else lightingSet.name
            files[f"{egg.FOLDERS[ext]}/{name}.{ext}"] = data
            written.append(f"{name}.{ext}")
        try:
            sceneData = lightingSet.saveScene()
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        if sceneData is not None and self.writeScene:
            files[SCENE_PATH] = sceneData
            written.append("scene.brres")
        path.write_bytes(u8.pack(files))
        _archiveCache.pop(str(path), None)
        lightingSet.sourceArc = self.filepath
        self.report({'INFO'}, f"Wrote {', '.join(written)} to {path.name}")
        return {'FINISHED'}


class LightingExportFiles(bpy.types.Operator, ExportHelper):
    """Write the files of the active lighting set next to each other"""

    bl_idname = "brres.lighting_export_files"
    bl_label = "Save Files"

    filename_ext = ".blight"
    filter_glob: bpy.props.StringProperty(default="*.blight", options={'HIDDEN'})

    @classmethod
    def poll(cls, context: bpy.types.Context):
        return activeLightingSet(context.scene) is not None

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event):
        self.filepath = activeLightingSet(context.scene).name + self.filename_ext
        return super().invoke(context, event)

    def execute(self, context: bpy.types.Context):
        lightingSet = activeLightingSet(context.scene)
        base = pathlib.Path(self.filepath).with_suffix("")
        for ext in egg.FILE_TYPES:
            data = lightingSet.saveFile(ext)
            if data is not None:
                base.with_suffix(f".{ext}").write_bytes(data)
        try:
            sceneData = lightingSet.saveScene()
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        if sceneData is not None:
            (base.parent / "scene.brres").write_bytes(sceneData)
        return {'FINISHED'}


# ---------------------------------------------------------------------------------------------
# ui


class LIGHTING_UL_sets(bpy.types.UIList):
    bl_idname = "BRRES_UL_lighting_sets"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.prop(item, "name", text="", emboss=False, icon='WORLD')
        row = layout.row(align=True)
        row.alignment = 'RIGHT'
        if item.useDof:
            row.label(text="", icon='CAMERA_DATA')
        if item.useBloom:
            row.label(text="", icon='LIGHT_SUN')


class LIGHTING_UL_lights(bpy.types.UIList):
    bl_idname = "BRRES_UL_lighting_lights"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        sub = row.row(align=True)
        sub.active = item.enabled
        sub.label(text=data.lightName(index), icon=LIGHT_TYPE_ITEMS[_enumValue(LIGHT_TYPE_ITEMS, item.lightType)][3]) # pylint: disable=line-too-long
        swatch = sub.row()
        swatch.scale_x = .4
        swatch.prop(item, "color", text="")


class LIGHTING_UL_lightTextures(bpy.types.UIList):
    bl_idname = "BRRES_UL_lighting_light_textures"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.prop(item, "name", text="", emboss=False, icon='TEXTURE')
        used = sum(1 for light in item.lights if light.enabled)
        row = layout.row()
        row.alignment = 'RIGHT'
        row.label(text=f"{used} lights")


class LIGHTING_UL_fogs(bpy.types.UIList):
    bl_idname = "BRRES_UL_lighting_fogs"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        sub = row.row(align=True)
        sub.active = item.enabled
        sub.label(text=f"Fog Set {index + 1}")
        swatch = sub.row()
        swatch.scale_x = .4
        swatch.prop(item, "color", text="")


class UseStandardView(bpy.types.Operator):
    """Set the view transform to Standard, the only one BerryBush shows the game's colors with"""

    bl_idname = "brres.use_standard_view"
    bl_label = "Use Standard View Transform"
    bl_options = {'UNDO'}

    def execute(self, context: bpy.types.Context):
        context.scene.view_settings.view_transform = 'Standard'
        context.scene.view_settings.look = 'None'
        return {'FINISHED'}


def drawViewTransformWarning(layout: bpy.types.UILayout, context: bpy.types.Context):
    """Warn if the view transform changes the colors BerryBush draws (Filmic, the default of new
    scenes, makes everything paler and flatter than in the game)."""
    viewSettings = context.scene.view_settings
    if viewSettings.view_transform == 'Standard' and viewSettings.look == 'None':
        return
    box = layout.box()
    box.alert = True
    box.label(text=f"The view transform '{viewSettings.view_transform}' changes the colors", icon='ERROR') # pylint: disable=line-too-long
    box.operator(UseStandardView.bl_idname, icon='COLOR')


class LightingPanelBase(PropertyPanel):
    bl_context = "world"
    COMPAT_ENGINES = {'BERRYBUSH'}

    @classmethod
    def poll(cls, context: bpy.types.Context):
        return context.engine in cls.COMPAT_ENGINES


class LightingSubPanel(LightingPanelBase):
    bl_parent_id = "WORLD_PT_brres_lighting"

    @classmethod
    def poll(cls, context: bpy.types.Context):
        return super().poll(context) and activeLightingSet(context.scene) is not None


class LightingPanel(LightingPanelBase):
    bl_idname = "WORLD_PT_brres_lighting"
    bl_label = "Scene Lighting"
    bl_options = set()

    def draw(self, context: bpy.types.Context):
        layout = self.layout
        settings = context.scene.brres
        row = layout.row()
        row.template_list(LIGHTING_UL_sets.bl_idname, "", settings, "lightingSets",
                          settings, "activeLightingSet", rows=3)
        col = row.column(align=True)
        col.operator(LightingSetAdd.bl_idname, text="", icon='ADD')
        col.operator(LightingSetRemove.bl_idname, text="", icon='REMOVE')
        row = layout.row(align=True)
        row.operator(LightingImportArchive.bl_idname, text="Load From Archive", icon='FILE_ARCHIVE')
        row.operator(LightingImportFiles.bl_idname, text="Load Files", icon='FILE_FOLDER')
        row = layout.row(align=True)
        row.operator(LightingExportArchive.bl_idname, text="Save To Archive", icon='FILE_ARCHIVE')
        row.operator(LightingExportFiles.bl_idname, text="Save Files", icon='FILE_TICK')
        lightingSet = activeLightingSet(context.scene)
        if lightingSet is not None and lightingSet.sourceArc:
            layout.label(text=f"From {pathlib.Path(lightingSet.sourceArc).name}", icon='INFO')
        layout.prop(settings, "renderLighting", text="Use In Viewport & Renders")
        drawViewTransformWarning(layout, context)


class LIGHTING_UL_lightSets(bpy.types.UIList):
    bl_idname = "BRRES_UL_lighting_light_sets"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=f"{index + 1}: {item.name}", icon='OUTLINER_OB_LIGHT')
        row = layout.row()
        row.alignment = 'RIGHT'
        row.label(text=", ".join(l.light for l in item.lights if l.light))


class LightSetLightAdd(bpy.types.Operator):
    """Add a light to the light set (up to 8)"""

    bl_idname = "brres.lighting_light_set_light_add"
    bl_label = "Add Light"
    bl_options = {'UNDO'}

    def execute(self, context: bpy.types.Context):
        lightingSet = activeLightingSet(context.scene)
        lightSet = lightingSet.lightSets[lightingSet.activeSceneLightSet]
        if len(lightSet.lights) < scn0.MAX_SET_LIGHTS:
            lightSet.lights.add()
        return {'FINISHED'}


class LightSetLightRemove(bpy.types.Operator):
    """Remove a light from the light set"""

    bl_idname = "brres.lighting_light_set_light_remove"
    bl_label = "Remove Light"
    bl_options = {'UNDO'}

    index: bpy.props.IntProperty()

    def execute(self, context: bpy.types.Context):
        lightingSet = activeLightingSet(context.scene)
        lightingSet.lightSets[lightingSet.activeSceneLightSet].lights.remove(self.index)
        _tagRedraw(self, context)
        return {'FINISHED'}


class LightSetsPanel(LightingSubPanel):
    bl_idname = "WORLD_PT_brres_lighting_light_sets"
    bl_label = "Light Sets"

    def draw(self, context: bpy.types.Context):
        layout = self.layout
        lightingSet = activeLightingSet(context.scene)
        if not lightingSet.lightSets:
            layout.label(text="No scene file loaded (scene/scene.brres of the archive)", icon='INFO')
            return
        layout.label(text=f"Scene '{lightingSet.sceneName}'. A material's light set number picks one of these") # pylint: disable=line-too-long
        layout.template_list(LIGHTING_UL_lightSets.bl_idname, "", lightingSet, "lightSets",
                             lightingSet, "activeSceneLightSet", rows=4)
        if not 0 <= lightingSet.activeSceneLightSet < len(lightingSet.lightSets):
            return
        lightSet: SceneLightSetSettings = lightingSet.lightSets[lightingSet.activeSceneLightSet]
        col = layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop_search(lightSet, "ambient", lightingSet, "sceneAmbients")
        for i, light in enumerate(lightSet.lights):
            row = col.row(align=True)
            row.prop_search(light, "light", lightingSet, "sceneLights", text=f"Light {i + 1}")
            row.operator(LightSetLightRemove.bl_idname, text="", icon='X').index = i
        if len(lightSet.lights) < scn0.MAX_SET_LIGHTS:
            col.operator(LightSetLightAdd.bl_idname, icon='ADD')


class SceneDefaultsPanel(LightingSubPanel):
    bl_idname = "WORLD_PT_brres_lighting_scene_defaults"
    bl_label = "Scene File Defaults"

    def draw(self, context: bpy.types.Context):
        layout = self.layout
        lightingSet = activeLightingSet(context.scene)
        if not lightingSet.sceneLights:
            layout.label(text="No scene file loaded", icon='INFO')
            return
        layout.label(text="Values of the scene file. The lights and ambient colors above replace them")
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column()
        for ambient in lightingSet.sceneAmbients:
            col.prop(ambient, "color", text=ambient.name)
        drawColumnSeparator(col)
        layout.template_list("UI_UL_list", "brres_scene_lights", lightingSet, "sceneLights",
                             lightingSet, "activeSceneLight", rows=4)
        if 0 <= lightingSet.activeSceneLight < len(lightingSet.sceneLights):
            light: SceneLightSettings = lightingSet.sceneLights[lightingSet.activeSceneLight]
            col = layout.column()
            if light.animated:
                col.label(text="This light is animated. Only its switches are editable", icon='INFO')
            col.prop(light, "enabled")
            col.prop(light, "lightType")
            sub = col.column()
            sub.active = not light.animated
            sub.prop(light, "color")
            sub.prop(light, "position")
            sub.prop(light, "aim")
            col.prop(light, "useColor")
            col.prop(light, "useAlpha")


class LightsPanel(LightingSubPanel):
    bl_idname = "WORLD_PT_brres_lighting_lights"
    bl_label = "Lights"

    def draw(self, context: bpy.types.Context):
        layout = self.layout
        lightingSet = activeLightingSet(context.scene)
        layout.template_list(LIGHTING_UL_lights.bl_idname, "", lightingSet, "lights",
                             lightingSet, "activeLight", rows=6)
        if not 0 <= lightingSet.activeLight < len(lightingSet.lights):
            return
        light: LightSettings = lightingSet.lights[lightingSet.activeLight]
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column()
        col.prop(light, "lightType")
        col.prop(light, "color")
        col.prop(light, "intensity")
        col.prop(light, "shadeColor")
        drawColumnSeparator(col)
        col.prop(light, "coordSpace")
        col.prop(light, "snap")
        if light.snap:
            col.prop(light, "snapTarget")
        else:
            col.prop(light, "position")
            col.prop(light, "aim")
        drawColumnSeparator(col)
        col.prop(light, "send")
        sub = col.column()
        sub.active = light.send
        sub.prop(light, "colorEnabled")
        sub.prop(light, "alphaEnabled")
        col.prop(light, "lightMaps")
        drawColumnSeparator(col)
        col.prop(light, "specular")
        if light.lightType == 'SPOT':
            col.prop(light, "rawAngle")
            sub = col.column()
            sub.active = not light.rawAngle
            sub.prop(light, "spotFn")
            sub.prop(light, "spotCutoff")
        if light.lightType != 'DIRECTIONAL':
            col.prop(light, "rawDistance")
            sub = col.column()
            sub.active = not light.rawDistance
            sub.prop(light, "distFn")
            sub.prop(light, "refDistance")
            sub.prop(light, "refBrightness")


class AmbientPanel(LightingSubPanel):
    bl_idname = "WORLD_PT_brres_lighting_ambient"
    bl_label = "Ambient Colors"

    def draw(self, context: bpy.types.Context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        lightingSet = activeLightingSet(context.scene)
        col = layout.column()
        for i, ambient in enumerate(lightingSet.ambients):
            col.prop(ambient, "color", text=lightingSet.ambientName(i))
        drawColumnSeparator(col)
        col.prop(lightingSet, "backColor")


class LightTexturesPanel(LightingSubPanel):
    bl_idname = "WORLD_PT_brres_lighting_light_textures"
    bl_label = "Light Maps"

    def draw(self, context: bpy.types.Context):
        layout = self.layout
        lightingSet = activeLightingSet(context.scene)
        layout.template_list(LIGHTING_UL_lightTextures.bl_idname, "", lightingSet, "lightTextures",
                             lightingSet, "activeLightTexture", rows=6)
        if not 0 <= lightingSet.activeLightTexture < len(lightingSet.lightTextures):
            return
        texture: LightTextureSettings = lightingSet.lightTextures[lightingSet.activeLightTexture]
        col = layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(texture, "baseLayer")
        col.prop(texture, "useAmbient")
        sub = col.column()
        sub.active = texture.useAmbient
        sub.prop(texture, "ambientIndex", text=f"Ambient ({lightingSet.ambientName(texture.ambientIndex)})") # pylint: disable=line-too-long
        sub.prop(texture, "ambientScale")
        layout.prop(lightingSet, "showDisabledTextureLights")
        box = layout.column(align=True)
        numLights = len(lightingSet.lights)
        for i, light in enumerate(texture.lights[:numLights]):
            if not light.enabled and not lightingSet.showDisabledTextureLights:
                continue
            row = box.row(align=True)
            row.prop(light, "enabled", text=lightingSet.lightName(i))
            sub = row.row(align=True)
            sub.active = light.enabled
            sub.prop(light, "scale", text="")
            sub.prop(light, "gradient", text="")


class FogPanel(LightingSubPanel):
    bl_idname = "WORLD_PT_brres_lighting_fog"
    bl_label = "Fog"

    def draw(self, context: bpy.types.Context):
        layout = self.layout
        lightingSet = activeLightingSet(context.scene)
        layout.template_list(LIGHTING_UL_fogs.bl_idname, "", lightingSet, "fogs",
                             lightingSet, "activeFog", rows=4)
        if not 0 <= lightingSet.activeFog < len(lightingSet.fogs):
            return
        fog: FogSettings = lightingSet.fogs[lightingSet.activeFog]
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column()
        col.active = fog.enabled
        col.prop(fog, "fogType")
        col.prop(fog, "color")
        col.prop(fog, "startZ")
        col.prop(fog, "endZ")
        col.prop(fog, "rangeAdjust")


class DofPanel(LightingSubPanel):
    bl_idname = "WORLD_PT_brres_lighting_dof"
    bl_label = "Depth Of Field"

    def draw_header(self, context: bpy.types.Context):
        self.layout.prop(activeLightingSet(context.scene), "useDof", text="")

    def draw(self, context: bpy.types.Context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        lightingSet = activeLightingSet(context.scene)
        dof: DofSettings = lightingSet.dof
        col = layout.column()
        col.active = lightingSet.useDof
        col.prop(dof, "drawMode")
        col.prop(dof, "focusCenter")
        if dof.drawMode == 'SPLIT':
            col.prop(dof, "nearStrength")
            col.prop(dof, "farStrength", text="Far Strength")
        else:
            col.prop(dof, "focusRange")
            col.prop(dof, "depthCurve")
            col.prop(dof, "farStrength")
        drawColumnSeparator(col)
        col.prop(dof, "blurTaps")
        sub = col.column()
        sub.active = dof.blurTaps != '1'
        sub.prop(dof, "blurRadius")
        col.prop(dof, "quarterResolution")
        col.prop(dof, "writeAlpha")
        drawColumnSeparator(col)
        col.prop(dof, "heatHaze")
        sub = col.column()
        sub.active = dof.heatHaze
        sub.prop(dof, "hazeStrength")
        sub.prop(dof, "hazeScale")
        sub.prop(dof, "hazeScroll")
        drawColumnSeparator(col)
        col.prop(dof, "unused")


class BloomPanel(LightingSubPanel):
    bl_idname = "WORLD_PT_brres_lighting_bloom"
    bl_label = "Bloom"

    def draw_header(self, context: bpy.types.Context):
        self.layout.prop(activeLightingSet(context.scene), "useBloom", text="")

    def draw(self, context: bpy.types.Context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        lightingSet = activeLightingSet(context.scene)
        bloom: BloomSettings = lightingSet.bloom
        col = layout.column()
        col.active = lightingSet.useBloom
        col.prop(bloom, "threshold")
        col.prop(bloom, "thresholdColor")
        col.prop(bloom, "compositeColor")
        col.prop(bloom, "compositeBlend")
        col.prop(bloom, "halfResolution")
        drawColumnSeparator(col)
        col.label(text="Blur")
        col.prop(bloom, "fullBlur")
        if not bloom.fullBlur:
            col.prop(bloom, "blurTwice")
        col.prop(bloom, "radius")
        col.prop(bloom, "intensity")
        col.prop(bloom, "weight")
        drawColumnSeparator(col)
        col.prop(bloom, "secondBlur")
        sub = col.column()
        sub.active = bloom.secondBlur
        sub.prop(bloom, "secondRadius")
        sub.prop(bloom, "secondIntensity")
        sub.prop(bloom, "secondWeight")
        sub.prop(bloom, "secondPasses")


class RenderLightingPanel(PropertyPanel):
    bl_idname = "RENDER_PT_brres_lighting"
    bl_label = "Scene Lighting"
    bl_context = "render"
    bl_options = set()
    COMPAT_ENGINES = {'BERRYBUSH'}

    @classmethod
    def poll(cls, context: bpy.types.Context):
        return context.engine in cls.COMPAT_ENGINES

    def draw_header(self, context: bpy.types.Context):
        self.layout.prop(context.scene.brres, "renderLighting", text="")

    def draw(self, context: bpy.types.Context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        settings = context.scene.brres
        lightingSet = activeLightingSet(context.scene)
        drawViewTransformWarning(layout, context)
        if lightingSet is None:
            layout.label(text="No lighting set (see the World tab)", icon='INFO')
        else:
            layout.label(text=f"Set: {lightingSet.name}", icon='WORLD')
        col = layout.column()
        col.active = settings.renderLighting and lightingSet is not None
        col.prop(settings, "lightingUnitScale")
        drawColumnSeparator(col)
        col.prop(settings, "renderLights")
        col.prop(settings, "renderLightMaps")
        col.prop(settings, "renderFog")
        col.prop(settings, "renderDof")
        col.prop(settings, "renderBloom")


CLASSES = (
    SceneLightSettings,
    SceneAmbientSettings,
    SceneLightSetLight,
    SceneLightSetSettings,
    LightSettings,
    AmbientSettings,
    LightTextureLightSettings,
    LightTextureSettings,
    FogSettings,
    DofSettings,
    BloomSettings,
    LightingSet,
)
"""Data classes, to register before the scene settings that use them."""


UI_CLASSES = (
    LIGHTING_UL_sets,
    LIGHTING_UL_lights,
    LIGHTING_UL_lightTextures,
    LIGHTING_UL_fogs,
    LIGHTING_UL_lightSets,
    LightingPanel,
    LightSetsPanel,
    LightsPanel,
    AmbientPanel,
    LightTexturesPanel,
    FogPanel,
    DofPanel,
    BloomPanel,
    SceneDefaultsPanel,
    RenderLightingPanel,
    LightSetLightAdd,
    LightSetLightRemove,
    LightingSetAdd,
    LightingSetRemove,
    UseStandardView,
    LightingImportArchive,
    LightingImportArchiveSet,
    LightingImportFiles,
    LightingExportArchive,
    LightingExportFiles,
)
