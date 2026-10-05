#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Upscales part files (TGA / DDS) with Real-ESRGAN (ncnn, Vulkan) and ImageMagick.

The AI always works at 4x. The result is scaled down to `factor` times the original size:
factor 2 = double edge length (textures of the 3D world), factor 1 = same size as the original
(user interface: cleaner edges, but the pixel size the game expects stays the same).

Output files have the same names as the input files. The alpha channel is NOT handled here
(Real-ESRGAN delivers none): run texfix afterwards, which also converts DXT5 back to DXT3.
Already existing output files are skipped, so an interrupted run can be continued.
"""

import concurrent.futures
import os
import shutil
import struct
import subprocess
import tempfile
from typing import Callable, Optional

from . import dxt

MIN_AI_SIZE = 16  # smaller images are only resized (or kept), the AI makes nothing useful of them


def _size(path: str) -> tuple[int, int, int, str]:
    """width, height, bits per pixel (TGA) and FourCC (DDS)"""
    with open(path, "rb") as f:
        h = f.read(128)
    if path.lower().endswith(".tga"):
        width, height = struct.unpack_from("<HH", h, 12)
        return width, height, h[16], ""
    info = dxt.parseDDS(h)
    return info.width, info.height, 0, info.fourcc


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:2])} failed: {r.stderr.strip()[:300]}")


def _encode(magick: str, source: str, dst: str, bpp: int, fourcc: str) -> None:
    if dst.lower().endswith(".tga"):
        kind = "TrueColorAlpha" if bpp == 32 else "TrueColor"
        _run([magick, source, "-type", kind, "-depth", "8", "-compress", "none", dst])
    else:
        comp = "dxt1" if fourcc == "DXT1" else "dxt5"  # DXT3 is made from DXT5 by texfix
        _run([magick, source, "-alpha", "off", "-define", f"dds:compression={comp}",
              "-define", "dds:mipmaps=0", "-define", "dds:cluster-fit=true", dst])


def upscaleParts(inDir: str, outDir: str, names: list[str], factor: int, model: str, esrgan: str,
                 magick: str = "magick", batch: int = 200, workers: int = 4,
                 log: Optional[Callable[[str], None]] = None, plain: bool = False) -> list[str]:
    """returns the names that failed. plain=True resizes with Lanczos only, no AI (right for normal / bump maps)"""
    log = log or print
    if factor not in (1, 2):
        raise ValueError("factor has to be 1 or 2")

    os.makedirs(outDir, exist_ok=True)
    esrgan = os.path.abspath(esrgan)
    modelDir = os.path.join(os.path.dirname(esrgan), "models")

    todo = [n for n in names if not os.path.isfile(os.path.join(outDir, n))]
    log(f"{len(todo)} of {len(names)} parts to do")
    failed: list[str] = []

    with tempfile.TemporaryDirectory(prefix="upscale_") as work, concurrent.futures.ThreadPoolExecutor(workers) as pool:
        for start in range(0, len(todo), batch):
            chunk = todo[start:start + batch]
            pngIn, pngUp = os.path.join(work, "in"), os.path.join(work, "up")
            for d in (pngIn, pngUp):
                shutil.rmtree(d, ignore_errors=True)
                os.makedirs(d)

            jobs: dict[str, tuple[str, int, str, int, int]] = {}  # name -> (id, bpp, fourcc, w, h)
            ai: list[str] = []

            def prepare(item):
                i, name = item
                src = os.path.join(inDir, name)
                try:
                    width, height, bpp, fourcc = _size(src)
                    if plain or max(width, height) < MIN_AI_SIZE:
                        if factor == 1 and not plain:  # keep the original part
                            shutil.copyfile(src, os.path.join(outDir, name))
                            return name, None
                        tmp = os.path.join(pngIn, f"{i}_small.png")
                        _run([magick, src, "-alpha", "off", "-filter", "Lanczos", "-resize",
                              f"{width * factor}x{height * factor}!", tmp])
                        _encode(magick, tmp, os.path.join(outDir, name), bpp, fourcc)
                        return name, None
                    _run([magick, src, "-alpha", "off", os.path.join(pngIn, f"{i}.png")])
                    return name, (str(i), bpp, fourcc, width, height)
                except (RuntimeError, ValueError, OSError) as e:
                    log(f"  FAILED {name}: {e}")
                    return name, "failed"

            for name, result in pool.map(prepare, enumerate(chunk, start)):
                if result == "failed":
                    failed.append(name)
                elif result is not None:
                    jobs[name] = result
                    ai.append(name)

            if jobs:
                keep = {f"{j[0]}.png" for j in jobs.values()}
                for f in os.listdir(pngIn):  # the small images are already finished
                    if f not in keep:
                        os.remove(os.path.join(pngIn, f))
                r = subprocess.run([esrgan, "-i", pngIn, "-o", pngUp, "-n", model, "-s", "4", "-m", modelDir, "-f", "png"],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    raise RuntimeError(f"Real-ESRGAN failed (exit code {r.returncode}): {r.stderr[-300:]}")

            def finish(name: str):
                i, bpp, fourcc, width, height = jobs[name]
                up = os.path.join(pngUp, f"{i}.png")
                try:
                    if not os.path.isfile(up):
                        raise RuntimeError("no result from Real-ESRGAN")
                    small = os.path.join(pngUp, f"{i}_s.png")
                    _run([magick, up, "-filter", "Lanczos", "-resize", f"{width * factor}x{height * factor}!", small])
                    tmp = os.path.join(outDir, "~" + name)
                    _encode(magick, small, tmp, bpp, fourcc)
                    os.replace(tmp, os.path.join(outDir, name))
                    return None
                except (RuntimeError, OSError) as e:
                    log(f"  FAILED {name}: {e}")
                    return name

            failed += [n for n in pool.map(finish, ai) if n]
            log(f"  {min(start + batch, len(todo))}/{len(todo)}")

    return failed
