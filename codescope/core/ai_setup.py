"""Set up a local AI server for the assistant: llama.cpp's llama-server running a GGUF model.

Reads the model's GGUF header, detects the GPU and memory, picks matching llama.cpp release
downloads (Windows), plans how much of the model goes to the GPU and writes a start script.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import urllib.request
import zipfile

from .assistant import config_dir

RELEASE_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
GIB = 1024 ** 3
HEADROOM_GIB = 2.5          # VRAM kept free for the KV cache, compute buffers and the desktop


# ---- GGUF -----------------------------------------------------------------------------------------

_SCALARS = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}


class _Reader:
    def __init__(self, fh):
        self.fh = fh

    def unpack(self, fmt):
        size = struct.calcsize("<" + fmt)
        data = self.fh.read(size)
        if len(data) != size:
            raise ValueError("unexpected end of file")
        return struct.unpack("<" + fmt, data)[0]

    def string(self):
        n = self.unpack("Q")
        if n > 1 << 24:
            raise ValueError("implausible string length")
        return self.fh.read(n).decode("utf-8", "replace")

    def value(self, vtype, keep=True):
        if vtype in _SCALARS:
            return self.unpack(_SCALARS[vtype])
        if vtype == 8:
            return self.string()
        if vtype == 9:
            itype, count = self.unpack("I"), self.unpack("Q")
            if itype in _SCALARS and not keep:
                self.fh.seek(struct.calcsize("<" + _SCALARS[itype]) * count, 1)
                return None
            items = [self.value(itype, keep) for _ in range(count)]
            return items if keep else None
        raise ValueError(f"unknown GGUF value type {vtype}")


def gguf_info(path):
    """Model facts from a GGUF file header: architecture, layers, experts, context length, size."""
    info = {"path": path, "size": os.path.getsize(path), "architecture": "", "name": "", "block_count": 0,
            "expert_count": 0, "expert_used_count": 0, "context_length": 0}
    with open(path, "rb") as fh:
        if fh.read(4) != b"GGUF":
            raise ValueError(f"{path} is not a GGUF model file")
        r = _Reader(fh)
        version = r.unpack("I")
        if version < 2:
            raise ValueError(f"GGUF version {version} is too old")
        r.unpack("Q")                     # tensor count
        kv_count = r.unpack("Q")
        meta = {}
        for _ in range(kv_count):
            key = r.string()
            vtype = r.unpack("I")
            # big arrays (the tokenizer vocabulary) are skipped, not kept
            meta[key] = r.value(vtype, keep=vtype != 9)
    arch = meta.get("general.architecture", "")
    info["architecture"] = arch
    info["name"] = meta.get("general.name", "") or os.path.splitext(os.path.basename(path))[0]
    for key in ("block_count", "expert_count", "expert_used_count", "context_length"):
        v = meta.get(f"{arch}.{key}")
        if isinstance(v, int):
            info[key] = v
    return info


# ---- hardware -------------------------------------------------------------------------------------

def _run(cmd, timeout=15):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def total_ram():
    if os.name == "nt":
        import ctypes

        class MemStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MemStatus()
        st.dwLength = ctypes.sizeof(MemStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return st.ullTotalPhys
        return 0
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 0


def detect_hardware():
    """{"gpus": [{"name", "vram", "vendor"}], "cuda": "12.8" or "", "ram": bytes}."""
    hw = {"gpus": [], "cuda": "", "ram": total_ram()}
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[1].isdigit():
            hw["gpus"].append({"name": parts[0], "vram": int(parts[1]) * 1024 ** 2, "vendor": "nvidia"})
    if hw["gpus"]:
        m = re.search(r"CUDA Version:\s*([\d.]+)", _run(["nvidia-smi"]))
        hw["cuda"] = m.group(1) if m else ""
    elif os.name == "nt":
        out = _run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }"])
        for name in out.strip().splitlines():
            name = name.strip()
            low = name.lower()
            vendor = "amd" if ("amd" in low or "radeon" in low) else "intel" if "intel" in low else ""
            if vendor and "basic" not in low:
                hw["gpus"].append({"name": name, "vram": 0, "vendor": vendor})
    return hw


def choose_backend(hw):
    if any(g["vendor"] == "nvidia" for g in hw["gpus"]):
        return "cuda"
    if hw["gpus"]:
        return "vulkan"
    return "cpu"


# ---- llama.cpp release --------------------------------------------------------------------------

def _ver(text):
    return tuple(int(x) for x in re.findall(r"\d+", text))


def pick_assets(release, backend, cuda_version=""):
    """[(name, url)] to download from a llama.cpp GitHub release for Windows x64."""
    assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
    if backend == "cuda":
        builds = {}
        for name in assets:
            m = re.match(r"^llama-.*-bin-win-cuda-(?:cu)?(\d+(?:\.\d+)*)-x64\.zip$", name)
            if m:
                builds[m.group(1)] = name
        if not builds:
            return []
        usable = [v for v in builds if not cuda_version or _ver(v)[:2] <= _ver(cuda_version)[:2]]
        best = max(usable or builds, key=_ver)
        out = [(builds[best], assets[builds[best]])]
        for name in assets:
            m = re.match(r"^cudart-llama-bin-win-(?:cuda-)?(?:cu)?(\d+(?:\.\d+)*)-x64\.zip$", name)
            if m and m.group(1) == best:
                out.append((name, assets[name]))
        return out
    pattern = {"vulkan": r"^llama-.*-bin-win-vulkan-x64\.zip$",
               "cpu": r"^llama-.*-bin-win-(?:cpu|avx2)-x64\.zip$"}[backend]
    hits = sorted(n for n in assets if re.match(pattern, n))
    return [(hits[0], assets[hits[0]])] if hits else []


def fetch_release():
    req = urllib.request.Request(RELEASE_API, headers={"User-Agent": "CodeScope", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def download(url, dest, progress=None):
    req = urllib.request.Request(url, headers={"User-Agent": "CodeScope"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest + ".part", "wb") as out:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    os.replace(dest + ".part", dest)


def server_name():
    return "llama-server.exe" if os.name == "nt" else "llama-server"


def find_server(root):
    for base, _dirs, files in os.walk(root):
        if server_name() in files:
            return os.path.join(base, server_name())
    return None


def install_llama_cpp(backend, cuda_version="", dest=None, log=print, progress=None):
    """Download and unpack the latest llama.cpp Windows build; returns the llama-server path."""
    dest = dest or os.path.join(config_dir(), "llama.cpp")
    log("Looking up the latest llama.cpp release on GitHub …")
    release = fetch_release()
    tag = release.get("tag_name", "latest")
    assets = pick_assets(release, backend, cuda_version)
    if not assets:
        names = ", ".join(a["name"] for a in release.get("assets", []) if a["name"].endswith(".zip"))
        raise RuntimeError(f"No Windows {backend} build in llama.cpp {tag}. Available: {names}")
    target = os.path.join(dest, f"{tag}-{backend}")
    existing = find_server(target) if os.path.isdir(target) else None
    if existing:
        log(f"llama.cpp {tag} ({backend}) is already installed in {target}")
        return existing
    os.makedirs(target, exist_ok=True)
    for name, url in assets:
        zpath = os.path.join(dest, name)
        log(f"Downloading {name} …")
        download(url, zpath, progress)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(target)
        os.remove(zpath)
    exe = find_server(target)
    if exe is None:
        raise RuntimeError(f"{server_name()} not found in the downloaded files ({target})")
    # the CUDA runtime DLLs must sit next to the server
    exe_dir = os.path.dirname(exe)
    for base, _dirs, files in os.walk(target):
        if base != exe_dir:
            for f in files:
                if f.lower().endswith(".dll") and not os.path.exists(os.path.join(exe_dir, f)):
                    shutil.copy2(os.path.join(base, f), exe_dir)
    log(f"Installed llama.cpp {tag} ({backend}) in {target}")
    return exe


# ---- plan and start script ----------------------------------------------------------------------

def plan(info, hw, backend, context=32768):
    """llama-server arguments for this model and machine, with an explanation."""
    size_g = info["size"] / GIB
    vram_g = max((g["vram"] for g in hw["gpus"]), default=0) / GIB
    ram_g = hw["ram"] / GIB
    layers = info["block_count"] or 0
    moe = info["expert_count"] > 0
    notes = [f"Model: {info['name']} ({info['architecture'] or 'unknown'}), {size_g:.1f} GiB"
             + (f", {layers} layers" if layers else "")
             + (f", mixture of experts ({info['expert_used_count']} of {info['expert_count']} active)" if moe else "")]
    if info["context_length"]:
        context = min(context, info["context_length"])
    args = ["--jinja", "-c", str(context)]
    if backend == "cpu" or vram_g <= 0:
        args += ["-ngl", "0"]
        notes.append("Running on the CPU only." + (" No GPU memory size known; set -ngl yourself to offload layers."
                                                   if backend != "cpu" else ""))
    elif vram_g >= size_g + HEADROOM_GIB:
        args += ["-ngl", "99"]
        notes.append(f"The whole model fits in the GPU's {vram_g:.0f} GiB: everything runs on the GPU.")
    elif moe and layers:
        # attention and shared weights are small; the experts make up most of the file
        expert_share = 0.9
        per_layer = size_g * expert_share / layers
        free = vram_g - HEADROOM_GIB - size_g * (1 - expert_share)
        on_gpu = max(0, min(layers, int(free / per_layer))) if per_layer > 0 else 0
        cpu_layers = layers - on_gpu
        args += ["-ngl", "99", "--n-cpu-moe", str(cpu_layers)]
        notes.append(f"The model ({size_g:.1f} GiB) is larger than the GPU's {vram_g:.0f} GiB. All layers run on "
                     f"the GPU, but the expert weights of {cpu_layers} of {layers} layers stay in RAM "
                     f"(--n-cpu-moe {cpu_layers}). Lower that number while the GPU still has free memory "
                     f"for more speed, raise it if the server runs out of GPU memory.")
    else:
        per_layer = size_g / layers if layers else size_g
        ngl = max(0, int((vram_g - HEADROOM_GIB) / per_layer)) if layers else 0
        args += ["-ngl", str(ngl)]
        notes.append(f"{ngl} of {layers or '?'} layers go to the GPU; the rest runs on the CPU.")
    total_g = ram_g + vram_g
    if ram_g and size_g + 4 > total_g:
        notes.append(f"Warning: the model needs about {size_g + 4:.0f} GiB but this PC has {ram_g:.0f} GiB RAM"
                     + (f" + {vram_g:.0f} GiB VRAM" if vram_g else "") + ". It may not load; try a smaller quant.")
    notes.append(f"Context window: {context} tokens.")
    return args, notes


def write_start_script(server, model, args, port=8080, path=None):
    """A .bat (Windows) or .sh script that starts llama-server for the assistant."""
    windows = os.name == "nt"
    path = path or os.path.join(config_dir(), "start-ai-server.bat" if windows else "start-ai-server.sh")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    full = ["-m", model] + list(args) + ["--host", "127.0.0.1", "--port", str(port)]

    def q(a):
        return f'"{a}"' if (" " in a or "\\" in a or "/" in a) else a
    if windows:
        text = ("@echo off\r\n"
                "rem Starts the local AI server used by CodeScope's AI Assistant. Keep this window open.\r\n"
                "rem Written by CodeScope; edit the options below as you like.\r\n"
                f"title CodeScope AI server ({os.path.basename(model)})\r\n"
                f'cd /d "{os.path.dirname(server)}"\r\n'
                f"{q(server)} {' '.join(q(a) for a in full)}\r\n"
                "echo.\r\necho The AI server has stopped.\r\npause\r\n")
    else:
        text = ("#!/bin/sh\n# Starts the local AI server used by CodeScope's AI Assistant.\n"
                f"exec {q(server)} {' '.join(q(a) for a in full)}\n")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    if not windows:
        os.chmod(path, 0o755)
    return path


def start_server(script):
    """Run the start script in its own console window."""
    if os.name == "nt":
        return subprocess.Popen(["cmd", "/c", script], creationflags=subprocess.CREATE_NEW_CONSOLE)
    return subprocess.Popen(["sh", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)


def describe_hardware(hw):
    gpus = ", ".join(f"{g['name']}" + (f" ({g['vram'] / GIB:.0f} GiB)" if g["vram"] else "") for g in hw["gpus"])
    return (f"GPU: {gpus or 'none detected'}" + (f", CUDA driver {hw['cuda']}" if hw["cuda"] else "")
            + f"; RAM: {hw['ram'] / GIB:.0f} GiB")


def platform_supported():
    return sys.platform.startswith("win")
