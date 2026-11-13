# standard imports
import struct


MAGIC = 0x55AA382D
_NODE = struct.Struct(">BxHII")


def unpack(data: bytes) -> dict[str, bytes]:
    """Read a U8 archive into a dict mapping file paths (with forward slashes) to their data."""
    magic, nodeOffset = struct.unpack_from(">II", data, 0)
    if magic != MAGIC:
        raise ValueError("Not a U8 archive")
    numNodes = _NODE.unpack_from(data, nodeOffset)[3]
    strOffset = nodeOffset + numNodes * _NODE.size
    files: dict[str, bytes] = {}
    dirEnds: list[tuple[int, str]] = []
    path = ""
    for i in range(1, numNodes):
        while dirEnds and dirEnds[-1][0] <= i:
            path = dirEnds.pop()[1]
        isDir, nameOffset, a, b = _NODE.unpack_from(data, nodeOffset + i * _NODE.size)
        nameStart = strOffset + nameOffset
        name = data[nameStart:data.index(b"\0", nameStart)].decode("shift-jis")
        if isDir:
            dirEnds.append((b, path))
            path = f"{path}{name}/"
        else:
            files[path + name] = data[a:a + b]
    return files


def pack(files: dict[str, bytes]) -> bytes:
    """Write a U8 archive from a dict mapping file paths (with forward slashes) to their data."""
    tree: dict = {}
    for path, fileData in files.items():
        node = tree
        *dirs, name = path.split("/")
        for d in dirs:
            node = node.setdefault(d, {})
        node[name] = fileData
    nodes: list[list] = [[1, 0, 0, 0]]
    strings = bytearray(b"\0")

    def walk(node: dict, parent: int):
        for name, entry in node.items():
            nameOffset = len(strings)
            strings.extend(name.encode("shift-jis") + b"\0")
            if isinstance(entry, dict):
                idx = len(nodes)
                nodes.append([1, nameOffset, parent, 0])
                walk(entry, idx)
                nodes[idx][3] = len(nodes)
            else:
                nodes.append([0, nameOffset, 0, entry])

    walk(tree, 0)
    nodes[0][3] = len(nodes)
    headerSize = 0x20
    tableSize = len(nodes) * _NODE.size + len(strings)
    dataOffset = (headerSize + tableSize + 0x1F) & ~0x1F
    table = bytearray()
    body = bytearray()
    for isDir, nameOffset, a, b in nodes:
        if isDir:
            table += _NODE.pack(1, nameOffset, a, b)
        else:
            body += b"\0" * (-len(body) % 0x20)
            table += _NODE.pack(0, nameOffset, dataOffset + len(body), len(b))
            body += b
    out = struct.pack(">IIII", MAGIC, headerSize, tableSize, dataOffset) + b"\0" * 16
    out += table + strings
    out += b"\0" * (dataOffset - len(out))
    return bytes(out + body)
