"""Works out what the game's scene lighting does with a lighting set, for the renderer.

Everything here follows the game's EGG code (PAL addresses): the lights as EGG::LightObject
calculates them per camera (0x802C9F30) and hands them to GX (0x802CA230), and the light map textures
as EGG::LightTexture draws them (0x802CC340, 0x802CCA20) from its gradient textures (0x802CBC20).
"""
# standard imports
import math
import re
# 3rd party imports
import bpy
from mathutils import Matrix
import numpy as np
# internal imports
from .lighting import (
    LightingSet, activeLightingSet,
    COORD_SPACE_ITEMS, LIGHT_TYPE_ITEMS, SPOT_FN_ITEMS, DIST_FN_ITEMS, GRADIENT_ITEMS,
    BASE_LAYER_ITEMS, FOG_TYPE_ITEMS, _enumValue
)


LIGHT_MAP_SIZE = 32
"""Width and height of the light maps the game generates."""

MAX_LIGHTS = 40
MAX_LIGHT_SETS = 16
MAX_FOGS = 32
MAX_SET_LIGHTS = 8

# game space (Y up) to Blender space (Z up)
GAME_TO_BLENDER = np.array(((1, 0, 0), (0, 0, -1), (0, 1, 0)), dtype=np.float64)

_NAME_SUFFIX = re.compile(r"\.\d{3}$")


def lightMapName(imageName: str) -> str:
    """Name a light map would have for an image, which is the image's name without a .001 suffix."""
    return _NAME_SUFFIX.sub("", imageName)


def _normalize(v: np.ndarray) -> np.ndarray:
    length = np.linalg.norm(v)
    return v / length if length > 0 else np.zeros(3)


# ---------------------------------------------------------------------------------------------
# gradient textures


def _gradientArray(curve: int, start: int, end: int, size: int = 64) -> np.ndarray:
    """Values (0-255) of a gradient as GenerateGradientArray (0x802C08D0) fills them.

    Black up to `start`, white from `end - 1`, and the curve in between.
    """
    values = np.zeros(size, dtype=np.float64)
    values[end - 1:] = 1
    for i in range(start + 1, end - 1):
        t = (i - start) / (end - start)
        if curve == 1:
            t = t ** 2
        elif curve == 2:
            t = t ** 4
        elif curve == 3:
            t = t ** 8
        elif curve == 4:
            t = t ** 16
        elif curve == 5:
            t = 1 - (t - 1) ** 2
        elif curve == 6:
            t = 1 - (t - 1) ** 4
        values[i] = t
    return np.floor(values * 255)


def _makeGradients() -> np.ndarray:
    """The gradient textures of the light maps: 64 texels each, as EGG::LightTexture::initialize
    makes them. Each is turned by half its width, so a normal at a right angle to the light reads
    the middle of the gradient at texel 0."""
    curves = (0, 0, 0, 1, 2, 3, 4, 0, 5, 5)
    ranges = {1: (0, 0x40), 2: (0x10, 0x40), 7: (4, 0x24), 8: (10, 0x2A), 9: (13, 0x2D)}
    gradients = []
    for kind, curve in enumerate(curves):
        start, end = ranges.get(kind, (0x20, 0x40))
        values = _gradientArray(curve, start + 1, end - 1)
        gradients.append(np.roll(values, -32))
    return np.array(gradients)


GRADIENTS = _makeGradients()


def _sphereNormals() -> np.ndarray:
    """Normals of the sphere the light maps shade, as the bytes of the game's normal texture
    (EGG::CpuTexture::fillNormalMapSphere, 0x802C0F60), top row first. Minus 128 they are what the
    indirect texture stage reads."""
    size = LIGHT_MAP_SIZE
    half = size // 2
    x, y = np.meshgrid(np.arange(size) - half, half - np.arange(size))
    x = x.astype(np.float64)
    y = y.astype(np.float64)
    planar = x * x + y * y
    z = np.sqrt(np.maximum(half * half - planar, 0))
    normals = np.stack((x, y, z), axis=-1)
    lengths = np.linalg.norm(normals, axis=-1, keepdims=True)
    lengths[lengths == 0] = 1
    normals /= lengths
    encoded = np.floor(np.clip(127 + 128 * normals, 0, 255))
    return encoded - 128


SPHERE_NORMALS = _sphereNormals()


def _sampleGradient(kind: int, coord: np.ndarray) -> np.ndarray:
    """Sample a gradient texture as the GPU does, with linear filtering and wrapping."""
    gradient = GRADIENTS[kind]
    pos = coord - .5
    low = np.floor(pos)
    frac = pos - low
    low = low.astype(np.int64)
    a = gradient[low % 64]
    b = gradient[(low + 1) % 64]
    return np.floor(a + (b - a) * frac + .5)


# ---------------------------------------------------------------------------------------------
# lights


class EvaluatedLight:
    """A light as the game has it after its per camera calculation."""

    def __init__(self):
        self.enabled = False
        self.sent = False
        self.lightMaps = False
        self.lightType = 1
        self.color = np.zeros(4)
        """Color times intensity, as bytes (the game's multiplication wraps past 255)."""
        self.shadeColor = np.zeros(4)
        self.viewDir = np.zeros(3)
        """Direction the light shines in, in view space."""
        self.viewPos = np.zeros(3)
        self.aimDistSq = 0.0
        self.specular = False
        self.useColor = True
        self.useAlpha = True
        self.angleAttn = (1.0, 0.0, 0.0)
        self.distAttn = (1.0, 0.0, 0.0)


def _spotCoefficients(cutoff: float, fn: int) -> tuple[float, float, float]:
    """Angle attenuation coefficients as GXInitLightSpot sets them."""
    if cutoff <= 0 or cutoff > 90:
        fn = 0
    cr = math.cos(math.radians(cutoff))
    if fn == 1: # flat
        return (-1000 * cr, 1000, 0)
    if fn == 2: # cos
        return (-cr / (1 - cr), 1 / (1 - cr), 0)
    if fn == 3: # cos squared
        return (0, -cr / (1 - cr), 1 / (1 - cr))
    d = (1 - cr) ** 2
    if fn == 4: # sharp
        return (cr * (cr - 2) / d, 2 / d, -1 / d)
    if fn == 5: # ring 1
        return (-4 * cr / d, 4 * (1 + cr) / d, -4 / d)
    if fn == 6: # ring 2
        return (1 - 2 * cr * cr / d, 4 * cr / d, -2 / d)
    return (1, 0, 0)


def _distCoefficients(refDist: float, refBright: float, fn: int) -> tuple[float, float, float]:
    """Distance attenuation coefficients as GXInitLightDistAttn sets them."""
    if refDist < 0 or refBright <= 0 or refBright >= 1:
        fn = 0
    if fn == 1: # gentle
        return (1, (1 - refBright) / (refBright * refDist), 0)
    if fn == 2: # medium
        return (1, .5 * (1 - refBright) / (refBright * refDist),
                .5 * (1 - refBright) / (refBright * refDist * refDist))
    if fn == 3: # steep
        return (1, 0, (1 - refBright) / (refBright * refDist * refDist))
    return (1, 0, 0)


def evaluateLights(lightingSet: LightingSet, viewMtx: np.ndarray,
                   unitScale: float) -> list[EvaluatedLight]:
    """Calculate the lights of a lighting set for a camera.

    `viewMtx` is Blender's 4x4 world to view matrix, `unitScale` the game units per Blender unit.
    """
    viewRot = viewMtx[:3, :3]
    toViewer = viewRot.T @ np.array((0.0, 0.0, 1.0)) # camera's backward axis in Blender world space
    settings = list(lightingSet.lights)
    lights: list[EvaluatedLight] = []
    for index, s in enumerate(settings):
        light = EvaluatedLight()
        lights.append(light)
        light.enabled = s.enabled
        light.sent = s.enabled and s.send
        light.lightMaps = s.enabled and s.lightMaps
        light.lightType = _enumValue(LIGHT_TYPE_ITEMS, s.lightType)
        light.specular = s.specular
        light.useColor = s.colorEnabled
        light.useAlpha = s.alphaEnabled
        color = np.array([round(c * 255) for c in s.color], dtype=np.float64)
        color[:3] = np.floor(color[:3] * s.intensity) % 256
        light.color = color
        light.shadeColor = np.array([round(c * 255) for c in s.shadeColor], dtype=np.float64)
        # a light that follows another has that one's position and aim
        source = s
        if s.snap and s.snapTarget != index and s.snapTarget < len(settings):
            source = settings[s.snapTarget]
        position = np.array(source.position, dtype=np.float64)
        aim = np.array(source.aim, dtype=np.float64)
        toAim = aim - position
        light.aimDistSq = float(toAim @ toAim)
        direction = _normalize(toAim)
        if not s.enabled:
            continue
        space = _enumValue(COORD_SPACE_ITEMS, s.coordSpace)
        if space in (0, 1):
            worldDir = GAME_TO_BLENDER @ direction
            if space == 1:
                # the direction halfway between the light's and the camera's, where a highlight is
                worldDir = -_normalize(_normalize(-worldDir) + toViewer)
            light.viewDir = viewRot @ worldDir
            worldPos = np.append(GAME_TO_BLENDER @ position / unitScale, 1)
            light.viewPos = (viewMtx @ worldPos)[:3] * unitScale
        elif space == 2:
            light.viewDir = direction
            light.viewPos = position
        elif space == 3:
            light.viewDir = np.array((0.0, 0.0, -1.0))
        elif space == 4:
            light.viewDir = np.array((0.0, 0.0, 1.0))
            light.viewPos = np.array((0.0, 0.0, light.aimDistSq))
        if s.rawAngle:
            light.angleAttn = (1.0, 0.0, 0.0)
        else:
            light.angleAttn = _spotCoefficients(max(s.spotCutoff, 0) or .0001,
                                                _enumValue(SPOT_FN_ITEMS, s.spotFn))
        if s.rawDistance:
            light.distAttn = (1.0, 0.0, 0.0)
        else:
            light.distAttn = _distCoefficients(s.refDistance * light.aimDistSq, s.refBrightness,
                                               _enumValue(DIST_FN_ITEMS, s.distFn))
        if s.specular:
            light.angleAttn = (0.0, 0.0, 1.0)
            light.distAttn = (8.0, 0.0, -7.0)
    return lights


# ---------------------------------------------------------------------------------------------
# light maps


def _tevLerp(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Blend from a to b by c as a TEV stage does, in its 8 bit arithmetic."""
    c = c + np.floor(c / 128)
    return np.floor((a * (256 - c) + b * c) / 256)


def generateLightMap(texture, lightingSet: LightingSet, lights: list[EvaluatedLight]) -> np.ndarray:
    """Draw one light map as the game does: a (32, 32, 4) array of 0-1 values, bottom row first."""
    size = LIGHT_MAP_SIZE
    base = _enumValue(BASE_LAYER_ITEMS, texture.baseLayer)
    # starts from the ambient color or black
    image = np.zeros((size, size, 3), dtype=np.float64)
    if texture.useAmbient and texture.ambientIndex < len(lightingSet.ambients):
        ambient = np.array([round(c * 255) for c in lightingSet.ambients[texture.ambientIndex].color])
        image[:] = np.floor(ambient[:3] * texture.ambientScale) % 256
    normals = SPHERE_NORMALS
    if base == 2:
        normals = np.roll(normals, (size // 2, size // 2), axis=(0, 1))
    batch = np.zeros_like(image)
    numDrawn = 0
    stagesPerQuad = 2 if base == 1 else 3
    for index, light in enumerate(lights):
        if index >= len(texture.lights):
            break
        setting = texture.lights[index]
        if not (light.lightMaps and setting.enabled):
            continue
        if base == 1 and numDrawn >= 2:
            continue
        color = light.color[:3]
        if setting.scale < 1:
            color = np.floor(color * setting.scale)
        kind = _enumValue(GRADIENT_ITEMS, setting.gradient)
        if base == 1:
            # the gradient itself laid over the texture, left to right
            coord = (np.arange(size) + .5) * (64 / size)
            gradient = np.broadcast_to(_sampleGradient(kind, coord)[np.newaxis, :], (size, size))
        else:
            # the indirect stage offsets the gradient lookup by the normal along the light
            coord = (normals @ -light.viewDir) * (.484375 / 2)
            gradient = _sampleGradient(kind, coord)
        stage = _tevLerp(light.shadeColor[:3], color, gradient[..., np.newaxis])
        batch = np.minimum(batch + stage, 255)
        numDrawn += 1
        if numDrawn % stagesPerQuad == 0:
            image = np.minimum(image + batch, 255)
            batch = np.zeros_like(image)
    if numDrawn % stagesPerQuad:
        image = np.minimum(image + batch, 255)
    rgba = np.ones((size, size, 4), dtype=np.float32)
    rgba[..., :3] = image / 255
    return rgba[::-1]


# ---------------------------------------------------------------------------------------------
# everything a frame needs


class SceneLightingState:
    """What the game's scene lighting comes to for one lighting set and camera."""

    def __init__(self):
        self.useLights = False
        self.useFog = False
        self.unitScale = 16.0
        self.lights: list[EvaluatedLight] = []
        self.lightSets: list[tuple[tuple[float, ...], list[int]]] = []
        """Ambient color (0-1) and light indices of each light set."""
        self.fogs: list[tuple[int, float, float, tuple[float, ...]]] = []
        """Type, start, end and color of each fog. Type 0 is no fog."""
        self.lightMaps: dict[str, np.ndarray] = {}
        """Image of each light map, by name."""
        self.dof = None
        self.bloom = None


def evaluate(scene: bpy.types.Scene, viewMtx: Matrix) -> SceneLightingState:
    """Work out the scene lighting of a scene for a camera, or None if it has none enabled."""
    settings = scene.brres
    lightingSet = activeLightingSet(scene)
    if not settings.renderLighting or lightingSet is None:
        return None
    state = SceneLightingState()
    state.unitScale = settings.lightingUnitScale
    view = np.array(viewMtx, dtype=np.float64)
    lights = evaluateLights(lightingSet, view, state.unitScale)
    if settings.renderLights:
        state.useLights = True
        state.lights = lights
        lightIndices = {l.name: i for i, l in enumerate(lightingSet.sceneLights)}
        ambientIndices = {a.name: i for i, a in enumerate(lightingSet.sceneAmbients)}
        for lightSet in lightingSet.lightSets:
            # a set without an ambient color leaves the material's as it is
            ambient = (1.0, 1.0, 1.0, 1.0)
            ambientIndex = ambientIndices.get(lightSet.ambient, -1)
            if 0 <= ambientIndex < len(lightingSet.ambients):
                ambient = tuple(lightingSet.ambients[ambientIndex].color)
            indices = [lightIndices.get(l.light, -1) for l in lightSet.lights]
            state.lightSets.append((ambient, indices))
    if settings.renderFog:
        state.useFog = True
        for fog in lightingSet.fogs:
            fogType = _enumValue(FOG_TYPE_ITEMS, fog.fogType) if fog.enabled else 0
            state.fogs.append((fogType, fog.startZ, fog.endZ, tuple(fog.color)))
    if settings.renderLightMaps:
        for texture in lightingSet.lightTextures:
            state.lightMaps[texture.name] = generateLightMap(texture, lightingSet, lights)
    if settings.renderDof and lightingSet.useDof:
        state.dof = lightingSet.dof
    if settings.renderBloom and lightingSet.useBloom:
        state.bloom = lightingSet.bloom
    return state
