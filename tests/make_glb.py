"""Writes small .glb files for testing: a textured UV sphere or a vertex-coloured one."""
import json
import math
import struct
import zlib


def png(w, h, pixel):
    raw = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(w)) for y in range(h))
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def sphere(seg=48, rings=24):
    pos, uv, idx = [], [], []
    for r in range(rings + 1):
        v = r / rings
        for s in range(seg + 1):
            u = s / seg
            a, b = 2 * math.pi * u, math.pi * v
            pos += [math.sin(b) * math.cos(a), math.cos(b), math.sin(b) * math.sin(a)]
            uv += [u, v]
    for r in range(rings):
        for s in range(seg):
            a = r * (seg + 1) + s; b = a + seg + 1
            idx += [a, b, a + 1, a + 1, b, b + 1]
    return pos, uv, idx


def glb(textured=True, seg=48, rings=24) -> bytes:
    pos, uv, idx = sphere(seg, rings)
    n = len(pos) // 3
    blobs, views, accs = [], [], []

    def add(data: bytes, target=None):
        while sum(len(b) for b in blobs) % 4:
            blobs.append(b"\0")
        off = sum(len(b) for b in blobs)
        blobs.append(data)
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(data), **({"target": target} if target else {})})
        return len(views) - 1

    pv = add(struct.pack(f"<{len(pos)}f", *pos), 34962)
    accs.append({"bufferView": pv, "componentType": 5126, "count": n, "type": "VEC3", "min": [-1, -1, -1], "max": [1, 1, 1]})
    attrs = {"POSITION": 0}
    mesh_prim = {"attributes": attrs, "mode": 4}
    images = textures = materials = None
    if textured:
        uvv = add(struct.pack(f"<{len(uv)}f", *uv), 34962)
        accs.append({"bufferView": uvv, "componentType": 5126, "count": n, "type": "VEC2"}); attrs["TEXCOORD_0"] = 1
        img = png(64, 32, lambda x, y: (230, 60, 60) if (x // 8 + y // 8) % 2 else (240, 240, 240))
        iv = add(img)
        images = [{"bufferView": iv, "mimeType": "image/png"}]
        textures = [{"source": 0}]
        materials = [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}]
        mesh_prim["material"] = 0
    else:
        cols = []
        for i in range(n):
            x, y, z = pos[i * 3:i * 3 + 3]
            cols += [int((x + 1) * 127), int((y + 1) * 127), int((z + 1) * 127), 255]
        cv = add(bytes(cols), 34962)
        accs.append({"bufferView": cv, "componentType": 5121, "normalized": True, "count": n, "type": "VEC4"}); attrs["COLOR_0"] = 1
    iv2 = add(struct.pack(f"<{len(idx)}I", *idx), 34963)
    accs.append({"bufferView": iv2, "componentType": 5125, "count": len(idx), "type": "SCALAR"})
    mesh_prim["indices"] = len(accs) - 1
    binary = b"".join(blobs)
    binary += b"\0" * ((4 - len(binary) % 4) % 4)
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
           "meshes": [{"primitives": [mesh_prim]}], "accessors": accs, "bufferViews": views,
           "buffers": [{"byteLength": len(binary)}]}
    if textured:
        doc.update(images=images, textures=textures, materials=materials)
    j = json.dumps(doc).encode()
    j += b" " * ((4 - len(j) % 4) % 4)
    body = struct.pack("<II", len(j), 0x4E4F534A) + j + struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body


if __name__ == "__main__":
    import sys
    open(sys.argv[1], "wb").write(glb(textured=len(sys.argv) < 3 or sys.argv[2] != "colour"))
