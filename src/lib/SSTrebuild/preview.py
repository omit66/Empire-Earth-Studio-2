#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Side by side preview: a random sample of parts, once as original and once per Real-ESRGAN model,
so models (or factors) can be compared before a long run.
"""

import os
import random
import shutil
import struct
import subprocess
import tempfile
from typing import Callable, Optional

from . import dxt

TILE = 280  # edge length of one picture on the sheets


def _width(path: str) -> int:
    with open(path, "rb") as f:
        h = f.read(128)
    if path.lower().endswith(".tga"):
        return max(struct.unpack_from("<HH", h, 12))
    info = dxt.parseDDS(h)
    return max(info.width, info.height)


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{os.path.basename(cmd[0])} failed: {r.stderr.strip()[:300]}")


def pickSample(inDir: str, candidates: list[str], count: int, minSize: int, maxSize: int, seed: int) -> list[str]:
    """random parts of a useful size; at most one part per texture so the sample is varied"""
    usable = []
    for name in candidates:
        try:
            if minSize <= _width(os.path.join(inDir, name)) <= maxSize:
                usable.append(name)
        except (OSError, ValueError, struct.error):
            continue

    random.Random(seed).shuffle(usable)
    sample, seen = [], set()
    for name in usable:
        key = name.rsplit("_", 2)[0] if "_RES" in name else name
        if key in seen:
            continue
        seen.add(key)
        sample.append(name)
        if len(sample) == count:
            break
    return sample


def makePreview(inDir: str, outDir: str, names: list[str], models: list[str], factor: int, esrgan: str,
                magick: str = "magick", rowsPerSheet: int = 5, plain: bool = False,
                log: Optional[Callable[[str], None]] = None) -> list[str]:
    """writes compare_1.png, compare_2.png ... to outDir and returns their paths"""
    log = log or print
    os.makedirs(outDir, exist_ok=True)
    esrgan = os.path.abspath(esrgan)
    modelDir = os.path.join(os.path.dirname(esrgan), "models")

    with tempfile.TemporaryDirectory(prefix="preview_") as work:
        src = os.path.join(work, "src")
        os.makedirs(src)
        for i, name in enumerate(names):
            _run([magick, os.path.join(inDir, name), "-alpha", "off", os.path.join(src, f"{i}.png")])

        for model in models:
            log(f"model {model} ...")
            dst = os.path.join(work, model)
            os.makedirs(dst)
            r = subprocess.run([esrgan, "-i", src, "-o", dst, "-n", model, "-s", "4", "-m", modelDir, "-f", "png"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"Real-ESRGAN failed for model {model}: {r.stderr[-300:]}")

        rows = []
        for i, name in enumerate(names):
            width, height = (int(v) for v in subprocess.run(
                [magick, os.path.join(src, f"{i}.png"), "-format", "%w %h", "info:"],
                capture_output=True, text=True).stdout.split())
            size = f"{width * factor}x{height * factor}!"

            tiles = []
            columns = [("original", src, "Cubic")]
            if plain:  # plain resize without AI, the right thing for normal / bump maps
                columns.append(("plain resize (no AI)", src, "Lanczos"))
            columns += [(m, os.path.join(work, m), "Lanczos") for m in models]
            for label, folder, filt in columns:
                tile = os.path.join(work, f"t_{i}_{len(tiles)}.png")
                _run([magick, os.path.join(folder, f"{i}.png"), "-filter", filt, "-resize", size,
                      "-resize", f"{TILE}x{TILE}", "-background", "#333", "-gravity", "center", "-extent", f"{TILE}x{TILE}",
                      "-gravity", "SouthWest", "-fill", "yellow", "-undercolor", "#000000A0", "-pointsize", "14",
                      "-annotate", "+3+3", label, tile])
                tiles.append(tile)

            row = os.path.join(work, f"row_{i}.png")
            _run([magick] + tiles + ["+append", "-gravity", "NorthWest", "-fill", "white", "-undercolor", "#000000A0",
                                     "-pointsize", "14", "-annotate", "+3+3",
                                     f"{name}  ({width}px -> {width * factor}px)", row])
            rows.append(row)

        sheets = []
        for start in range(0, len(rows), rowsPerSheet):
            sheet = os.path.join(outDir, f"compare_{start // rowsPerSheet + 1}.png")
            _run([magick] + rows[start:start + rowsPerSheet] + ["-append", sheet])
            sheets.append(sheet)

        shutil.rmtree(work, ignore_errors=True)

    return sheets
