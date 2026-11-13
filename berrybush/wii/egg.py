"""The lighting and post effect files of EGG as New Super Mario Bros. Wii reads them.

Every file is a block of magic, size, version byte at 0x08 and data. Each class keeps the bytes it was
read from and only overwrites the fields it knows when packed, so unknown and unused bytes survive.
"""
# standard imports
import struct


def _name(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", "replace")


class _Block:
    MAGIC = b""
    SIZE = 0
    VERSION = 0

    def __init__(self):
        self._raw = bytearray(self.SIZE)
        self._raw[0:4] = self.MAGIC
        struct.pack_into(">IB", self._raw, 4, self.SIZE, self.VERSION)

    def _check(self, data: bytes, offset: int):
        magic = bytes(data[offset:offset + 4])
        version = data[offset + 8]
        if magic != self.MAGIC:
            raise ValueError(f"Expected {self.MAGIC.decode()} data, found {magic!r}")
        if version != self.VERSION:
            raise ValueError(f"{self.MAGIC.decode()} version {version} is not supported "
                             f"(only version {self.VERSION})")

    def _get(self, fmt: str, offset: int):
        vals = struct.unpack_from(">" + fmt, self._raw, offset)
        return vals[0] if len(vals) == 1 else vals

    def _set(self, fmt: str, offset: int, *vals):
        struct.pack_into(">" + fmt, self._raw, offset, *vals)


# ---------------------------------------------------------------------------------------------
# .blight


class LightObject(_Block):
    """One light of a .blight file (LOBJ)."""

    MAGIC = b"LOBJ"
    SIZE = 0x50
    VERSION = 2

    FLAG_ENABLED = 0x001
    FLAG_SNAP = 0x002
    FLAG_LIGHT_MAPS = 0x020
    FLAG_SEND = 0x040
    FLAG_SPOT = 0x080
    FLAG_MANUAL_DISTANCE = 0x100
    FLAG_G3D_COLOR = 0x200
    FLAG_G3D_ALPHA = 0x400
    FLAG_SHININESS = 0x800

    def __init__(self):
        super().__init__()
        self.spotFn = 0
        self.distFn = 0
        self.coordSpace = 1
        self.lightType = 1
        self.index = 0
        self.flags = 0x661
        self.position = (-10000.0, 10000.0, 10000.0)
        self.aim = (0.0, 0.0, 0.0)
        self.intensity = 1.0
        self.color = (255, 255, 255, 255)
        self.shadeColor = (0, 0, 0, 255)
        self.spotCutoff = 90.0
        self.refDistance = 0.5
        self.refBrightness = 0.5
        self.snapTarget = 0

    @classmethod
    def unpack(cls, data: bytes, offset: int):
        self = cls()
        self._check(data, offset)
        self._raw = bytearray(data[offset:offset + cls.SIZE])
        self.spotFn, self.distFn, self.coordSpace, self.lightType = self._get("4B", 0x10)
        self.index, self.flags = self._get("2H", 0x14)
        self.position = self._get("3f", 0x18)
        self.aim = self._get("3f", 0x24)
        self.intensity = self._get("f", 0x30)
        self.color = self._get("4B", 0x34)
        self.shadeColor = self._get("4B", 0x38)
        self.spotCutoff, self.refDistance, self.refBrightness = self._get("3f", 0x3C)
        self.snapTarget = self._get("H", 0x4C)
        return self

    def pack(self) -> bytes:
        self._set("4B", 0x10, self.spotFn, self.distFn, self.coordSpace, self.lightType)
        self._set("2H", 0x14, self.index, self.flags)
        self._set("3f", 0x18, *self.position)
        self._set("3f", 0x24, *self.aim)
        self._set("f", 0x30, self.intensity)
        self._set("4B", 0x34, *self.color)
        self._set("4B", 0x38, *self.shadeColor)
        self._set("3f", 0x3C, self.spotCutoff, self.refDistance, self.refBrightness)
        self._set("H", 0x4C, self.snapTarget)
        return bytes(self._raw)


class LightFile(_Block):
    """A .blight file (LGHT): the lights and ambient colors of a scene."""

    MAGIC = b"LGHT"
    SIZE = 0x28
    VERSION = 2

    def __init__(self):
        super().__init__()
        self.lights: list[LightObject] = []
        self.ambients: list[tuple[int, int, int, int]] = []
        self._ambientRaw: list[bytearray] = []
        self.backColor = (0, 0, 0, 255)
        self.blendTargets = b""
        """Further LGHT blocks of the file, which the game blends toward (WaterScene)."""

    @classmethod
    def unpack(cls, data: bytes):
        self = cls()
        self._check(data, 0)
        self._raw = bytearray(data[:cls.SIZE])
        numLights, numAmbients = self._get("2H", 0x10)
        self.backColor = self._get("4B", 0x14)
        offset = cls.SIZE
        for _ in range(numLights):
            self.lights.append(LightObject.unpack(data, offset))
            offset += LightObject.SIZE
        for _ in range(numAmbients):
            self._ambientRaw.append(bytearray(data[offset:offset + 8]))
            self.ambients.append(tuple(data[offset:offset + 4]))
            offset += 8
        self.blendTargets = bytes(data[offset:])
        return self

    def pack(self) -> bytes:
        size = self.SIZE + LightObject.SIZE * len(self.lights) + 8 * len(self.ambients)
        self._set("I", 0x04, size)
        self._set("2H", 0x10, len(self.lights), len(self.ambients))
        self._set("4B", 0x14, *self.backColor)
        out = bytearray(self._raw)
        for i, light in enumerate(self.lights):
            # the game writes each light's own index here and never reads it back
            light.index = i
            out += light.pack()
        for i, ambient in enumerate(self.ambients):
            raw = self._ambientRaw[i] if i < len(self._ambientRaw) else bytearray(8)
            raw[0:4] = bytes(ambient)
            out += raw
        return bytes(out) + self.blendTargets


# ---------------------------------------------------------------------------------------------
# .blmap


class LightTextureSetting:
    """How one light is drawn into a light texture."""

    def __init__(self, scale=1.0, gradient=0, enabled=False):
        self.scale = scale
        self.gradient = gradient
        self.enabled = enabled
        self._raw = bytearray(8)


class LightTexture(_Block):
    """One generated light map texture of a .blmap file (LTEX)."""

    MAGIC = b"LTEX"
    SIZE = 0x48
    VERSION = 2
    NUM_SETTINGS = 0x80

    def __init__(self):
        super().__init__()
        self.name = ""
        self.baseLayer = 0
        self.ambientLight = True
        self.ambientIndex = 0
        self.ambientScale = 1.0
        self.settings = [LightTextureSetting() for _ in range(self.NUM_SETTINGS)]

    @property
    def size(self):
        return self.SIZE + 8 * len(self.settings)

    @classmethod
    def unpack(cls, data: bytes, offset: int):
        self = cls()
        self._check(data, offset)
        self._raw = bytearray(data[offset:offset + cls.SIZE])
        numSettings, flags, self.ambientIndex = self._get("HBB", 0x10)
        self.ambientLight = bool(flags & 1)
        self.name = _name(self._raw[0x14:0x34])
        self.baseLayer = self._raw[0x34]
        self.ambientScale = self._get("f", 0x3C)
        self.settings = []
        offset += cls.SIZE
        for _ in range(numSettings):
            setting = LightTextureSetting()
            setting._raw = bytearray(data[offset:offset + 8])
            setting.scale, setting.gradient, settingFlags = struct.unpack_from(">fBB", data, offset)
            setting.enabled = bool(settingFlags & 1)
            self.settings.append(setting)
            offset += 8
        return self

    def pack(self) -> bytes:
        self._set("I", 0x04, self.size)
        flags = (self._raw[0x12] & ~1) | int(self.ambientLight)
        self._set("HBB", 0x10, len(self.settings), flags, self.ambientIndex)
        self._raw[0x14:0x34] = self.name.encode("ascii")[:31].ljust(32, b"\0")
        self._raw[0x34] = self.baseLayer
        self._set("f", 0x3C, self.ambientScale)
        out = bytearray(self._raw)
        for setting in self.settings:
            settingFlags = (setting._raw[5] & ~1) | int(setting.enabled)
            struct.pack_into(">fBB", setting._raw, 0, setting.scale, setting.gradient, settingFlags)
            out += setting._raw
        return bytes(out)


class LightMapFile(_Block):
    """A .blmap file (LMAP): the light map textures the game generates from the lights."""

    MAGIC = b"LMAP"
    SIZE = 0x20
    VERSION = 0

    def __init__(self):
        super().__init__()
        self.textures: list[LightTexture] = []

    @classmethod
    def unpack(cls, data: bytes):
        self = cls()
        self._check(data, 0)
        self._raw = bytearray(data[:cls.SIZE])
        offset = cls.SIZE
        for _ in range(self._get("H", 0x10)):
            texture = LightTexture.unpack(data, offset)
            self.textures.append(texture)
            offset += texture.size
        return self

    def pack(self) -> bytes:
        self._set("I", 0x04, self.SIZE + sum(t.size for t in self.textures))
        self._set("H", 0x10, len(self.textures))
        return bytes(self._raw) + b"".join(t.pack() for t in self.textures)


# ---------------------------------------------------------------------------------------------
# .bfog


class Fog(_Block):
    """One fog of a .bfog file (FOGD). Materials pick theirs by fog set index."""

    MAGIC = b"FOGD"
    SIZE = 0x30
    VERSION = 0

    def __init__(self):
        super().__init__()
        self.startZ = 0.0
        self.endZ = 0.0
        self.nearZ = 0.0
        self.farZ = 0.0
        self.color = (255, 255, 255, 255)
        self.fogType = 0
        self.enabled = False
        self.rangeAdjust = False

    @classmethod
    def unpack(cls, data: bytes, offset: int):
        self = cls()
        self._check(data, offset)
        self._raw = bytearray(data[offset:offset + cls.SIZE])
        self.startZ, self.endZ, self.nearZ, self.farZ = self._get("4f", 0x10)
        self.color = self._get("4B", 0x20)
        self.fogType, flags = self._get("2B", 0x24)
        self.enabled = bool(flags & 1)
        self.rangeAdjust = bool(flags & 2)
        return self

    def pack(self) -> bytes:
        self._set("4f", 0x10, self.startZ, self.endZ, self.nearZ, self.farZ)
        self._set("4B", 0x20, *self.color)
        flags = (self._raw[0x25] & ~3) | int(self.enabled) | (int(self.rangeAdjust) << 1)
        self._set("2B", 0x24, self.fogType, flags)
        return bytes(self._raw)


class FogFile(_Block):
    """A .bfog file (FOGM)."""

    MAGIC = b"FOGM"
    SIZE = 0x14
    VERSION = 0
    NUM_FOGS = 0x20

    def __init__(self):
        super().__init__()
        self.fogs = [Fog() for _ in range(self.NUM_FOGS)]

    @classmethod
    def unpack(cls, data: bytes):
        self = cls()
        self._check(data, 0)
        self._raw = bytearray(data[:cls.SIZE])
        numFogs = self._get("H", 0x10)
        self.fogs = [Fog.unpack(data, cls.SIZE + i * Fog.SIZE) for i in range(numFogs)]
        return self

    def pack(self) -> bytes:
        self._set("I", 0x04, self.SIZE + Fog.SIZE * len(self.fogs))
        self._set("H", 0x10, len(self.fogs))
        return bytes(self._raw) + b"".join(f.pack() for f in self.fogs)


# ---------------------------------------------------------------------------------------------
# .bdof


class DofFile(_Block):
    """A .bdof file (PDOF): depth of field."""

    MAGIC = b"PDOF"
    SIZE = 0x50
    VERSION = 0

    FLAG_WRITE_ALPHA = 0x1
    FLAG_HEAT_HAZE = 0x2
    FLAG_QUARTER_RESOLUTION = 0x4

    def __init__(self):
        super().__init__()
        self.flags = 0
        self.nearStrength = 0
        self.farStrength = 255
        self.drawMode = 0
        self.blurTaps = 0
        self.depthCurve = 0
        self.focusCenter = 0.0
        self.focusRange = 0.0
        self.unused = 20.0
        """Read and written by the game, but used by none of its code."""
        self.blurRadius = 1.6
        self.hazeScroll = (0.0, 0.0)
        self.hazeStrength = (0.0, 0.0)
        self.hazeScale = (1.0, 1.0)

    @classmethod
    def unpack(cls, data: bytes):
        self = cls()
        self._check(data, 0)
        self._raw = bytearray(data[:cls.SIZE])
        self.flags, self.nearStrength, self.farStrength = self._get("H2B", 0x10)
        self.drawMode, self.blurTaps, self.depthCurve = self._get("3B", 0x14)
        self.focusCenter, self.focusRange, self.unused, self.blurRadius = self._get("4f", 0x18)
        self.hazeScroll = self._get("2f", 0x28)
        self.hazeStrength = self._get("2f", 0x30)
        self.hazeScale = self._get("2f", 0x38)
        return self

    def pack(self) -> bytes:
        self._set("H2B", 0x10, self.flags, self.nearStrength, self.farStrength)
        self._set("3B", 0x14, self.drawMode, self.blurTaps, self.depthCurve)
        self._set("4f", 0x18, self.focusCenter, self.focusRange, self.unused, self.blurRadius)
        self._set("2f", 0x28, *self.hazeScroll)
        self._set("2f", 0x30, *self.hazeStrength)
        self._set("2f", 0x38, *self.hazeScale)
        return bytes(self._raw)


# ---------------------------------------------------------------------------------------------
# .bblm


class BloomFile(_Block):
    """A .bblm file (PBLM): bloom."""

    MAGIC = b"PBLM"
    SIZE = 0xA4
    VERSION = 1

    FLAG_SECOND_BLUR = 0x01
    FLAG_SIMPLE_BLUR_TWICE = 0x02
    FLAG_HALF_RESOLUTION = 0x04
    FLAG_FULL_BLUR = 0x08
    FLAG_NO_THRESHOLD_ALPHA = 0x10

    def __init__(self):
        super().__init__()
        self.threshold = 0.5
        self.thresholdColor = (255, 255, 255, 255)
        self.compositeColor = (255, 255, 255, 255)
        self.flags = 0x0B
        self.blurRadius = [1.0, 2.0]
        self.blurIntensity = [0.3, 0.08]
        self.compositeBlend = 1
        self.secondBlurPasses = 2
        self.weights = [0.0, 0.0]

    @classmethod
    def unpack(cls, data: bytes):
        self = cls()
        self._check(data, 0)
        self._raw = bytearray(data[:cls.SIZE])
        self.threshold = self._get("f", 0x10)
        self.thresholdColor = self._get("4B", 0x14)
        self.compositeColor = self._get("4B", 0x18)
        self.flags = self._get("H", 0x1C)
        for i, offset in enumerate((0x20, 0x40)):
            self.blurRadius[i], self.blurIntensity[i] = self._get("2f", offset)
        self.compositeBlend, self.secondBlurPasses = self._get("2B", 0x80)
        self.weights = list(self._get("2f", 0x9C))
        return self

    def pack(self) -> bytes:
        self._set("f", 0x10, self.threshold)
        self._set("4B", 0x14, *self.thresholdColor)
        self._set("4B", 0x18, *self.compositeColor)
        self._set("H", 0x1C, self.flags)
        for i, offset in enumerate((0x20, 0x40)):
            self._set("2f", offset, self.blurRadius[i], self.blurIntensity[i])
        self._set("2B", 0x80, self.compositeBlend, self.secondBlurPasses)
        self._set("2f", 0x9C, *self.weights)
        return bytes(self._raw)


FILE_TYPES: dict[str, type] = {
    "blight": LightFile,
    "blmap": LightMapFile,
    "bfog": FogFile,
    "bdof": DofFile,
    "bblm": BloomFile,
}
"""File class for each extension."""

FOLDERS = {"blight": "light", "blmap": "light", "bfog": "fog", "bdof": "dof", "bblm": "bloom"}
"""Archive folder each extension is kept in."""
