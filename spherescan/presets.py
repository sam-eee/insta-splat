"""The four detail levels and the mesh file types. Nothing else sets quality."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Detail:
    level: int
    name: str
    blurb: str
    frames: int            # camera positions (a "frame" = one instant seen by every lens)
    sfm_quality: str       # spirula sfm --quality
    train_quality: str     # spirula train --quality (splat budget + steps)
    splats: int            # cap, millions of splats (for estimates)
    iterations: int
    texture_size: int
    min_vram_gb: float     # below this the level is marked as unavailable


DETAILS = {
    1: Detail(1, "Quick preview", "Fastest. Checks your footage works.", 120, "low", "low", 0.2, 15000, 2048, 0),
    2: Detail(2, "Standard", "Good for most footage.", 200, "medium", "medium", 1.0, 30000, 4096, 4),
    3: Detail(3, "High", "Sharper detail. Takes longer.", 320, "high", "high", 3.0, 50000, 4096, 6),
    4: Detail(4, "Maximum", "Best quality. Slowest.", 500, "high", "ultra", 10.0, 80000, 8192, 8),
}


@dataclass(frozen=True)
class MeshFormat:
    key: str
    label: str
    spirula_format: str    # spirula mesh --format
    color: str             # spirula mesh --color
    extension: str
    hint: str
    keeps_color: bool


FORMATS = {
    "glb": MeshFormat("glb", "GLB (recommended)", "glb+jpg", "texture", ".glb",
                      "One file with colour and detail built in. Opens in Windows 3D Viewer, Blender, "
                      "and online viewers; on Android and iPhone use a free viewer app.", True),
    "glb-lossless": MeshFormat("glb-lossless", "GLB, lossless texture", "glb+png", "texture", ".glb",
                               "Same as GLB with an uncompressed texture. Larger file.", True),
    "obj": MeshFormat("obj", "OBJ + texture", "obj", "texture", ".obj",
                      "Widely supported. Keep the .obj, .mtl and image files together.", True),
    "gltf": MeshFormat("gltf", "glTF + texture", "gltf", "texture", ".gltf",
                       "Like GLB but split into several files.", True),
    "ply": MeshFormat("ply", "PLY (vertex colours)", "ply", "vertex", ".ply",
                      "Colour is stored per point, so fine texture detail is lost. For scanning tools.", False),
    "stl": MeshFormat("stl", "STL (no colour)", "stl", "none", ".stl",
                      "For 3D printing. STL cannot store colour.", False),
}
DEFAULT_FORMAT = "glb"
