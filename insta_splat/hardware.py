"""What this computer can do, and how to decode video on it.

Everything here is a best-effort probe: a failed probe yields "unknown", never
an exception, so the app still starts on odd machines.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .util import IS_LINUX, IS_MAC, IS_WINDOWS, find_executable, run


@dataclass
class Gpu:
    name: str
    vendor: str                 # nvidia | amd | intel | apple | other
    vram_gb: Optional[float]    # None when unknown
    integrated: bool
    speed: float                # relative to an RTX 3060 = 1.0 (estimates only)


@dataclass
class DecodePlan:
    ffmpeg: Optional[str]
    hwaccel: Optional[str]      # value for ffmpeg -hwaccel, None = software
    verified: bool              # a real test decode succeeded with it
    reason: str


@dataclass
class Hardware:
    os: str
    cpu_threads: int
    ram_gb: Optional[float]
    gpus: list = field(default_factory=list)
    vulkan: Optional[bool] = None       # None = could not tell
    decode: Optional[DecodePlan] = None
    usable_vram_gb: Optional[float] = None
    problems: list = field(default_factory=list)   # block running
    notes: list = field(default_factory=list)      # informational

    def best_gpu(self) -> Optional[Gpu]:
        if not self.gpus:
            return None
        return max(self.gpus, key=lambda g: (not g.integrated, g.speed))

    def to_dict(self) -> dict:
        return asdict(self)


# (regex on lowercase name, speed relative to RTX 3060). First match wins.
_SPEED = [
    (r"rtx 5090", 4.5), (r"rtx 5080", 3.2), (r"rtx 5070", 2.3), (r"rtx 5060", 1.5),
    (r"rtx 4090", 4.0), (r"rtx 4080", 3.0), (r"rtx 4070", 2.1), (r"rtx 4060", 1.3),
    (r"rtx 3090", 2.8), (r"rtx 3080", 2.4), (r"rtx 3070", 1.8), (r"rtx 3060", 1.0),
    (r"rtx 3050", 0.65), (r"rtx 2080", 1.5), (r"rtx 2070", 1.2), (r"rtx 2060", 0.8),
    (r"gtx 16", 0.55), (r"gtx 10[78]0", 0.6), (r"gtx 10", 0.4),
    (r"rx 79", 2.4), (r"rx 78", 1.9), (r"rx 77", 1.5), (r"rx 76", 1.0),
    (r"rx 6[89]", 1.6), (r"rx 6[67]", 0.9), (r"rx 5", 0.7),
    (r"arc.*\bb5", 1.1), (r"arc.*\ba7", 1.0), (r"arc.*\ba5", 0.8), (r"arc", 0.6),
    (r"iris xe", 0.25), (r"uhd|hd graphics", 0.12), (r"radeon.*graphics$|vega", 0.2),
]


def _apple_speed(name: str) -> float:
    m = re.search(r"\bm(\d)\b", name.lower())
    gen = int(m.group(1)) if m else 1
    base = {1: 0.5, 2: 0.6, 3: 0.75, 4: 0.9}.get(gen, 1.0)
    n = name.lower()
    mult = 4.0 if "ultra" in n else 2.4 if "max" in n else 1.6 if "pro" in n else 1.0
    return base * mult


def _classify(name: str, vram_gb: Optional[float], ram_gb: Optional[float]) -> Gpu:
    n = name.lower()
    if "nvidia" in n or "geforce" in n or "quadro" in n or re.search(r"\brtx\b", n):
        vendor = "nvidia"
    elif "apple" in n or re.match(r"m\d\b", n):
        vendor = "apple"
    elif "amd" in n or "radeon" in n or "advanced micro" in n:
        vendor = "amd"
    elif "intel" in n or "arc" in n:
        vendor = "intel"
    else:
        vendor = "other"
    integrated = vendor == "apple" or bool(re.search(r"uhd|hd graphics|iris|radeon.*graphics$|vega \d", n))
    speed = _apple_speed(name) if vendor == "apple" else next(
        (s for rx, s in _SPEED if re.search(rx, n)), 0.2 if integrated else 0.6)
    if integrated and ram_gb and (vram_gb is None or vendor == "apple"):
        vram_gb = round(ram_gb * (0.65 if vendor == "apple" else 0.4), 1)   # shared memory
    return Gpu(name=name.strip(), vendor=vendor, vram_gb=vram_gb, integrated=integrated, speed=speed)


def _ram_gb() -> Optional[float]:
    try:
        if IS_WINDOWS:
            class MS(ctypes.Structure):
                _fields_ = [("l", ctypes.c_ulong), ("m", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong), ("c", ctypes.c_ulonglong),
                            ("d", ctypes.c_ulonglong), ("e", ctypes.c_ulonglong), ("f", ctypes.c_ulonglong)]
            s = MS(); s.l = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))  # type: ignore[attr-defined]
            return round(s.total / 2**30, 1)
        if IS_MAC:
            rc, out = run(["sysctl", "-n", "hw.memsize"])
            return round(int(out.strip()) / 2**30, 1) if rc == 0 else None
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / 2**20, 1)
    except (OSError, ValueError, AttributeError):
        pass
    return None


def _gpus_nvidia(ram: Optional[float]) -> list:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    rc, out = run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    gpus = []
    if rc == 0:
        for line in out.strip().splitlines():
            name, _, mem = line.rpartition(",")
            try:
                gpus.append(_classify(name, round(float(mem) / 1024, 1), ram))
            except ValueError:
                continue
    return gpus


def _gpus_windows(ram: Optional[float]) -> list:
    rc, out = run(["powershell", "-NoProfile", "-Command",
                   "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Json"], 20)
    try:
        data = json.loads(out)
    except ValueError:
        return []
    data = data if isinstance(data, list) else [data]
    gpus = []
    for d in data:
        name = d.get("Name") or ""
        if not name or "basic render" in name.lower() or "remote" in name.lower():
            continue
        # AdapterRAM is a 32-bit field, so it reads 4 GB for any larger card: only trust it below that.
        ar = d.get("AdapterRAM")
        vram = round(ar / 2**30, 1) if isinstance(ar, int) and 0 < ar < 4 * 2**30 - 1 else None
        gpus.append(_classify(name, vram, ram))
    return gpus


def _gpus_linux(ram: Optional[float]) -> list:
    rc, out = run(["lspci", "-mm"])
    gpus = []
    for line in out.splitlines():
        if re.search(r'"(VGA compatible controller|3D controller|Display controller)"', line):
            parts = re.findall(r'"([^"]*)"', line)
            if len(parts) >= 4:
                gpus.append(_classify(f"{parts[2]} {parts[3]}", None, ram))
    # amdgpu exposes dedicated memory in sysfs; use it for any AMD card without a size.
    amd = [g for g in gpus if g.vendor == "amd" and g.vram_gb is None]
    for p in sorted(Path("/sys/class/drm").glob("card[0-9]/device/mem_info_vram_total")):
        try:
            gb = round(int(p.read_text()) / 2**30, 1)
        except (OSError, ValueError):
            continue
        if amd:
            amd.pop(0).vram_gb = gb
    return gpus


def _gpus_mac(ram: Optional[float]) -> list:
    rc, out = run(["system_profiler", "SPDisplaysDataType", "-json"], 20)
    try:
        items = json.loads(out).get("SPDisplaysDataType", [])
    except ValueError:
        return []
    return [_classify(i.get("sppci_model", "GPU"), None, ram) for i in items]


def detect_gpus(ram: Optional[float]) -> list:
    nv = _gpus_nvidia(ram)
    if IS_WINDOWS:
        other = [g for g in _gpus_windows(ram) if g.vendor != "nvidia"]
        gpus = nv + other if nv else _gpus_windows(ram)
    elif IS_MAC:
        gpus = _gpus_mac(ram)
    else:
        other = [g for g in _gpus_linux(ram) if g.vendor != "nvidia"]
        gpus = nv + other if nv else _gpus_linux(ram)
    return gpus


def detect_vulkan() -> Optional[bool]:
    if IS_MAC:
        return True     # Spirula bundles MoltenVK
    exe = shutil.which("vulkaninfo")
    if exe:
        rc, out = run([exe, "--summary"], 20)
        if rc == 0:
            return "deviceName" in out or "GPU" in out
    import ctypes.util
    lib = ctypes.util.find_library("vulkan-1" if IS_WINDOWS else "vulkan")
    if lib:
        return True
    if IS_WINDOWS and (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "vulkan-1.dll").exists():
        return True
    return None if exe is None and not lib else False


# ---- video decoding ------------------------------------------------------

def _hwaccel_preference(gpu: Optional[Gpu]) -> list:
    vendor = gpu.vendor if gpu else "other"
    if IS_MAC:
        return ["videotoolbox"]
    if IS_WINDOWS:
        return (["cuda", "d3d11va"] if vendor == "nvidia" else ["d3d11va", "dxva2", "qsv"])
    return {"nvidia": ["cuda"], "amd": ["vaapi"], "intel": ["vaapi", "qsv"]}.get(vendor, ["vaapi"])


_HW_FAIL = re.compile(r"failed setup for format|hwaccel initialisation returned error|"
                      r"device creation failed|cannot load|no device available|failed to initialise|"
                      r"error initializing", re.I)


def _test_hwaccel(ffmpeg: str, hw: str) -> Optional[bool]:
    """Decode a tiny clip with `hw`. True/False, or None if no test clip could be made."""
    with tempfile.TemporaryDirectory() as td:
        clip = str(Path(td) / "t.mp4")
        rc, _ = run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=10",
                     "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", clip], 30)
        if rc != 0 or not Path(clip).exists():
            return None
        rc, out = run([ffmpeg, "-v", "warning", "-hwaccel", hw, "-i", clip, "-f", "null", "-"], 30)
        return rc == 0 and not _HW_FAIL.search(out)


def plan_decode(gpu: Optional[Gpu], verify: bool = True) -> DecodePlan:
    ffmpeg = find_executable("ffmpeg", "INSTA_SPLAT_FFMPEG")
    if not ffmpeg:
        return DecodePlan(None, None, False,
                          "No separate video helper needed: Spirula decodes video itself where it can.")
    rc, out = run([ffmpeg, "-hide_banner", "-hwaccels"])
    available = {l.strip() for l in out.splitlines()[1:] if l.strip()} if rc == 0 else set()
    for hw in _hwaccel_preference(gpu):
        if hw not in available:
            continue
        ok = _test_hwaccel(ffmpeg, hw) if verify else None
        if ok is False:
            continue
        return DecodePlan(ffmpeg, hw, ok is True,
                          f"GPU video decoding ({hw})" + ("" if ok else " (could not be test-verified)"))
    return DecodePlan(ffmpeg, None, False, "Software decoding on the CPU (no working GPU decoder found)")


# ---- LOD gating ------------------------------------------------------------

def usable_vram(gpus: list, ram: Optional[float]) -> Optional[float]:
    g = max(gpus, key=lambda g: (not g.integrated, g.vram_gb or 0), default=None)
    return g.vram_gb if g else None


def detect(verify_decode: bool = True) -> Hardware:
    ram = _ram_gb()
    hw = Hardware(os="Windows" if IS_WINDOWS else "macOS" if IS_MAC else "Linux",
                  cpu_threads=os.cpu_count() or 1, ram_gb=ram)
    hw.gpus = detect_gpus(ram)
    hw.vulkan = detect_vulkan()
    best = hw.best_gpu()
    hw.usable_vram_gb = usable_vram(hw.gpus, ram)
    hw.decode = plan_decode(best, verify_decode)
    if not hw.gpus:
        hw.problems.append("No graphics card was detected. Training needs a Vulkan-capable GPU.")
    if hw.vulkan is False:
        hw.problems.append("Vulkan was not found. Install your graphics driver's latest version.")
    if hw.ram_gb is not None and hw.ram_gb < 8:
        hw.notes.append("Under 8 GB of RAM: long videos may be slow. Detail levels above 2 are not recommended.")
    if best and best.integrated:
        hw.notes.append("Integrated graphics detected: expect long run times, and stay at low detail levels.")
    if len([g for g in hw.gpus if not g.integrated]) and any(g.integrated for g in hw.gpus):
        hw.notes.append("A laptop with two GPUs was detected: the faster one is selected for training.")
    return hw


def device_env(hw: Hardware) -> dict:
    """Environment that pins Spirula to the best GPU when the machine has several."""
    if len(hw.gpus) > 1 and hw.best_gpu():
        return {"SS_VK_DEVICE": hw.best_gpu().name}
    return {}
