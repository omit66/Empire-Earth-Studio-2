#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Repairs upscaled part files (TGA / DDS) before they go into an SST:

 - Real-ESRGAN (ncnn) delivers images without alpha channel for many inputs. Where the original
   had transparency, the alpha channel of the original is scaled and put back.
 - ImageMagick can only write DXT1 and DXT5. The game picks the surface format from the SST
   (format code in the header), so DXT3 images have to be DXT3 again. DXT5 -> DXT3 only
   needs the alpha block to be converted, the colour block is the same.

Repaired files are written to a separate folder, the upscaled originals are never touched.
"""

import concurrent.futures
import os
import struct
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

from . import dxt


@dataclass
class FixAction:
    name: str
    action: str    # "keep", "merge-alpha", "convert-dxt3", "problem"
    detail: str = ""


def _tgaHeader(path: str) -> tuple[int, int, int, int, int]:
    """type, width, height, bpp, descriptor"""
    with open(path, "rb") as f:
        h = f.read(18)
    if len(h) < 18:
        raise ValueError("file too short")
    width, height = struct.unpack_from("<HH", h, 12)
    return h[2], width, height, h[16], h[17]


def _tgaHasAlpha(path: str) -> bool:
    with open(path, "rb") as f:
        blob = f.read()
    _, width, height, bpp, _ = _tgaHeader(path)
    pixels = blob[18 + blob[0]:18 + blob[0] + width * height * 4]
    alpha = pixels[3::4]
    return alpha.count(0xFF) != len(alpha)


def plan(name: str, inPath: str, outPath: str, factor: int = 2) -> FixAction:
    """decides what has to happen with one upscaled part"""
    ext = os.path.splitext(name)[1].lower()

    try:
        if ext == ".tga":
            _, iw, ih, ibpp, _ = _tgaHeader(inPath)
            otype, ow, oh, obpp, _ = _tgaHeader(outPath)
            if otype != 2 or obpp != ibpp or (ow, oh) != (iw * factor, ih * factor):
                return FixAction(name, "problem", f"original {iw}x{ih}x{ibpp}, upscaled {ow}x{oh}x{obpp} type {otype}")
            if ibpp == 32 and _tgaHasAlpha(inPath) and not _tgaHasAlpha(outPath):
                return FixAction(name, "merge-alpha")
            return FixAction(name, "keep")

        with open(inPath, "rb") as f:
            orig = dxt.parseDDS(f.read())
        with open(outPath, "rb") as f:
            new = dxt.parseDDS(f.read())
    except (ValueError, OSError) as e:
        return FixAction(name, "problem", str(e))

    if (new.width, new.height) != (orig.width * factor, orig.height * factor):
        return FixAction(name, "problem", f"original {orig.width}x{orig.height}, upscaled {new.width}x{new.height}")

    try:
        alphaIn = dxt.hasAlpha(orig.fourcc, orig.data, orig.width, orig.height)
        alphaOut = dxt.hasAlpha(new.fourcc, new.data[:dxt.dataSize(new.width, new.height, new.fourcc)], new.width, new.height)
    except ValueError as e:
        return FixAction(name, "problem", str(e))

    alphaOk = alphaOut or not alphaIn

    if orig.fourcc == "DXT3":
        if new.fourcc == "DXT5" and alphaOk:
            return FixAction(name, "convert-dxt3")
        return FixAction(name, "merge-alpha", "DXT3")
    if new.fourcc == orig.fourcc and alphaOk:
        return FixAction(name, "keep")
    return FixAction(name, "merge-alpha", orig.fourcc)


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed: {r.stderr.strip()[:300]}")


def _alphaMask(inPath: str, width: int, height: int, fmt: str) -> list[str]:
    mask = [inPath, "-alpha", "extract", "-filter", "Lanczos", "-resize", f"{width}x{height}!"]
    if fmt == "DXT1":
        mask += ["-threshold", "50%"]  # DXT1 only knows transparent / opaque
    return mask


def _mergeAlpha(magick: str, inPath: str, outPath: str, dst: str, width: int, height: int, fmt: str) -> None:
    """RGB of the upscaled image + alpha of the original (scaled up) -> TGA or DXT1/DXT5"""
    merge = [magick, outPath, "("] + _alphaMask(inPath, width, height, fmt) +             [")", "-alpha", "off", "-compose", "CopyOpacity", "-composite"]

    if dst.lower().endswith(".tga"):
        _run(merge + ["-depth", "8", "-compress", "none", dst])
        return

    ddsArgs = ["-define", "dds:mipmaps=0", "-define", "dds:cluster-fit=true"]

    if fmt == "DXT1":
        # ImageMagick writes DXT1 without transparency: let it encode the colours, put the alpha in ourselves
        r = subprocess.run(merge + ["-depth", "8", "rgba:-"], capture_output=True)
        if r.returncode != 0:
            raise RuntimeError(f"magick failed: {r.stderr.decode(errors='replace').strip()[:300]}")
        rgba = r.stdout

        _run([magick, outPath, "-alpha", "off", "-define", "dds:compression=dxt1"] + ddsArgs + [dst])
        with open(dst, "rb") as f:
            base = dxt.parseDDS(f.read())

        data = dxt.dxt1WithAlpha(rgba, width, height, base.data)
        with open(dst, "wb") as f:
            f.write(base.header + data)
        return

    _run(merge + ["-define", "dds:compression=dxt5"] + ddsArgs + [dst])  # DXT3 is made from DXT5


def _writeDxt3(dxt5Blob: bytes, dst: str) -> None:
    info = dxt.parseDDS(dxt5Blob)
    if info.fourcc != "DXT5":
        raise ValueError(f"expected DXT5, got {info.fourcc}")

    size = dxt.dataSize(info.width, info.height, "DXT5")
    blob = dxt.withFourCC(info.header + dxt.dxt5ToDxt3(info.data[:size]), "DXT3")
    with open(dst, "wb") as f:
        f.write(blob)


def apply(action: FixAction, inPath: str, outPath: str, dstPath: str, magick: str) -> None:
    """writes the repaired part to dstPath (only for actions that change something)"""
    if action.action == "merge-alpha":
        with open(outPath, "rb") as f:
            head = f.read(24)
        if outPath.lower().endswith(".tga"):
            width, height = struct.unpack_from("<HH", head, 12)
        else:
            height, width = struct.unpack_from("<II", head, 12)

        if action.detail == "DXT3":  # let ImageMagick write DXT5 first
            tmp = dstPath + ".tmp.dds"
            _mergeAlpha(magick, inPath, outPath, tmp, width, height, "DXT5")
            with open(tmp, "rb") as f:
                blob = f.read()
            os.remove(tmp)
            _writeDxt3(blob, dstPath)
        else:
            _mergeAlpha(magick, inPath, outPath, dstPath, width, height, action.detail)

    elif action.action == "convert-dxt3":
        with open(outPath, "rb") as f:
            _writeDxt3(f.read(), dstPath)

    else:
        raise ValueError(action.action)


def fixAll(inDir: str, outDir: str, fixedDir: str, magick: str = "magick", workers: int = 4,
           log: Optional[Callable[[str], None]] = None, dryRun: bool = False, factor: int = 2) -> list[FixAction]:
    """runs plan() and apply() for all parts that exist in inDir and outDir"""
    log = log or print
    os.makedirs(fixedDir, exist_ok=True)

    names = sorted(n for n in os.listdir(outDir)
                   if n.lower().endswith((".tga", ".dds")) and os.path.isfile(os.path.join(inDir, n)))

    actions: list[FixAction] = []

    def work(name: str) -> FixAction:
        inPath, outPath = os.path.join(inDir, name), os.path.join(outDir, name)
        dst = os.path.join(fixedDir, name)

        act = plan(name, inPath, outPath, factor)
        if act.action in ("merge-alpha", "convert-dxt3") and not dryRun and not os.path.isfile(dst):
            try:
                tmp = os.path.join(fixedDir, "~" + name)  # keeps the extension, so ImageMagick knows the format
                apply(act, inPath, outPath, tmp, magick)
                os.replace(tmp, dst)
            except (RuntimeError, ValueError, OSError) as e:
                return FixAction(name, "problem", f"repair failed: {e}")
        return act

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for act in pool.map(work, names):
            actions.append(act)
            if len(actions) % 1000 == 0:
                log(f"  {len(actions)}/{len(names)}")

    return actions
