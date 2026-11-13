"""Scene settings (SCN0) of a BRRES file, as the game's lighting uses them.

The game takes the light sets (which lights and which ambient color a material's light set index
means) and the names of lights and ambient colors from the SCN0 of scene/scene.brres. The values of
its lights and ambient colors are only starting values, as the .blight of a scene replaces them.

A scene is read from and written back into the BRRES it came from, without moving anything: values
are overwritten in place, and names refer to strings the file already has. So the number of light
sets, lights and ambient colors of a scene is fixed, and they can't be renamed.
"""
# standard imports
import struct


MAX_SET_LIGHTS = 8


def _str(data: bytes, offset: int) -> str:
    return data[offset:data.index(b"\0", offset)].decode("ascii", "replace")


class SceneAmbient:
    """Ambient color of a scene."""

    def __init__(self):
        self.name = ""
        self.color = (100, 100, 100, 0)
        self.useColor = True
        self.useAlpha = True
        self.animated = False
        self._offset = 0


class SceneLight:
    """Light of a scene. Only constant (not animated) values are read."""

    TYPES = ("POINT", "DIRECTIONAL", "SPOT")

    def __init__(self):
        self.name = ""
        self.lightType = 1
        self.enabled = False
        self.useColor = True
        self.useAlpha = True
        self.specular = False
        self.position = (0.0, 0.0, 0.0)
        self.aim = (0.0, 0.0, 0.0)
        self.color = (255, 255, 255, 255)
        self.distFn = 0
        self.refDistance = 0.0
        self.refBrightness = 0.0
        self.spotFn = 0
        self.spotCutoff = 0.0
        self.specularColor = (0, 0, 0, 0)
        self.shininess = 0.0
        self.fixedFlags = 0
        self._offset = 0

    # bits of the fixed flags that say a value is constant instead of animated
    FIXED_POSITION = 0x38
    FIXED_COLOR = 0x40
    FIXED_ENABLE = 0x80
    FIXED_AIM = 0x700
    FIXED_ALL = 0xFFF8

    @property
    def animated(self):
        return (self.fixedFlags & self.FIXED_ALL) != self.FIXED_ALL


class SceneLightSet:
    """Light set of a scene: the lights and ambient color materials with its index are lit by."""

    def __init__(self):
        self.name = ""
        self.ambient = ""
        self.lights: list[str] = []
        self._offset = 0


class Scene:
    """One SCN0 of a BRRES file."""

    def __init__(self):
        self.name = ""
        self.lightSets: list[SceneLightSet] = []
        self.ambients: list[SceneAmbient] = []
        self.lights: list[SceneLight] = []
        self._offset = 0

    @classmethod
    def unpack(cls, data: bytes, offset: int):
        self = cls()
        self._offset = offset
        version = struct.unpack_from(">I", data, offset + 8)[0]
        if version != 5:
            raise ValueError(f"SCN0 version {version} is not supported (only version 5)")
        setsOffset, ambientsOffset, lightsOffset = struct.unpack_from(">3I", data, offset + 0x14)
        self.name = _str(data, offset + struct.unpack_from(">I", data, offset + 0x2C)[0])
        numSets, numAmbients, numLights = struct.unpack_from(">3H", data, offset + 0x3C)
        p = offset + setsOffset
        for _ in range(numSets):
            lightSet = SceneLightSet()
            lightSet._offset = p
            size, _, nameOffset = struct.unpack_from(">IiI", data, p)
            lightSet.name = _str(data, p + nameOffset)
            ambientOffset, _, numSetLights = struct.unpack_from(">IHB", data, p + 0x14)
            lightSet.ambient = _str(data, p + ambientOffset) if ambientOffset else ""
            # unlike every other name, these are relative to the name array
            for nameOffset in struct.unpack_from(">8I", data, p + 0x1C)[:numSetLights]:
                lightSet.lights.append(_str(data, p + 0x1C + nameOffset) if nameOffset else "")
            self.lightSets.append(lightSet)
            p += size
        p = offset + ambientsOffset
        for _ in range(numAmbients):
            ambient = SceneAmbient()
            ambient._offset = p
            size, _, nameOffset = struct.unpack_from(">IiI", data, p)
            ambient.name = _str(data, p + nameOffset)
            fixed, flags = data[p + 0x14], data[p + 0x17]
            ambient.animated = not fixed & 0x80
            ambient.useColor = bool(flags & 1)
            ambient.useAlpha = bool(flags & 2)
            if not ambient.animated:
                ambient.color = tuple(data[p + 0x18:p + 0x1C])
            self.ambients.append(ambient)
            p += size
        p = offset + lightsOffset
        for _ in range(numLights):
            light = SceneLight()
            light._offset = p
            size, _, nameOffset = struct.unpack_from(">IiI", data, p)
            light.name = _str(data, p + nameOffset)
            light.fixedFlags, flags = struct.unpack_from(">HH", data, p + 0x1C)
            light.lightType = flags & 3
            light.enabled = bool(flags & 4)
            light.specular = bool(flags & 8)
            light.useColor = bool(flags & 0x10)
            light.useAlpha = bool(flags & 0x20)
            if not light.animated:
                light.position = struct.unpack_from(">3f", data, p + 0x24)
                light.color = tuple(data[p + 0x30:p + 0x34])
                light.aim = struct.unpack_from(">3f", data, p + 0x34)
                light.distFn, light.refDistance, light.refBrightness, light.spotFn, \
                    light.spotCutoff = struct.unpack_from(">IffIf", data, p + 0x40)
                light.specularColor = tuple(data[p + 0x54:p + 0x58])
                light.shininess = struct.unpack_from(">f", data, p + 0x58)[0]
            self.lights.append(light)
            p += size
        return self

    def packInto(self, data: bytearray):
        """Write the settings of this scene over the SCN0 it was read from."""
        lightNames = {l.name: l._offset + struct.unpack_from(">I", data, l._offset + 8)[0]
                      for l in self.lights}
        ambientNames = {a.name: a._offset + struct.unpack_from(">I", data, a._offset + 8)[0]
                        for a in self.ambients}
        for lightSet in self.lightSets:
            p = lightSet._offset
            if lightSet.ambient and lightSet.ambient not in ambientNames:
                raise ValueError(f"Light set '{lightSet.name}' uses the ambient color "
                                 f"'{lightSet.ambient}', which the scene doesn't have")
            ambientOffset = ambientNames[lightSet.ambient] - p if lightSet.ambient else 0
            lights = lightSet.lights[:MAX_SET_LIGHTS]
            struct.pack_into(">IHB", data, p + 0x14, ambientOffset, 0xFFFF, len(lights))
            offsets = []
            for name in lights:
                if name and name not in lightNames:
                    raise ValueError(f"Light set '{lightSet.name}' uses the light '{name}', "
                                     "which the scene doesn't have")
                offsets.append(lightNames[name] - (p + 0x1C) if name else 0)
            offsets += [0] * (MAX_SET_LIGHTS - len(offsets))
            struct.pack_into(">8I", data, p + 0x1C, *offsets)
            struct.pack_into(">8h", data, p + 0x3C, *([-1] * MAX_SET_LIGHTS))
        for ambient in self.ambients:
            p = ambient._offset
            data[p + 0x17] = (data[p + 0x17] & ~3) | int(ambient.useColor) | int(ambient.useAlpha) << 1
            if not ambient.animated:
                data[p + 0x18:p + 0x1C] = bytes(ambient.color)
        for light in self.lights:
            p = light._offset
            flags = struct.unpack_from(">H", data, p + 0x1E)[0] & ~0x3F
            flags |= light.lightType | int(light.enabled) << 2 | int(light.specular) << 3
            flags |= int(light.useColor) << 4 | int(light.useAlpha) << 5
            struct.pack_into(">H", data, p + 0x1E, flags)
            if not light.animated:
                struct.pack_into(">3f", data, p + 0x24, *light.position)
                data[p + 0x30:p + 0x34] = bytes(light.color)
                struct.pack_into(">3f", data, p + 0x34, *light.aim)
                struct.pack_into(">IffIf", data, p + 0x40, light.distFn, light.refDistance,
                                 light.refBrightness, light.spotFn, light.spotCutoff)
                data[p + 0x54:p + 0x58] = bytes(light.specularColor)
                struct.pack_into(">f", data, p + 0x58, light.shininess)


def findScenes(data: bytes) -> list[Scene]:
    """Read every scene (SCN0) of a BRRES file."""
    if data[:4] != b"bres":
        raise ValueError("Not a BRRES file")
    scenes: list[Scene] = []
    rootOffset = struct.unpack_from(">H", data, 0x0C)[0]
    numSections = struct.unpack_from(">H", data, 0x0E)[0]
    # subfiles follow the root section one after another, each starting with magic and size
    p = rootOffset + struct.unpack_from(">I", data, rootOffset + 4)[0]
    for _ in range(numSections - 1):
        if p + 8 > len(data):
            break
        magic = data[p:p + 4]
        size = struct.unpack_from(">I", data, p + 4)[0]
        if magic == b"SCN0":
            scenes.append(Scene.unpack(data, p))
        if size == 0:
            break
        p += (size + 0x1F) & ~0x1F if magic != b"SCN0" else size
        # sections are aligned, so step to the next magic if padding follows
        while p + 4 <= len(data) and data[p:p + 4] == b"\0\0\0\0":
            p += 4
    return scenes
